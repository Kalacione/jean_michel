"""Orchestrator: pure Python state machine, drives agent turns and persists everything.

Implemented as a generator that `yield`s events. The CLI consumes those events
to render the conversation in real time without coupling itself to the
orchestrator internals.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import config, db
from .config import MAX_RECURSION_DEPTH, MAX_STEPS_PER_REQUEST, UserProfile, ensure_dirs
from .llm import LLMClient
from .models import LLMResponse
from .persistence import (
    append_to_journal,
    conversation_folder_name,
    write_artifact,
)
from .prompts import (
    PromptContext,
    render_system_prompt,
    tools_payload_for_agent,
)
from .tools import build_registry

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


# ---- Helpers --------------------------------------------------------------

def _detect_language(text: str) -> str:
    try:
        from langdetect import detect
        return detect(text)
    except Exception:
        return "und"


def _new_uuid() -> str:
    return uuid.uuid4().hex[:12]


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
                     sender: str) -> Generator[object, None, tuple[str, str | None, bool]]:
        """Run a single agent request, recursively if it delegates.

        Returns (answer, artifact_filename, converged) — the answer text, the
        filename of the response artifact (or None), and whether the agent
        signalled convergence via signal_convergence rather than return_to_user.
        """
        assert self.conv_folder is not None

        with db.connect() as conn:
            agent = db.get_agent_by_code(conn, agent_code)
            paradigms = db.load_paradigms_for_agent(conn, agent.id, self.mode)
            available_agents = db.list_active_agents(conn)
            tool_grants = db.load_tool_grants(conn, agent.id)
            has_workspace_write = db.has_workspace_grant(conn, agent.id)
            sandbox_grants = db.load_sandbox_grants(conn, agent.id)
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
        )

        # Multi-step loop: tool_call -> tool_response -> ... until return_to_user.
        running_user_text = inbound_text
        seen_ask = False  # at most one ask_human across all steps of this request
        _successful_calls: set[str] = set()  # fingerprints of native tool calls that succeeded

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
        )
        system = render_system_prompt(ctx)
        tools_payload = tools_payload_for_agent(agent.role, tool_grants, registry, depth=depth)
        self._write_artifact(req_id, agent_code, "prompt",
            f"## System\n\n{system}\n\n---\n\n## User\n\n{running_user_text}\n")

        try:
            llm_steps = 0
            while llm_steps < MAX_STEPS_PER_REQUEST:
                response: LLMResponse = self.llm.chat(
                    system=system,
                    user=running_user_text,
                    tools=tools_payload,
                    temperature=agent.temperature,
                    thinking=agent.thinking_mode,
                )
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

                    # ---- Control tools --------------------------------------
                    if call.name == "return_to_user":
                        answer = (call.arguments.get("answer") or "").strip()
                        artifact = self._write_artifact(req_id, agent_code, "response", answer)
                        with db.connect() as conn:
                            db.update_request_status(conn, req_id, "completed", completed=True)
                        return answer, artifact, False

                    if call.name == "signal_convergence":
                        synthesis = (call.arguments.get("synthesis") or "").strip()
                        open_qs = call.arguments.get("open_questions") or []
                        if open_qs:
                            synthesis += "\n\nOpen questions:\n" + "\n".join(
                                f"- {q}" for q in open_qs
                            )
                        artifact = self._write_artifact(req_id, agent_code, "response", synthesis)
                        with db.connect() as conn:
                            db.update_request_status(conn, req_id, "completed", completed=True)
                        return synthesis, artifact, True

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
                        briefing = call.arguments.get("briefing", "")
                        expected = call.arguments.get("expected", "")
                        sup_files = call.arguments.get("support_files") or []
                        # Validate that every support_file actually exists in the
                        # conversation folder. Agents can only write to the workspace
                        # (workspace_create_file) — they cannot write to conv_folder.
                        # support_files is exclusively for orchestrator-written artifacts.
                        missing = [f for f in sup_files
                                   if not (self.conv_folder / f).exists()]
                        if missing:
                            tool_responses.append(json.dumps({
                                "tool": "delegate_to",
                                "error": (
                                    f"support_files not found in conversation folder: {missing}. "
                                    "support_files is only for orchestrator artifact filenames "
                                    "(the `artifact` value from a previous delegate_to result). "
                                    "For fetched data (e.g. Wikipedia), write it to the workspace "
                                    "with workspace_create_file, then reference the workspace "
                                    "path in the briefing text."
                                ),
                            }))
                            continue
                        yield DelegationStarted(parent_agent=agent_code,
                                                child_agent=child_code, briefing=briefing)
                        self._write_artifact(req_id, agent_code, "briefing",
                            f"to: {child_code}\nexpected: {expected}\n\n{briefing}")
                        try:
                            child_answer, child_artifact, child_converged = yield from self._run_request(
                                agent_code=child_code,
                                inbound_text=briefing,
                                expected_outcome=expected,
                                support_files=sup_files,
                                parent_request_id=req_id,
                                depth=depth + 1,
                                sender=agent_code,
                            )
                            response_obj: dict = {
                                "tool": "delegate_to",
                                "agent": child_code,
                                "artifact": child_artifact,
                                "answer": child_answer,
                            }
                            if child_converged:
                                response_obj["converged"] = True
                            tool_responses.append(json.dumps(response_obj))
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
                    call_fingerprint = (
                        f"{call.name}:{json.dumps(call.arguments, sort_keys=True)}"
                    )
                    if call_fingerprint in _successful_calls:
                        tool_responses.append(json.dumps({
                            "tool": call.name,
                            "error": (
                                "Duplicate call detected. This exact call already produced "
                                "a result earlier in this request. Re-running it will not "
                                "change the output. Use the previous result or reformulate "
                                "your query with different arguments."
                            ),
                        }))
                        continue
                    try:
                        result = spec.handler(**call.arguments)
                    except TypeError as e:
                        valid = list(
                            spec.parameters.get("properties", {}).keys()
                        )
                        result = json.dumps({
                            "error": (
                                f"Bad arguments for tool '{call.name}': {e}. "
                                f"Valid parameters: {valid}"
                            )
                        })
                    except Exception as e:  # noqa: BLE001
                        result = json.dumps({"error": f"Tool failed: {e}"})
                    # Register as successful only if the result is not an error.
                    if not (isinstance(result, str) and '"error"' in result):
                        _successful_calls.add(call_fingerprint)
                    yield ToolResponseRecorded(agent_code=agent_code,
                                               tool_name=call.name, response=result)
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

                # Feed all tool responses back to the model on the next iteration.
                running_user_text = (
                    "[ORCHESTRATOR] Tool results below (one JSON object per "
                    "tool call, in the order of your calls). Resume execution "
                    "of your current task.\n\n"
                    + "\n".join(tool_responses)
                )

        except Exception:
            with db.connect() as conn:
                db.update_request_status(conn, req_id, "failed", completed=True)
            raise

        exchanges_summary = "; ".join(
            f"Q: {q} → A: {a}" for q, a in self._turn_exchanges
        ) or None
        error_payload = json.dumps({
            "status": "step_budget_exhausted",
            "agent": agent_code,
            "partial_clarifications": exchanges_summary,
            "error": (
                "The agent exhausted its step budget without producing a result."
                + (f" Human clarified during this request: {exchanges_summary}"
                   if exchanges_summary else "")
            ),
        })
        artifact = self._write_artifact(req_id, agent_code, "response", error_payload)
        with db.connect() as conn:
            db.update_request_status(conn, req_id, "failed", completed=True)
        return error_payload, None, False

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

    def _run_archivist(self, last_user: str, last_answer: str) -> Generator[object, None, None]:
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
