"""Orchestrator: pure Python state machine, drives agent turns and persists everything.

Implemented as a generator that `yield`s events. The CLI consumes those events
to render the conversation in real time without coupling itself to the
orchestrator internals.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import config, db
from . import plan_writer as _plan_writer
from .config import (
    LLM_CALL_TIMEOUT_SECONDS,
    MAX_DELEGATIONS,
    MAX_RECURSION_DEPTH,
    MAX_STEP_BONUS,
    MAX_STEPS_PER_REQUEST,
    REQUEST_WALL_CLOCK_SECONDS,
    SOFT_DEADLINE_RATIO,
    TURN_WALL_CLOCK_SECONDS,
    WRITE_STEP_BONUS,
    UserProfile,
    ensure_dirs,
)
from .llm import LLMClient, LLMTimeoutError, _looks_corrupted
from .models import LLMResponse
from .persistence import (
    append_to_journal,
    conversation_folder_name,
    write_artifact,
)
from .prompts import (
    PromptContext,
    render_plan_recap,
    render_system_prompt,
    tools_payload_for_agent,
)
from .tools import build_registry
from .tools._errors import CRITICAL_ERROR_CODES, tool_error
from .tools._workspace import quota_remaining, workspace_root_for
from .tools.conv_status import budget_snapshot as _budget_snapshot

# Tools the router LOSES once it has delegated at least once in this
# conversation (i.e. plan.md exists). Router must orchestrate from that point
# on, not execute data-gathering itself — otherwise the fetched data is
# trapped in its private context, unreachable to delegated children.
_ROUTER_DEEP_RESEARCH_FORBIDDEN_TOOLS: frozenset[str] = frozenset({
    "web_search",
    "wikipedia_search",
    "wikipedia_fetch",
    "wikipedia_summary",
})

# Modes where the task-class gate and todo-planning gate are enforced.
# In vocal mode the interaction is conversational; gates would add friction.
_PLANNING_MODES: frozenset[str] = frozenset({"analyse", "chat"})

# ---- Events emitted to the CLI -------------------------------------------

@dataclass
class ConversationStarted:
    conversation_id: str
    folder_path: str
    user_language: str


@dataclass
class AgentStarted:
    agent_code: str
    request_id: str
    depth: int


@dataclass
class ThoughtCaptured:
    agent_code: str
    text: str


@dataclass
class ToolCallEmitted:
    agent_code: str
    tool_name: str
    arguments: dict


@dataclass
class ToolResponseRecorded:
    agent_code: str
    tool_name: str
    response: str


@dataclass
class DelegationStarted:
    parent_agent: str
    child_agent: str
    briefing: str


@dataclass
class HumanQuestionAsked:
    agent_code: str
    question: str
    why: str


@dataclass
class HumanAnswerReceived:
    answer: str


@dataclass
class RecursionLimitReached:
    agent_code: str
    depth: int


@dataclass
class FinalAnswer:
    text: str


@dataclass
class OrchestrationFailed:
    reason: str


@dataclass
class TurnStarted:
    turn_index: int


@dataclass
class SummaryUpdated:
    path: str


@dataclass
class WallClockExceeded:
    scope: str         # "llm_call" | "request" | "turn"
    agent_code: str
    elapsed_seconds: float


@dataclass
class SoftDeadlineReached:
    """Emitted when the orchestrator forces a graceful wrap-up.

    The agent is then restricted to its conclusion tool only
    (report_findings for specialists, return_to_user for router/finalizer)
    and asked to produce a partial result with what it already has.
    """
    scope: str         # "request" | "turn"
    agent_code: str
    elapsed_seconds: float


@dataclass
class DuplicateCallBlocked:
    agent_code: str
    tool_name: str
    fingerprint: str


@dataclass
class ForcedConvergence:
    agent_code: str
    reason: str


@dataclass
class CorruptedOutputDetected:
    agent_code: str


@dataclass
class FilesystemErrorObserved:
    agent_code: str
    tool_name: str
    error_code: str
    message: str


@dataclass
class QuotaWarning:
    remaining_bytes: int
    total_bytes: int


@dataclass
class ReportFindingsReceived:
    agent_code: str
    confidence: str
    files_produced: list[str]
    sub_questions_count: int


@dataclass(frozen=True)
class SignalConvergenceRedirected:
    agent_code: str


@dataclass(frozen=True)
class TodoListUpdated:
    agent: str
    scope: str           # "conversation" | "request"
    request_id: str | None
    todos: tuple         # snapshot complet (tuple pour frozen)
    stats: dict


# Roles that may NOT call return_to_user (must use report_findings instead).
_SPECIALIST_ROLES = frozenset({"specialist"})


def _format_report_for_parent(agent_code: str, payload: dict) -> str:
    """Render a report_findings payload as the markdown the parent agent will see."""
    confidence = payload.get("confidence", "?")
    summary = payload.get("summary", "")
    files = payload.get("files_produced") or []
    sub_qs = payload.get("sub_questions") or []
    blockers = payload.get("blockers") or []

    lines = [
        f"## Report from {agent_code} (confidence: {confidence})",
        "",
        "### Summary",
        summary,
        "",
        "### Files produced",
    ]
    if files:
        lines += [f"- {f}" for f in files]
    else:
        lines.append("None.")
    lines.append("")

    lines.append(f"### Sub-questions ({len(sub_qs)})")
    if sub_qs:
        for i, sq in enumerate(sub_qs, start=1):
            q = sq.get("question", "")
            why = sq.get("why", "")
            agent = sq.get("suggested_agent", "")
            parts = [f"{i}. {q}"]
            if why:
                parts.append(f"   Why: {why}")
            if agent:
                parts.append(f"   Suggested agent: {agent}")
            lines += parts
    else:
        lines.append("None.")
    lines.append("")

    lines.append("### Blockers")
    if blockers:
        lines += [f"- {b}" for b in blockers]
    else:
        lines.append("None.")

    return "\n".join(lines)


def _detect_language(text: str) -> str:
    try:
        from langdetect import detect
        return detect(text)
    except Exception:
        return "und"


def _new_uuid() -> str:
    return uuid.uuid4().hex[:12]


# ---- Fingerprint helpers (loop / duplicate detection) --------------------

_WS_RE = re.compile(r"\s+")

# Read-only, idempotent tools. Duplicate calls to these are blocked (so we
# don't waste budget re-reading the same file), BUT they do not count toward
# the 3-consecutive-duplicates force-stop. Re-reading a file is a legitimate
# uncertainty-resolution attempt, not a runaway loop.
_IDEMPOTENT_READ_TOOLS: frozenset[str] = frozenset({
    "conv_read_file",
    "conv_list",
    "conv_history_scan",
    "workspace_view",
    "workspace_list",
    "self_inspect_agent",
    "self_inspect_paradigm",
})


def _normalise_value(v: object) -> object:
    if isinstance(v, str):
        return _WS_RE.sub(" ", v.strip().lower())
    if isinstance(v, list):
        return [_normalise_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _normalise_value(x) for k, x in v.items()}
    return v


def _spec_defaults(spec) -> dict:
    props = spec.parameters.get("properties", {})
    return {k: v["default"] for k, v in props.items() if "default" in v}


# For read-only tools, the fingerprint is built ONLY from the identifying
# argument (the file path). This collapses semantically-equivalent calls that
# differ only in display parameters like view_range, max_bytes, etc. — which
# the LLM otherwise uses (sometimes accidentally) to bypass the duplicate
# detector while asking for the same data.
_READ_TOOL_IDENTITY_ARGS: dict[str, tuple[str, ...]] = {
    "workspace_view": ("relative_path",),
    "workspace_list": ("sub_path",),
    "conv_read_file": ("relative_path",),
}


def _fingerprint(tool_name: str, args: dict, defaults: dict) -> str:
    if tool_name in _READ_TOOL_IDENTITY_ARGS:
        keys = _READ_TOOL_IDENTITY_ARGS[tool_name]
        norm = {k: _normalise_value((args or {}).get(k, "")) for k in keys}
    else:
        merged = {**defaults, **(args or {})}
        norm = {k: _normalise_value(v) for k, v in merged.items()}
    return f"{tool_name}:{json.dumps(norm, sort_keys=True, ensure_ascii=False)}"


def _extract_files_from_report(markdown: str) -> list[str]:
    """Extract a files_produced list from a formatted report or partial report.

    Both _format_report_for_parent and _build_partial_report emit a section
    titled either '### Files produced' or '### Files written to workspace before
    abort', followed by '- <path>' bullets until the next heading.
    """
    if not markdown:
        return []
    files: list[str] = []
    in_section = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if (
            stripped.startswith("### Files produced")
            or stripped.startswith("### Files written to workspace")
            or stripped.startswith("### Fichiers produits")
            or stripped.startswith("### Fichiers écrits dans le workspace")
        ):
            in_section = True
            continue
        if in_section:
            if stripped.startswith("###") or stripped.startswith("## "):
                break
            if stripped.startswith("- "):
                candidate = stripped[2:].strip()
                if candidate and candidate.lower() not in {"none.", "none", "—", "aucun.", "aucun"}:
                    files.append(candidate)
    return files


def _build_partial_report(conv_folder, req_id: str, agent_code: str,
                          status: str, error: str,
                          recent_tool_calls: list[dict],
                          lang: str = "en") -> tuple[str, str]:
    """Build a partial findings report when a child crashes (loop/budget/wall-clock).

    Instead of returning a bare JSON error to the parent, surface:
      - the workspace files the child managed to write
      - the last few tool calls attempted
    so the parent can salvage work and decide where to redirect.

    Localised in French when ``lang == "fr"`` so the human reading the
    aborted report sees it in the language of their request.

    Returns (markdown_for_parent, json_payload_for_artifact).
    """
    # 1. Workspace files (relative paths) at this point in time.
    workspace_files: list[str] = []
    try:
        ws_root = workspace_root_for(conv_folder)
        if ws_root.exists():
            for p in sorted(ws_root.rglob("*")):
                if p.is_file():
                    workspace_files.append(str(p.relative_to(ws_root)))
    except OSError:
        pass

    # 2. Recent tool calls (already collected in-process).
    tool_snippets = []
    for tc in recent_tool_calls[-8:]:
        name = tc.get("name", "?")
        args = tc.get("arguments") or {}
        if isinstance(args, dict):
            # One-line argument preview
            args_preview = ", ".join(
                f"{k}={(repr(v)[:60] + '…') if len(repr(v)) > 60 else repr(v)}"
                for k, v in list(args.items())[:4]
            )
        else:
            args_preview = repr(args)[:120]
        tool_snippets.append(f"{name}({args_preview})")

    payload = {
        "status": status,
        "agent": agent_code,
        "error": error,
        "files_produced": workspace_files,
        "recent_tool_calls": tool_snippets,
    }

    fr = lang == "fr"
    if fr:
        labels = {
            "title": f"## Rapport interrompu de {agent_code}",
            "status": "**Statut :**",
            "reason": "**Raison :**",
            "files_header": "### Fichiers écrits dans le workspace avant l'interruption",
            "files_hint": (
                "Ces fichiers contiennent ce que l'agent a réussi à persister. "
                "Lis-les via workspace_view ou passe-les en support_files à une "
                "autre délégation pour continuer à partir d'ici — ne redémarre "
                "PAS de zéro."
            ),
            "files_none": "Aucun. L'agent n'a rien persisté dans le workspace.",
            "tools_header": "### Derniers appels d'outils tentés",
            "tools_none": "Aucun.",
            "next_header": "### Prochaine action recommandée",
            "next_body": (
                "- Si files_produced n'est pas vide : synthétise à partir de "
                "ces fichiers ou délègue un follow-up plus ciblé avec ces "
                "fichiers en support_files.\n"
                "- Si files_produced est vide : l'angle précédent n'a pas "
                "fonctionné ; change d'angle ou délègue à un autre agent.\n"
                "- Ne re-délègue PAS le même briefing — il échouera pareil."
            ),
        }
    else:
        labels = {
            "title": f"## Aborted report from {agent_code}",
            "status": "**Status:**",
            "reason": "**Reason:**",
            "files_header": "### Files written to workspace before abort",
            "files_hint": (
                "These files contain whatever the agent managed to persist. "
                "Read them via workspace_view or pass them as support_files to "
                "another delegation to continue from this point — do NOT "
                "restart from scratch."
            ),
            "files_none": "None. The agent did not persist anything to workspace.",
            "tools_header": "### Last tool calls attempted",
            "tools_none": "None.",
            "next_header": "### Recommended next action",
            "next_body": (
                "- If files_produced has content: synthesize from those files "
                "or delegate a narrower follow-up with these files as "
                "support_files.\n"
                "- If files_produced is empty: the previous angle did not "
                "work; either change angle or delegate to a different agent.\n"
                "- Do NOT re-delegate the same briefing — it will fail the "
                "same way."
            ),
        }

    md_lines = [
        labels["title"],
        "",
        f"{labels['status']} {status}",
        f"{labels['reason']} {error}",
        "",
        labels["files_header"],
    ]
    if workspace_files:
        md_lines += [f"- {f}" for f in workspace_files]
        md_lines.append("")
        md_lines.append(labels["files_hint"])
    else:
        md_lines.append(labels["files_none"])
    md_lines.append("")

    md_lines.append(f"{labels['tools_header']} ({len(tool_snippets)})")
    if tool_snippets:
        md_lines += [f"- {s}" for s in tool_snippets]
    else:
        md_lines.append(labels["tools_none"])
    md_lines.append("")

    md_lines.append(labels["next_header"])
    md_lines.append(labels["next_body"])

    return "\n".join(md_lines), json.dumps(payload, ensure_ascii=False)


# ---- Orchestrator ---------------------------------------------------------

class Orchestrator:
    """Owns one conversation. The CLI invokes `run(user_input)` once per
    human turn; the orchestrator persists state in DB + filesystem and yields
    events as it progresses.
    """

    def __init__(self, llm: LLMClient, profile: UserProfile,
                 mode: str = "analyse",
                 conv_id: str | None = None,
                 ask_human_callback=None) -> None:
        assert mode in config.MODES, f"Unknown mode: {mode!r}"
        ensure_dirs()
        self.llm = llm
        self.profile = profile
        self.mode = mode
        self.conv_id = conv_id or _new_uuid()
        self.ask_human_callback = ask_human_callback
        self.conv_folder: Path | None = None
        self.user_language: str = "und"
        self.turn_index: int = -1
        self._turn_exchanges: list[tuple[str, str]] = []
        self._turn_started_at: float = 0.0
        # Plan state — deterministic side-effect of delegate_to + report_findings.
        # Reset at the start of every turn.
        self._plan_steps: list[dict] = []
        self._plan_counters: dict[int, int] = {}
        self._total_delegations: int = 0

    # ---- Public API ------------------------------------------------------

    def bootstrap_conversation(self) -> None:
        """Create the conversation folder and DB row. Idempotent.

        Called once at CLI startup, before any user input. After this call,
        self.conv_folder is set and the conversation row exists in DB with
        status='active'.
        """
        if self.conv_folder is not None:
            return  # already bootstrapped
        started = datetime.now(UTC)
        folder_name = conversation_folder_name(self.conv_id, started)
        self.conv_folder = config.CONVERSATIONS_DIR / folder_name
        self.conv_folder.mkdir(parents=True, exist_ok=True)
        with db.connect() as conn:
            db.create_conversation(
                conn, self.conv_id, str(self.conv_folder),
                user_language=None, mode=self.mode,
            )

    def resume_conversation(self, folder_path: str, user_language: str) -> None:
        """Reattach to an existing conversation folder.

        Sets self.conv_folder to the existing path, restores turn_index from
        the highest turn_index in DB requests, and re-activates the row if
        it was 'closed'.
        """
        self.conv_folder = Path(folder_path)
        if not self.conv_folder.exists():
            raise FileNotFoundError(f"Conversation folder missing: {folder_path}")
        self.user_language = user_language
        with db.connect() as conn:
            row = conn.execute(
                "SELECT MAX(turn_index) AS max_turn FROM requests "
                "WHERE conversation_id=? AND parent_request_id IS NULL",
                (self.conv_id,),
            ).fetchone()
            self.turn_index = (row["max_turn"] if row["max_turn"] is not None else -1)
            conn.execute(
                "UPDATE conversations SET status='active', "
                "modified_at=datetime('now') WHERE id=? AND status='closed'",
                (self.conv_id,),
            )

    def close_conversation(self) -> None:
        """Mark the conversation as closed in DB and clean up the sandbox. Safe to call multiple times."""
        if self.conv_folder is None:
            return
        with db.connect() as conn:
            # If a request is awaiting_human, keep that status.
            row = conn.execute(
                "SELECT 1 FROM requests WHERE conversation_id=? "
                "AND status='awaiting_human' LIMIT 1",
                (self.conv_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "UPDATE conversations SET status='closed', "
                    "modified_at=datetime('now') WHERE id=?",
                    (self.conv_id,),
                )
        self.cleanup_sandbox()

    def run(self, user_input: str) -> Generator[object]:
        """Process one user input. Yields events; the CLI consumes them."""
        self.user_language = _detect_language(user_input)
        self._turn_exchanges = []
        self._turn_started_at = time.monotonic()
        self._plan_steps = []
        self._plan_counters = {}
        self._total_delegations = 0

        if self.conv_folder is None:
            # CLI did not call bootstrap_conversation() — create lazily (backward compat).
            self.bootstrap_conversation()

        if self.turn_index == -1:
            # First turn of this conversation.
            self.turn_index = 0
            with db.connect() as conn:
                db.update_conversation_language(conn, self.conv_id, self.user_language)
            yield ConversationStarted(
                conversation_id=self.conv_id,
                folder_path=str(self.conv_folder),
                user_language=self.user_language,
            )
        else:
            # Subsequent turns.
            self.turn_index += 1
            yield TurnStarted(turn_index=self.turn_index)

        enriched_input = self._prefix_summary(user_input)
        append_to_journal(self.conv_folder, f"## User (turn {self.turn_index})\n{user_input}\n")

        answer, _artifact, _converged = yield from self._run_request(
            agent_code="jean-michel",
            inbound_text=enriched_input,
            expected_outcome="Address the human request fully.",
            support_files=[],
            parent_request_id=None,
            depth=0,
            sender="human",
        )

        append_to_journal(self.conv_folder, f"## Jean-Michel\n{answer}\n")
        yield FinalAnswer(text=answer)

        if self.mode in {"chat", "vocal", "analyse"}:
            yield from self._run_archivist(user_input, answer)

    # ---- Internal --------------------------------------------------------

    def _run_request(self, *, agent_code: str, inbound_text: str,
                     expected_outcome: str, support_files: list[str],
                     parent_request_id: str | None, depth: int,
                     sender: str, step_id: str | None = None) -> Generator[object, None, tuple[str, str | None, bool]]:
        """Run a single agent request, recursively if it delegates.

        Returns (answer, artifact_filename, converged) — the answer text, the
        filename of the response artifact (or None), and whether the agent
        signalled convergence via signal_convergence rather than return_to_user.
        """
        assert self.conv_folder is not None
        current_step_id = step_id

        with db.connect() as conn:
            agent = db.get_agent_by_code(conn, agent_code)
            paradigms = db.load_paradigms_for_agent(conn, agent.id, self.mode)
            available_agents = db.list_active_agents(conn)
            tool_grants = db.load_tool_grants(conn, agent.id)
            has_workspace_write = db.has_workspace_grant(conn, agent.id)
            sandbox_grants = db.load_sandbox_grants(conn, agent.id)
            delegation_targets = db.load_delegation_targets(conn, agent.id)
            req_id = _new_uuid()
            db.create_request(
                conn,
                req_id=req_id,
                conv_id=self.conv_id,
                parent_id=parent_request_id,
                depth=depth,
                agent_id=agent.id,
                inbound_briefing=inbound_text,
                expected_outcome=expected_outcome,
                turn_index=self.turn_index,
            )
            db.update_request_status(conn, req_id, "running")

        yield AgentStarted(agent_code=agent_code, request_id=req_id, depth=depth)

        # request_id_provider is a closure so bash_sandbox can record audit rows
        # against the *current* req_id without receiving it as a tool argument.
        _current_req_id = req_id
        def _req_id_provider() -> str:
            return _current_req_id

        registry = build_registry(
            self.conv_folder,
            has_workspace_write=has_workspace_write,
            conv_id=self.conv_id,
            request_id_provider=_req_id_provider,
            sandbox_grants=sandbox_grants if sandbox_grants else None,
            sandbox_image=agent.sandbox_image,
            agent_role=agent.role,
        )

        # Multi-step loop: tool_call -> tool_response -> ... until return_to_user.
        running_user_text = inbound_text
        seen_ask = False  # at most one ask_human across all steps of this request
        _seen_calls: set[str] = set()  # normalised fingerprints — registered before execution
        _call_results: dict[str, str] = {}  # fp -> last result (for replay on duplicate)
        _duplicate_counts: dict[str, int] = {}  # fp -> how many times we've blocked it
        _consecutive_duplicates: int = 0
        _critical_fs_errors: int = 0
        _recent_tool_calls: list[dict] = []  # for partial report on crash

        # Task-class gate: router must call set_task_class before delegating
        # in planning modes. Load the persisted class (set by a prior turn, if
        # any) so the gate is transparent for turn 2+.
        _current_task_class: str | None = None
        if agent.role == "router" and self.mode in _PLANNING_MODES:
            with db.connect() as conn:
                _current_task_class, _ = db.get_pipeline_state(conn, self.conv_id)

        # Build the system prompt once — the mission is immutable for the
        # lifetime of this request. Only the LLM user message changes between
        # tool-call iterations.
        ctx = PromptContext(
            agent=agent,
            paradigms=paradigms,
            user_profile=self.profile,
            detected_language=self.user_language,
            conversation_id=self.conv_id,
            conversation_folder=str(self.conv_folder),
            request_id=req_id,
            parent_request_id=parent_request_id,
            depth=depth,
            mode=self.mode,
            turn_index=self.turn_index,
            sender=sender,
            expected_outcome=expected_outcome,
            support_files=support_files,
            inbound_text=inbound_text,
            tool_registry=registry,
            available_agents=available_agents,
            turn_clarifications=list(self._turn_exchanges),
            conv_budget=_budget_snapshot(self.conv_id) if agent.role == "router" else None,
            delegation_targets=frozenset(delegation_targets),
        )
        system = render_system_prompt(ctx)
        tools_payload = tools_payload_for_agent(
            agent.role, tool_grants, registry, depth=depth,
            has_workspace_write=has_workspace_write, agent_code=agent.code,
        )
        self._write_artifact(req_id, agent_code, "prompt",
            f"## System\n\n{system}\n\n---\n\n## User\n\n{running_user_text}\n")

        start_ts = time.monotonic()
        _timeout_scope: str | None = None
        _timeout_elapsed: float = 0.0
        _soft_deadline_triggered = False
        # Conclusion tool the agent is allowed to keep once the soft deadline
        # fires. None for unknown roles (no restriction applied).
        if agent.role == "specialist":
            _conclusion_tool = "report_findings"
        elif agent.role in ("router", "finalizer"):
            _conclusion_tool = "return_to_user"
        else:
            _conclusion_tool = None

        try:
            llm_steps = 0
            step_bonus = 0  # extended each time a workspace write succeeds
            while llm_steps < MAX_STEPS_PER_REQUEST + step_bonus:
                now = time.monotonic()
                if now - start_ts > REQUEST_WALL_CLOCK_SECONDS:
                    _timeout_scope = "request"
                    _timeout_elapsed = now - start_ts
                    break
                if now - self._turn_started_at > TURN_WALL_CLOCK_SECONDS:
                    _timeout_scope = "turn"
                    _timeout_elapsed = now - self._turn_started_at
                    break

                # Soft deadline: force a graceful wrap-up. We restrict the
                # tool payload to the agent's conclusion tool and inject an
                # orchestrator message asking for a partial conclusion. Only
                # fires once per request.
                if (
                    not _soft_deadline_triggered
                    and _conclusion_tool is not None
                    and SOFT_DEADLINE_RATIO < 1.0
                ):
                    elapsed_req = now - start_ts
                    elapsed_turn = now - self._turn_started_at
                    soft_req = REQUEST_WALL_CLOCK_SECONDS * SOFT_DEADLINE_RATIO
                    soft_turn = TURN_WALL_CLOCK_SECONDS * SOFT_DEADLINE_RATIO
                    if elapsed_req > soft_req or elapsed_turn > soft_turn:
                        _soft_deadline_triggered = True
                        _soft_scope = "request" if elapsed_req > soft_req else "turn"
                        _soft_el = elapsed_req if _soft_scope == "request" else elapsed_turn
                        tools_payload = [
                            t for t in tools_payload
                            if t.get("function", {}).get("name") == _conclusion_tool
                        ]
                        running_user_text = (
                            f"[ORCHESTRATOR] Time budget almost exhausted "
                            f"({_soft_el:.0f}s elapsed on {_soft_scope}). "
                            f"STOP all further exploration or delegation. Call "
                            f"{_conclusion_tool} NOW with the partial conclusion "
                            "you can draw from the information already gathered "
                            "(plan.md, workspace, prior tool results). State "
                            "explicitly in your answer that the results are "
                            "partial because the time budget was reached."
                        )
                        yield SoftDeadlineReached(
                            scope=_soft_scope,
                            agent_code=agent_code,
                            elapsed_seconds=_soft_el,
                        )

                # Deep-research guard for the router. Once any delegation has
                # happened in this conversation (signalled by plan.md existing),
                # the router must orchestrate, not execute data-gathering
                # itself. Stripping these tools forces it to delegate to
                # specialists that persist their results to workspace, instead
                # of trapping web_search results in its private context where
                # children can never reach them.
                effective_tools = tools_payload
                if (
                    agent.role == "router"
                    and (
                        (self.conv_folder / "plan.md").exists()
                        or _current_task_class == "deep_research"
                    )
                ):
                    effective_tools = [
                        t for t in tools_payload
                        if t.get("function", {}).get("name")
                        not in _ROUTER_DEEP_RESEARCH_FORBIDDEN_TOOLS
                    ]

                try:
                    response: LLMResponse = self.llm.chat(
                        system=system,
                        user=running_user_text,
                        tools=effective_tools,
                        temperature=agent.temperature,
                        thinking=agent.thinking_mode,
                    )
                except LLMTimeoutError:
                    _llm_elapsed = float(LLM_CALL_TIMEOUT_SECONDS)
                    yield WallClockExceeded(
                        scope="llm_call",
                        agent_code=agent_code,
                        elapsed_seconds=_llm_elapsed,
                    )
                    if time.monotonic() - self._turn_started_at < TURN_WALL_CLOCK_SECONDS:
                        running_user_text = (
                            "[ORCHESTRATOR] The previous LLM call timed out after "
                            f"{LLM_CALL_TIMEOUT_SECONDS}s. Please conclude with "
                            "the information already available to you."
                        )
                        continue
                    _timeout_scope = "llm_call"
                    _timeout_elapsed = _llm_elapsed
                    break
                if response.corrupted:
                    err_msg = (
                        "LLM produced corrupted output (contains tokenisation markers) "
                        "twice in a row. Likely cause: model hung or context truncated."
                    )
                    md_report, error_payload = _build_partial_report(
                        self.conv_folder, req_id, agent_code,
                        status="llm_output_corrupted", error=err_msg,
                        recent_tool_calls=_recent_tool_calls,
                        lang=self.user_language,
                    )
                    artifact = self._write_artifact(req_id, agent_code, "response", error_payload)
                    with db.connect() as conn:
                        db.update_request_status(conn, req_id, "failed", completed=True)
                    yield CorruptedOutputDetected(agent_code=agent_code)
                    return md_report, artifact, False
                llm_steps += 1

                if response.thinking:
                    self._write_artifact(req_id, agent_code, "thought", response.thinking)
                    yield ThoughtCaptured(agent_code=agent_code, text=response.thinking)

                # No tool calls: model produced free text. Treat as implicit return_to_user.
                if not response.tool_calls:
                    final = response.content.strip() or "(empty response)"
                    artifact = self._write_artifact(req_id, agent_code, "response", final)
                    with db.connect() as conn:
                        db.update_request_status(conn, req_id, "completed", completed=True)
                    return final, artifact, False

                tool_responses: list[str] = []
                for call in response.tool_calls:
                    yield ToolCallEmitted(agent_code=agent_code, tool_name=call.name,
                                          arguments=call.arguments)
                    self._write_artifact(req_id, agent_code, "tool_call",
                        f"**{call.name}**\n\n```json\n{call.arguments}\n```")
                    _recent_tool_calls.append({"name": call.name, "arguments": call.arguments})

                    # ---- Control tools --------------------------------------
                    if call.name == "return_to_user":
                        answer = (call.arguments.get("answer") or "").strip()
                        if _looks_corrupted(answer):
                            tool_responses.append(json.dumps({
                                "tool": "return_to_user",
                                "error": (
                                    "Your answer contains tokenisation markers "
                                    "(e.g. '<thought'). Rewrite a clean final answer "
                                    "without any XML-like markers."
                                ),
                            }))
                            continue
                        # Specialists must use report_findings, not return_to_user.
                        if agent.role in _SPECIALIST_ROLES:
                            tool_responses.append(json.dumps({
                                "tool": "return_to_user",
                                "error": (
                                    "Specialists must use report_findings to conclude, "
                                    "not return_to_user. return_to_user is reserved for "
                                    "the router (jean-michel) answering the human at the "
                                    "top level. Call report_findings(summary=..., "
                                    "confidence=...) instead."
                                ),
                            }))
                            continue
                        artifact = self._write_artifact(req_id, agent_code, "response", answer)
                        with db.connect() as conn:
                            db.update_request_status(conn, req_id, "completed", completed=True)
                        return answer, artifact, False

                    if call.name == "signal_convergence":
                        # Deprecated — redirect to report_findings.
                        yield SignalConvergenceRedirected(agent_code=agent_code)
                        tool_responses.append(json.dumps({
                            "tool": "signal_convergence",
                            "error": (
                                "signal_convergence is deprecated. "
                                "Use report_findings(summary=..., confidence=...) instead. "
                                "report_findings accepts the same 'synthesis' content as "
                                "your 'summary' field, plus optional files_produced, "
                                "sub_questions, and blockers."
                            ),
                        }))
                        continue

                    if call.name == "report_findings":
                        summary = (call.arguments.get("summary") or "").strip()
                        confidence = (call.arguments.get("confidence") or "").strip()
                        files_produced = list(call.arguments.get("files_produced") or [])
                        sub_questions = list(call.arguments.get("sub_questions") or [])
                        blockers = list(call.arguments.get("blockers") or [])

                        if not summary:
                            tool_responses.append(json.dumps({
                                "tool": "report_findings",
                                "error": "summary is required and must be a non-empty string.",
                            }))
                            continue
                        if confidence not in {"low", "medium", "high"}:
                            tool_responses.append(json.dumps({
                                "tool": "report_findings",
                                "error": "confidence must be one of: low, medium, high.",
                            }))
                            continue
                        if _looks_corrupted(summary):
                            tool_responses.append(json.dumps({
                                "tool": "report_findings",
                                "error": "summary contains tokenisation markers.",
                            }))
                            continue

                        # Guard: declared files must exist.
                        ws_root = self.conv_folder / "workspace"
                        missing_files = [
                            p for p in files_produced
                            if not (ws_root / p).exists()
                        ]
                        if missing_files:
                            tool_responses.append(json.dumps({
                                "tool": "report_findings",
                                "error": (
                                    f"Declared files_produced not found on disk: {missing_files}. "
                                    "Write them with workspace_create_file before calling "
                                    "report_findings."
                                ),
                            }))
                            continue

                        payload = {
                            "summary": summary,
                            "confidence": confidence,
                            "files_produced": files_produced,
                            "sub_questions": sub_questions,
                            "blockers": blockers,
                        }
                        artifact = self._write_artifact(
                            req_id, agent_code, "report",
                            json.dumps(payload, ensure_ascii=False, indent=2),
                        )
                        parent_view = _format_report_for_parent(agent_code, payload)
                        yield ReportFindingsReceived(
                            agent_code=agent_code,
                            confidence=confidence,
                            files_produced=files_produced,
                            sub_questions_count=len(sub_questions),
                        )
                        with db.connect() as conn:
                            db.update_request_status(conn, req_id, "completed", completed=True)
                        return parent_view, artifact, True

                    if call.name == "ask_human":
                        if seen_ask:
                            tool_responses.append(json.dumps({
                                "tool": "ask_human",
                                "error": "Only one ask_human is allowed per request.",
                            }))
                            continue
                        seen_ask = True
                        llm_steps -= 1  # ask_human is I/O, not an LLM step
                        answer = yield from self._handle_ask_human(
                            req_id, agent_code, call.arguments,
                        )
                        # Refresh turn_clarifications in the prompt after human reply.
                        ctx.turn_clarifications = list(self._turn_exchanges)
                        system = render_system_prompt(ctx)
                        tool_responses.append(json.dumps({
                            "tool": "ask_human",
                            "human_answer": answer,
                        }))
                        continue

                    if call.name == "delegate_to":
                        child_code = call.arguments.get("agent_code", "")
                        if child_code == "archivist":
                            tool_responses.append(json.dumps({
                                "tool": "delegate_to",
                                "error": "archivist is an internal component and cannot be called via delegate_to.",
                            }))
                            continue

                        # Gate 1 — classify_first: router must call set_task_class
                        # before any delegation in planning modes.
                        if (
                            self.mode in _PLANNING_MODES
                            and agent.role == "router"
                            and "set_task_class" in tool_grants
                            and _current_task_class is None
                        ):
                            tool_responses.append(json.dumps({
                                "tool": "delegate_to",
                                "error": (
                                    "classify_first: Before delegating, call "
                                    "set_task_class(task_class=...) to declare the "
                                    "complexity of this request. Use 'single_fact', "
                                    "'medium_task', or 'deep_research' — see the "
                                    "assess_complexity_first directive for criteria."
                                ),
                            }))
                            continue

                        # Gate 2 — plan_first: deep_research requires an externalised
                        # plan (manage_todo_list) before the first delegation.
                        if (
                            self.mode in _PLANNING_MODES
                            and agent.role == "router"
                            and _current_task_class == "deep_research"
                            and "manage_todo_list" in tool_grants
                            and not any(fp.startswith("manage_todo_list:") for fp in _seen_calls)
                            and not (self.conv_folder / "todo.json").exists()
                        ):
                            tool_responses.append(json.dumps({
                                "tool": "delegate_to",
                                "error": (
                                    "plan_first_required: This request is classified as "
                                    "deep_research. Before delegating, call "
                                    "manage_todo_list(operation='write', todos=[...]) to "
                                    "externalise your routing plan. List all planned steps "
                                    "with their assignee_hint, then come back to delegate."
                                ),
                            }))
                            continue

                        # Delegation whitelist: only explicitly listed targets allowed.
                        if child_code not in delegation_targets:
                            tool_responses.append(json.dumps({
                                "tool": "delegate_to",
                                "error": (
                                    f"You ({agent_code}) cannot delegate to {child_code!r}. "
                                    f"Allowed targets: {sorted(delegation_targets) or '[none]'}. "
                                    "If you have completed your work, use "
                                    "report_findings or return_to_user instead."
                                ),
                            }))
                            continue
                        if depth + 1 > MAX_RECURSION_DEPTH:
                            yield RecursionLimitReached(agent_code=agent_code, depth=depth + 1)
                            tool_responses.append(json.dumps({
                                "tool": "delegate_to",
                                "error": (
                                    f"Recursion depth {depth + 1} exceeds limit "
                                    f"{MAX_RECURSION_DEPTH}. Conclude with the "
                                    f"information at hand."
                                ),
                            }))
                            continue
                        # Guardrail: max delegations per turn to avoid runaway research.
                        self._total_delegations += 1
                        if self._total_delegations > MAX_DELEGATIONS:
                            tool_responses.append(json.dumps({
                                "tool": "delegate_to",
                                "error": (
                                    f"delegation_budget_exhausted: you have used "
                                    f"{MAX_DELEGATIONS} delegations this turn. "
                                    "Synthesize what you have and call return_to_user."
                                ),
                            }))
                            continue
                        briefing = call.arguments.get("briefing", "")
                        sup_files = call.arguments.get("support_files") or []
                        missing = [f for f in sup_files
                                   if not (self.conv_folder / f).exists()]
                        if missing:
                            tool_responses.append(json.dumps({
                                "tool": "delegate_to",
                                "error": (
                                    f"support_files not found in conversation folder: {missing}. "
                                    "support_files only accepts artifact filenames produced by a "
                                    "previous delegate_to call (the `artifact` field in the result). "
                                    "It does NOT accept workspace paths or files you fetched. "
                                    "How to fix — pick ONE option: "
                                    "(A) If the content is already in your briefing text: simply "
                                    "remove the invalid filename(s) from support_files and re-send "
                                    "with the same briefing. Do NOT clear the briefing. "
                                    "(B) If the content is NOT in your briefing: call "
                                    "workspace_create_file to write it, then reference the "
                                    "workspace path in the briefing text, and send support_files=[]."
                                ),
                            }))
                            continue
                        yield DelegationStarted(parent_agent=agent_code,
                                                child_agent=child_code, briefing=briefing)
                        # --- Deterministic plan.md update ---
                        _depth_cnt = self._plan_counters.get(depth, 0) + 1
                        self._plan_counters[depth] = _depth_cnt
                        if depth == 0:
                            _step_id = f"S{_depth_cnt}"
                        else:
                            _parent_cnt = self._plan_counters.get(depth - 1, 1)
                            _step_id = f"S{_parent_cnt}.{_depth_cnt}"
                        self._plan_steps.append({
                            "id": _step_id,
                            "agent": child_code,
                            "briefing": briefing,
                            "status": "in_progress",
                            "summary": "",
                            "files_produced": [],
                        })
                        _plan_writer.write(self.conv_folder, self._plan_steps)
                        # Normalise expected: legacy string → structured dict.
                        expected_raw = call.arguments.get("expected", "")
                        if isinstance(expected_raw, str):
                            expected_dict: dict = {
                                "completion_verb": "return_to_user",
                                "summary_format": expected_raw,
                            }
                            expected_str = expected_raw
                        else:
                            expected_dict = dict(expected_raw) if expected_raw else {}
                            expected_str = json.dumps(expected_raw)
                        self._write_artifact(req_id, agent_code, "briefing",
                            f"to: {child_code}\nexpected: {expected_raw}\n\n{briefing}")
                        try:
                            child_answer, child_artifact, child_converged = yield from self._run_request(
                                agent_code=child_code,
                                inbound_text=briefing,
                                expected_outcome=expected_str,
                                support_files=sup_files,
                                parent_request_id=req_id,
                                depth=depth + 1,
                                sender=agent_code,
                                step_id=_step_id,
                            )
                            response_obj: dict = {
                                "tool": "delegate_to",
                                "agent": child_code,
                                "artifact": child_artifact,
                                "answer": child_answer,
                            }
                            if child_converged:
                                response_obj["converged"] = True
                            # Post-delegation artifact validation.
                            required_arts = expected_dict.get("workspace_artifacts") or []
                            if required_arts:
                                ws_root = self.conv_folder / "workspace"
                                missing_req = [
                                    p for p in required_arts
                                    if not (ws_root / p).exists()
                                ]
                                if missing_req:
                                    response_obj["validation_error"] = (
                                        f"missing required workspace artifacts: {missing_req}"
                                    )
                            tool_responses.append(json.dumps(response_obj))
                            # Update plan step: done if child converged, blocked if aborted.
                            _step_files = _extract_files_from_report(child_answer)
                            if child_converged:
                                # Extract summary from formatted report ("### Summary\n...")
                                _sm_start = child_answer.find("### Summary\n")
                                _plan_summary = ""
                                if _sm_start >= 0:
                                    _rest = child_answer[_sm_start + 12:]
                                    _sm_end = _rest.find("\n###")
                                    _plan_summary = (_rest[:_sm_end] if _sm_end >= 0 else _rest).strip()
                                    if len(_plan_summary) > 120:
                                        _plan_summary = _plan_summary[:117] + "…"
                                _step_status = "done"
                            elif child_answer.startswith("## Aborted report") or child_answer.startswith("## Rapport interrompu"):
                                # Crash path: surface the abort reason in plan.md.
                                _status_line = ""
                                for _line in child_answer.split("\n"):
                                    if _line.startswith("**Status:**") or _line.startswith("**Statut :**"):
                                        _status_line = (
                                            _line.replace("**Status:**", "")
                                                 .replace("**Statut :**", "")
                                                 .strip()
                                        )
                                        break
                                _plan_summary = f"aborted: {_status_line}" if _status_line else "aborted"
                                # If the child managed to persist files, the work
                                # is salvageable — mark partial rather than blocked.
                                _step_status = "partial" if _step_files else "blocked"
                            else:
                                _plan_summary = ""
                                _step_status = "done"
                            # Update the step in-place.
                            for _s in self._plan_steps:
                                if _s["id"] == _step_id:
                                    _s["status"] = _step_status
                                    _s["summary"] = _plan_summary
                                    _s["files_produced"] = _step_files
                                    break
                            _plan_writer.write(self.conv_folder, self._plan_steps)
                        except KeyError:
                            tool_responses.append(json.dumps({
                                "tool": "delegate_to",
                                "agent": child_code,
                                "error": f"unknown agent: {child_code}",
                            }))
                        continue

                    # ---- Native Python tools --------------------------------
                    spec = registry.get(call.name)
                    if spec is None:
                        # Detect agent-as-tool confusion: LLMs sometimes use agent
                        # codes directly as function names instead of delegate_to.
                        _agent_codes = {ag.code for ag in available_agents}
                        _normalised = call.name.replace("_", "-")
                        _matched = call.name if call.name in _agent_codes else (
                            _normalised if _normalised in _agent_codes else None
                        )
                        if _matched:
                            _err = (
                                f"'{call.name}' is an agent, not a tool. "
                                f"Use delegate_to(agent_code='{_matched}', "
                                f"briefing='...', expected='...') to invoke it."
                            )
                        else:
                            _err = "unknown tool"
                        tool_responses.append(json.dumps({
                            "tool": call.name, "error": _err,
                        }))
                        continue
                    fp = _fingerprint(call.name, call.arguments, _spec_defaults(spec))
                    if fp in _seen_calls:
                        yield DuplicateCallBlocked(
                            agent_code=agent_code, tool_name=call.name, fingerprint=fp,
                        )
                        self._write_artifact(
                            req_id, agent_code, "duplicate_blocked",
                            "**tool:** " + call.name + "\n\n"
                            "**fingerprint:** `" + fp + "`\n\n"
                            "**arguments:**\n\n```json\n"
                            + json.dumps(call.arguments, ensure_ascii=False, indent=2)
                            + "\n```\n",
                        )
                        # Replay the cached result so the LLM has the data it was
                        # trying to re-fetch — prevents loops where the model keeps
                        # asking again because it forgot the previous response.
                        # We only re-inject the FULL payload the first time we block;
                        # subsequent duplicates get a short pointer so the context
                        # doesn't balloon if the LLM keeps banging on the same call.
                        cached = _call_results.get(fp)
                        dup_count = _duplicate_counts.get(fp, 0) + 1
                        _duplicate_counts[fp] = dup_count
                        if cached is not None and dup_count == 1:
                            tool_responses.append(json.dumps({
                                "tool": call.name,
                                "cached": True,
                                "notice": (
                                    "This exact call was already executed earlier in this "
                                    "request. Returning the cached result. Do NOT call this "
                                    "tool with the same arguments again — use the data below."
                                ),
                                "previous_result": cached,
                            }))
                        elif cached is not None:
                            tool_responses.append(json.dumps({
                                "tool": call.name,
                                "cached": True,
                                "duplicate_count": dup_count,
                                "notice": (
                                    f"Blocked: this call has now been attempted {dup_count} times. "
                                    "The result was already returned earlier in this conversation. "
                                    "Stop calling this tool and act on the data you already have, "
                                    "or change your approach entirely."
                                ),
                            }))
                        else:
                            tool_responses.append(json.dumps({
                                "tool": call.name,
                                "error": (
                                    "Duplicate call blocked. This call (after normalising whitespace, "
                                    "casing, and default-valued arguments) was already executed in "
                                    "this request. Re-running it cannot change the result. "
                                    "Either: (a) use the result you already have, or (b) change the "
                                    "ANGLE of your query (different keyword, different domain, "
                                    "different tool) — not a surface reformulation."
                                ),
                            }))
                        # Idempotent read tools (conv_read_file, workspace_view, …)
                        # don't count toward force-stop: re-reading is a legitimate
                        # uncertainty-resolution attempt, not a runaway loop. The
                        # block above is sufficient to stop budget waste.
                        if call.name in _IDEMPOTENT_READ_TOOLS:
                            continue
                        _consecutive_duplicates += 1
                        if _consecutive_duplicates >= 3:
                            err_msg = "3 consecutive duplicate-blocked calls — force-stopping."
                            md_report, payload = _build_partial_report(
                                self.conv_folder, req_id, agent_code,
                                status="loop_detected", error=err_msg,
                                recent_tool_calls=_recent_tool_calls,
                                lang=self.user_language,
                            )
                            artifact = self._write_artifact(req_id, agent_code, "response", payload)
                            with db.connect() as conn:
                                db.update_request_status(conn, req_id, "failed", completed=True)
                            yield ForcedConvergence(
                                agent_code=agent_code,
                                reason="3 consecutive duplicate-blocked calls",
                            )
                            return md_report, artifact, False
                        continue
                    _seen_calls.add(fp)
                    _consecutive_duplicates = 0
                    try:
                        result = spec.handler(**call.arguments)
                    except TypeError as e:
                        valid = list(
                            spec.parameters.get("properties", {}).keys()
                        )
                        result = tool_error(
                            "bad_arguments",
                            f"Bad arguments for tool '{call.name}': {e}",
                            valid_parameters=valid,
                        )
                    except Exception as e:  # noqa: BLE001
                        result = tool_error("tool_failed", f"Tool failed: {e}")
                    # Cache successful results so a future duplicate call (e.g.
                    # the LLM forgot it already read this file) can be served
                    # the same response without re-executing or losing data.
                    _call_results[fp] = result
                    yield ToolResponseRecorded(agent_code=agent_code,
                                               tool_name=call.name, response=result)
                    # Emit TodoListUpdated after a successful manage_todo_list call.
                    if call.name == "manage_todo_list":
                        try:
                            _tdata = json.loads(result)
                            if "error_code" not in _tdata and isinstance(_tdata.get("todos"), list):
                                _scope = "conversation" if agent.role == "router" else "request"
                                _rid = req_id if agent.role != "router" else None
                                yield TodoListUpdated(
                                    agent=agent_code,
                                    scope=_scope,
                                    request_id=_rid,
                                    todos=tuple(_tdata["todos"]),
                                    stats=_tdata.get("stats", {}),
                                )
                                if _scope == "conversation":
                                    _plan_writer.write(self.conv_folder, self._plan_steps)
                        except (json.JSONDecodeError, TypeError, KeyError):
                            pass
                    # Update local task-class cache after a successful set_task_class call.
                    if call.name == "set_task_class":
                        try:
                            _stc = json.loads(result)
                            if "error_code" not in _stc:
                                _current_task_class = call.arguments.get("task_class")
                        except (json.JSONDecodeError, TypeError):
                            pass
                    # For tools that return existing file content, write a stub
                    # artifact to avoid duplicating content already on disk.
                    # The LLM receives the full result regardless (via tool_responses).
                    if call.name in ("workspace_view", "conv_read_file"):
                        try:
                            rdata = json.loads(result)
                            _path = rdata.get("path", call.arguments.get("relative_path", "?"))
                            _bytes = len(rdata.get("content", result).encode())
                            _trunc = " [truncated]" if rdata.get("truncated") else ""
                            artifact_body = f"**{call.name}** → `{_path}` ({_bytes} bytes){_trunc}"
                        except Exception:
                            artifact_body = f"**{call.name}**\n\n```\n{result[:200]}\n```"
                    else:
                        artifact_body = f"**{call.name}**\n\n```\n{result}\n```"
                    self._write_artifact(req_id, agent_code, "tool_response", artifact_body)
                    tool_responses.append(result)
                    # Critical FS error detection.
                    try:
                        _robj = json.loads(result) if isinstance(result, str) else None
                    except (json.JSONDecodeError, ValueError):
                        _robj = None
                    _err_code = (_robj or {}).get("error_code") if isinstance(_robj, dict) else None
                    if _err_code in CRITICAL_ERROR_CODES:
                        _critical_fs_errors += 1
                        yield FilesystemErrorObserved(
                            agent_code=agent_code,
                            tool_name=call.name,
                            error_code=_err_code,
                            message=(_robj or {}).get("error", ""),
                        )
                        if _critical_fs_errors >= 3:
                            fs_err_msg = (
                                f"Agent encountered {_critical_fs_errors} critical "
                                "filesystem errors in one request. Failing fast."
                            )
                            md_report, fs_payload = _build_partial_report(
                                self.conv_folder, req_id, agent_code,
                                status="critical_fs_errors", error=fs_err_msg,
                                recent_tool_calls=_recent_tool_calls,
                                lang=self.user_language,
                            )
                            artifact = self._write_artifact(req_id, agent_code, "response", fs_payload)
                            with db.connect() as conn:
                                db.update_request_status(conn, req_id, "failed", completed=True)
                            return md_report, artifact, False
                    # Quota warning after a successful write.
                    elif call.name == "workspace_create_file" and _err_code is None:
                        # Reward the agent: persisting findings extends its budget,
                        # capped by MAX_STEP_BONUS. Discourages info-loops that
                        # never write anything.
                        if step_bonus < MAX_STEP_BONUS:
                            step_bonus = min(step_bonus + WRITE_STEP_BONUS, MAX_STEP_BONUS)
                        _ws_root = workspace_root_for(self.conv_folder)
                        _remaining = quota_remaining(_ws_root)
                        if _remaining < config.WORKSPACE_QUOTA_BYTES * 0.1:
                            yield QuotaWarning(
                                remaining_bytes=_remaining,
                                total_bytes=config.WORKSPACE_QUOTA_BYTES,
                            )
                    elif call.name == "workspace_str_replace" and _err_code is None:
                        if step_bonus < MAX_STEP_BONUS:
                            step_bonus = min(step_bonus + WRITE_STEP_BONUS, MAX_STEP_BONUS)

                    # ---- Plan: log this tool call as a sub-action of the
                    # current step (or the root if we're at top level).
                    self._plan_log_action(
                        step_id=current_step_id,
                        agent_code=agent_code,
                        tool_name=call.name,
                        arguments=call.arguments or {},
                        result=result if isinstance(result, str) else json.dumps(result),
                    )

                # Feed all tool responses back to the model on the next iteration.
                # Prepend a recap of the agent's current step (its own tool
                # calls + summarised results) so it sees what it has already
                # done even though the system prompt is rendered only once.
                _recap = render_plan_recap(
                    str(self.conv_folder), current_step_id=current_step_id,
                )
                running_user_text = (
                    _recap
                    + "[ORCHESTRATOR] Tool results below (one JSON object per "
                    "tool call, in the order of your calls). Resume execution "
                    "of your current task.\n\n"
                    + "\n".join(tool_responses)
                )

        except Exception:
            with db.connect() as conn:
                db.update_request_status(conn, req_id, "failed", completed=True)
            raise

        if _timeout_scope is not None:
            error_msg = (
                f"Wall-clock exceeded ({_timeout_scope}) — {_timeout_elapsed:.1f}s."
            )
            md_report, payload = _build_partial_report(
                self.conv_folder, req_id, agent_code,
                status=f"{_timeout_scope}_wall_clock_exceeded", error=error_msg,
                recent_tool_calls=_recent_tool_calls,
                lang=self.user_language,
            )
            artifact = self._write_artifact(req_id, agent_code, "response", payload)
            with db.connect() as conn:
                db.update_request_status(conn, req_id, "failed", completed=True)
            yield WallClockExceeded(
                scope=_timeout_scope,
                agent_code=agent_code,
                elapsed_seconds=_timeout_elapsed,
            )
            yield OrchestrationFailed(reason=error_msg)
            return md_report, artifact, False

        exchanges_summary = "; ".join(
            f"Q: {q} → A: {a}" for q, a in self._turn_exchanges
        ) or None
        budget_error = (
            "The agent exhausted its step budget without producing a result."
            + (f" Human clarified during this request: {exchanges_summary}"
               if exchanges_summary else "")
        )
        md_report, error_payload = _build_partial_report(
            self.conv_folder, req_id, agent_code,
            status="step_budget_exhausted", error=budget_error,
            recent_tool_calls=_recent_tool_calls,
            lang=self.user_language,
        )
        artifact = self._write_artifact(req_id, agent_code, "response", error_payload)
        with db.connect() as conn:
            db.update_request_status(conn, req_id, "failed", completed=True)
        return md_report, artifact, False

    # ---- Summary helpers ------------------------------------------------

    def _prefix_summary(self, user_input: str) -> str:
        if self.turn_index == 0:
            return user_input
        assert self.conv_folder is not None
        summary_path = self.conv_folder / "summary.md"
        if not summary_path.exists():
            return user_input
        summary = summary_path.read_text(encoding="utf-8").strip()
        return (
            "## Conversation summary so far\n"
            f"{summary}\n\n"
            "## New user turn\n"
            f"{user_input}"
        )

    def _run_archivist(self, last_user: str, last_answer: str) -> Generator[object]:
        assert self.conv_folder is not None
        summary_path = self.conv_folder / "summary.md"
        previous_summary = ""
        if summary_path.exists():
            previous_summary = summary_path.read_text(encoding="utf-8")

        exchanges_block = ""
        if self._turn_exchanges:
            exchanges_block = (
                "\n\n## Clarifications exchanged during this turn\n"
                + "\n".join(f"- Q: {q}\n  A: {a}" for q, a in self._turn_exchanges)
            )
        briefing = (
            "Update the running summary.\n\n"
            f"## Previous summary\n{previous_summary or '(none)'}\n\n"
            f"## Latest user turn\n{last_user}{exchanges_block}\n\n"
            f"## Latest assistant answer\n{last_answer}\n\n"
            "Produce the new summary as the value of return_to_user. "
            "Follow the archivist_format paradigm strictly."
        )

        try:
            new_summary, _artifact, _converged = yield from self._run_request(
                agent_code="archivist",
                inbound_text=briefing,
                expected_outcome="Updated running summary, structured per archivist_format.",
                support_files=[],
                parent_request_id=None,
                depth=0,
                sender="orchestrator",
            )
        except Exception:  # noqa: BLE001
            return  # Keep previous summary on failure; do not block the user.

        # Persist the canonical summary.md and record it as a DB artifact.
        summary_path.write_text(new_summary, encoding="utf-8")
        # We attach the summary.md artifact to the most recent request of this
        # conversation (the archivist's request just above).
        with db.connect() as conn:
            row = conn.execute(
                "SELECT id FROM requests WHERE conversation_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (self.conv_id,),
            ).fetchone()
            if row is not None:
                db.record_artifact(conn, row["id"], "summary.md", "summary")

        yield SummaryUpdated(path=str(summary_path))

    # ---- ask_human handling ---------------------------------------------

    def _handle_ask_human(self, req_id: str, agent_code: str,
                          args: dict) -> Generator[object, None, str]:
        question = args.get("question", "")
        if isinstance(question, list):
            question = "\n".join(str(q) for q in question)
        elif not isinstance(question, str):
            question = str(question)
        why = args.get("why", "")
        if not isinstance(why, str):
            why = str(why)
        self._write_artifact(req_id, agent_code, "ask_human",
            f"**why:** {why}\n\n**question:**\n{question}")
        with db.connect() as conn:
            db.update_request_status(conn, req_id, "awaiting_human")
        yield HumanQuestionAsked(agent_code=agent_code, question=question, why=why)
        if self.ask_human_callback is None:
            answer = ""
        else:
            answer = self.ask_human_callback(question=question, why=why)
        self._write_artifact(req_id, agent_code, "human_answer", answer)
        with db.connect() as conn:
            db.update_request_status(conn, req_id, "running")
        self._turn_exchanges.append((question, answer))
        assert self.conv_folder is not None
        append_to_journal(
            self.conv_folder,
            f"## Clarification (agent: {agent_code})\n**Q:** {question}\n**A:** {answer}\n",
        )
        yield HumanAnswerReceived(answer=answer)
        return answer

    # ---- Misc ------------------------------------------------------------

    def _plan_log_action(self, *, step_id: str | None, agent_code: str,
                         tool_name: str, arguments: dict, result: str) -> None:
        """Log a tool call as a sub-action of the current plan step.

        Thin wrapper around ``plan_writer.log_action`` that also flushes the
        plan to disk so peer agents (router & specialists) see updates in
        their next prompt render. Filters and summarisation live in
        ``plan_writer``.
        """
        if step_id is None:
            return
        _plan_writer.log_action(
            self._plan_steps,
            step_id=step_id,
            agent=agent_code,
            tool_name=tool_name,
            arguments=arguments,
            result=result,
        )
        assert self.conv_folder is not None
        _plan_writer.write(self.conv_folder, self._plan_steps)

    def _write_artifact(self, request_id: str, agent_code: str,
                        kind: str, body: str) -> str:
        """Write a file artifact and record it in the DB. Returns the filename."""
        assert self.conv_folder is not None
        filename = write_artifact(
            self.conv_folder,
            conversation_id=self.conv_id,
            request_id=request_id,
            agent=agent_code,
            kind=kind,
            body=body,
        )
        with db.connect() as conn:
            db.record_artifact(conn, request_id, filename, kind)
        return filename

    def cleanup_sandbox(self) -> None:
        """Stop and remove the sandbox container for this conversation, if running."""
        import subprocess
        container_name = f"jm-sandbox-{self.conv_id}"
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", container_name],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
            )
