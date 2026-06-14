"""v2 orchestrator — Tier 1 main loop + Tier 2 subagent + nested delegation.

The whole module is ~400 lines, split into three layers :

1. **Data types**   : `AgentSpec`, `SubResult`, `_LoopContext`.
2. **Helpers**      : tool payload assembly, message-shape conversion,
                       budget initialisation, event emission.
3. **Loops**        : `_run_agent_loop` (shared core), `run_main_loop`
                       (Tier 1 entry), `spawn_subagent` (Tier 2 entry).

The loops follow the pseudo-code in `DevNotes/REVOLUCION/06_proposition_v2.md
§4` and `§5`. Termination conditions :

- main agent : an `assistant` turn without `tool_calls`. The `content` IS
  the user-facing answer.
- subagent   : the `report_back` tool call. Validated against
  `tools.report_back.validate_report_back_args`. The result is returned
  as a `SubResult`.

Nested delegation is the natural consequence of subagent_loop calling
`spawn_subagent` itself when the LLM emits a `delegate_to`. The `state.depth_current`
counter is incremented at every level — the `PreToolUse` hook refuses
`delegate_to` once `depth_current + 1 > MAX_DEPTH`.

The module is DB-agnostic : it takes `AgentSpec` instances and a tools
registry as inputs. Phase 6 will wire the DB loader. Until then, tests
pass dataclasses directly.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import context_packet, deliberation
from .config import (
    DEFAULT_MODEL_CONTEXT_WINDOW,
    OUTPUT_RESERVE_RATIO,
    SUBAGENT_BUDGET_RATIO,  # noqa: F401 — reserved for later use
    SUBAGENT_DEFAULT_MODEL,
    model_context_window,
)
from .events import (
    AgentThinking,
    AgentTokenStreamed,
    DelegationCompleted,
    DelegationStarted,
    HookFired,
    LLMCallCompleted,
    LLMCallStarted,
    RequestCompleted,
    RequestStarted,
    ToolCallCompleted,
    ToolCallStarted,
    WorkingBudgetUpdate,
)
from .hooks import HookRegistry, ToolCallContext, build_hook_registry
from .models import ConversationState, LLMResponse, ToolCall
from .persistence import (
    append_event,
    load_sub_messages,
    save_messages,
    save_state,
    save_sub_messages,
)
from .todo import load_todo
from .tokens import estimate_messages_tokens, estimate_tools_payload_tokens
from .tools import _repo
from .tools._workspace import workspace_root_for
from .tools.delegate_to import DELEGATE_TO_SCHEMA
from .tools.report_back import REPORT_BACK_SCHEMA, validate_report_back_args

_log = logging.getLogger(__name__)


# =============================================================================
# Control-verb schemas only used by this module
# =============================================================================

# `ask_human` is exclusive to the main agent (cf. §5 doc 06). It's a control
# verb : the loop intercepts the call, invokes a callback, and appends the
# human reply as a `role=user` message.
ASK_HUMAN_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "ask_human",
        "description": (
            "Ask the human directly and PAUSE for their reply. Two cases: (1) a "
            "clarification you genuinely need to proceed (use sparingly, only when "
            "an ambiguity blocks progress) ; (2) a decision among known options that "
            "only the user can make, OR when the user asks you to present them a "
            "choice / quiz them. The `why` field is mandatory: explain why you need "
            "their input. When the answer is a choice among known options, pass "
            "`choices` (a list) so the UI renders them as selectable options instead "
            "of plain text — set `multi=true` to allow picking several ; omit "
            "`choices` for an open-ended question. Do NOT write the options as plain "
            "text in your reply — use `choices`. "
            "Subagents do NOT have access to this tool ; they conclude with "
            "report_back(confidence='low', low_confidence_reason='...') instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "why": {"type": "string"},
                "choices": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: options to present as selectable answers ; omit for a free-text question.",
                },
                "multi": {
                    "type": "boolean",
                    "description": "Optional: allow selecting several options (default false).",
                },
            },
            "required": ["question", "why"],
        },
    },
}


_CONTROL_VERBS: frozenset[str] = frozenset({
    "delegate_to",
    "ask_human",
    "report_back",
})

# A blocked specialist signals it needs a human decision by concluding with
# report_back(confidence="low", low_confidence_reason="HUMAN INPUT NEEDED: <question>").
# The router asks the human, then re-delegates to the same agent → resume_subagent (P5).
_HUMAN_INPUT_MARKER = "HUMAN INPUT NEEDED:"


# =============================================================================
# Data types
# =============================================================================


@dataclass
class AgentSpec:
    """Minimal description of one agent for the v2 loop.

    `system_prompt` is pre-rendered (composition of identity + paradigms +
    output contract). The v2 loop does not re-render it ; it injects it
    verbatim as the first message. Phase 6 will wire `prompts.render_system_prompt_v2`
    as the canonical renderer.

    `tool_grants` and `delegation_targets` are frozen sets — the loop relies
    on `frozenset` semantics for fingerprinting and equality.
    """
    code: str
    role: str  # "router" | "specialist" | "finalizer"
    system_prompt: str
    tool_grants: frozenset[str] = field(default_factory=frozenset)
    delegation_targets: frozenset[str] = field(default_factory=frozenset)
    model: str = ""           # empty → defer to MAIN_MODEL / SUBAGENT_DEFAULT_MODEL
    thinking: bool = True
    temperature: float = 0.2


@dataclass
class SubResult:
    """Structured return from a subagent.

    Mirrors the schema of `tools.report_back.REPORT_BACK_SCHEMA` plus the
    agent code. Serialised as a `role=tool` payload when pushed back into
    the caller's messages[].
    """
    agent: str
    summary: str
    files_produced: list[str] = field(default_factory=list)
    confidence: str = "high"            # "low" | "medium" | "high"
    low_confidence_reason: str = ""
    # Work needs the worker surfaced for the orchestrator's plan (D11). The
    # orchestrator (sole TODO writer) folds these into its next todo_write.
    suggested_todo_updates: list[str] = field(default_factory=list)
    # Internal (never serialized to the LLM) : the subagent's saved-trace id, so the
    # orchestrator can RESUME this exact trace after a human round-trip (P5).
    request_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "agent": self.agent,
            "summary": self.summary,
            "files_produced": list(self.files_produced),
            "confidence": self.confidence,
        }
        if self.confidence == "low" and self.low_confidence_reason:
            out["low_confidence_reason"] = self.low_confidence_reason
        if self.suggested_todo_updates:
            out["suggested_todo_updates"] = list(self.suggested_todo_updates)
        return out


# Optional callbacks the loop may invoke.
EventEmitter = Callable[[Any], None]
AskHumanCallback = Callable[[str, str, list[str], bool], str]  # (question, why, choices, multi) → answer
AgentResolver = Callable[[str], "AgentSpec | None"]  # agent_code → spec


# =============================================================================
# Helpers
# =============================================================================


def _tool_call_to_dict(tc: ToolCall) -> dict[str, Any]:
    """Convert a parsed ToolCall back to Ollama's tool_calls dict shape."""
    return {
        "type": "function",
        "function": {
            "name": tc.name,
            "arguments": dict(tc.arguments),
        },
    }


def _build_tools_payload(agent: AgentSpec, registry: dict[str, Any]) -> list[dict[str, Any]]:
    """Combine control-verb schemas + agent-granted registry tools.

    The LLM only sees tools the agent is authorised to call — sending it
    forbidden tools would confuse it (and might leak across roles).
    Control verbs (ask_human / delegate_to / report_back) are role-gated
    here so the schema matches what the loop actually accepts.
    """
    payload: list[dict[str, Any]] = []

    if agent.role == "router":
        payload.append(ASK_HUMAN_SCHEMA)
        payload.append(DELEGATE_TO_SCHEMA)
    elif agent.role == "specialist":
        payload.append(DELEGATE_TO_SCHEMA)
        payload.append(REPORT_BACK_SCHEMA)
    # finalizer has no control-verb : it terminates on an empty tool_calls
    # turn (same as router for the user-facing answer).

    for tool_name in sorted(agent.tool_grants):
        spec = registry.get(tool_name)
        if spec is None:
            continue
        payload.append({
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        })

    return payload


def _initialize_state(
    state: ConversationState,
    initial_messages: list[dict[str, Any]],
    tools_payload: list[dict[str, Any]],
    model: str,
) -> None:
    """Set SYSTEM_RESERVE / OUTPUT_RESERVE / WORKING in `state` for a fresh run."""
    total_ctx = model_context_window(model) if model else DEFAULT_MODEL_CONTEXT_WINDOW
    system_msg_tokens = (
        estimate_messages_tokens([initial_messages[0]])
        if initial_messages
        else 0
    )
    tools_tokens = estimate_tools_payload_tokens(tools_payload)
    state.system_reserve_tokens = system_msg_tokens + tools_tokens
    state.output_reserve_tokens = int(OUTPUT_RESERVE_RATIO * total_ctx)
    state.working_budget = max(
        1024,
        total_ctx - state.system_reserve_tokens - state.output_reserve_tokens,
    )


def _emit(emitter: EventEmitter | None, conv_folder: Path | None, event: Any) -> None:
    """Emit an event to the optional live emitter AND persist to events.jsonl."""
    if emitter is not None:
        try:
            emitter(event)
        except Exception as exc:  # noqa: BLE001
            _log.warning("event emitter raised: %s", exc)
    if conv_folder is not None:
        try:
            append_event(conv_folder, event)
        except Exception as exc:  # noqa: BLE001
            _log.warning("event persistence failed: %s", exc)


def _execute_native_tool(call: ToolCall, registry: dict[str, Any]) -> dict[str, Any]:
    """Run a registry tool handler. Returns the parsed JSON result.

    The handler always returns a JSON string (cf. `tool_ok` / `tool_error`).
    Parsing failures are surfaced as a structured error so the loop never
    crashes on a bad tool.
    """
    spec = registry.get(call.name)
    if spec is None:
        return {
            "error": "unknown_tool",
            "summary": f"Tool '{call.name}' not in registry.",
        }
    try:
        raw = spec.handler(**call.arguments)
    except Exception as exc:  # noqa: BLE001
        return {
            "error": "handler_raised",
            "summary": f"Tool '{call.name}' raised: {exc}",
        }
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {
            "error": "tool_output_not_json",
            "summary": (raw[:200] + "…") if isinstance(raw, str) and len(raw) > 200 else str(raw),
        }
    if not isinstance(data, dict):
        return {"error": "tool_output_not_object", "summary": str(data)[:200]}
    return data


# =============================================================================
# Loop core
# =============================================================================


def _append_assistant_turn(messages: list[dict[str, Any]], resp: LLMResponse) -> None:
    """Append an assistant turn from the LLM response to messages[]."""
    msg: dict[str, Any] = {
        "role": "assistant",
        "content": resp.content,
    }
    if resp.thinking:
        msg["thinking"] = resp.thinking
    if resp.tool_calls:
        msg["tool_calls"] = [_tool_call_to_dict(tc) for tc in resp.tool_calls]
    messages.append(msg)


def _append_tool_message(
    messages: list[dict[str, Any]], tool_name: str, result: dict[str, Any]
) -> None:
    """Append a `role=tool` message with the JSON-serialized result."""
    messages.append({
        "role": "tool",
        "tool_name": tool_name,
        "content": json.dumps(result, ensure_ascii=False),
    })


def _missing_support_files(conv_folder: Path, support_files: list[str]) -> list[str]:
    """support_files a worker can't find anywhere → a doomed delegation. A handoff
    artifact lives in the WORKSPACE ; a source file in the repo WORKTREE. Return the
    ones that exist in NEITHER, so the router is told instead of spawning a worker
    that loops looking for a phantom file (bug C, conv dfcafc75 : 'v1_analysis.md')."""
    roots = [
        r for r in (workspace_root_for(conv_folder), _repo.worktree_root(conv_folder))
        if r is not None
    ]
    missing: list[str] = []
    for rel in support_files:
        found = False
        for root in roots:
            try:
                target = _repo.safe_resolve(root, rel)
            except ValueError:
                continue
            if target.exists() and target.is_file():
                found = True
                break
        if not found:
            missing.append(rel)
    return missing


def _args_summary(args: dict[str, Any], max_chars: int = 80) -> str:
    """Short string repr of tool args for events."""
    s = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return s if len(s) <= max_chars else s[: max_chars - 1] + "…"


def _result_summary(result: dict[str, Any], max_chars: int = 120) -> str:
    """Short string repr of tool result for events."""
    s = result.get("summary") or result.get("error") or json.dumps(result, ensure_ascii=False)
    s = str(s)
    return s if len(s) <= max_chars else s[: max_chars - 1] + "…"


@dataclass
class _LoopOutcome:
    """Internal : what the shared loop produced for its caller."""
    kind: str  # "final_answer" | "report_back" | "aborted"
    content: str = ""               # for "final_answer"
    sub_result: SubResult | None = None  # for "report_back"
    reason: str = ""                # for "aborted"


def _run_agent_loop(
    *,
    conv_folder: Path,
    agent: AgentSpec,
    messages: list[dict[str, Any]],
    state: ConversationState,
    tools_registry: dict[str, Any],
    llm_client: Any,
    hooks: HookRegistry,
    dedup_cache: dict[str, dict[str, Any]],
    is_main_agent: bool,
    max_iterations: int,
    ask_human_callback: AskHumanCallback | None = None,
    agent_resolver: AgentResolver | None = None,
    event_emitter: EventEmitter | None = None,
) -> _LoopOutcome:
    """Core iteration. Shared between main agent and subagent.

    Termination :
    - main agent : assistant turn with no tool_calls → `_LoopOutcome(kind="final_answer")`.
    - subagent : `report_back` tool_call (after validation) → `_LoopOutcome(kind="report_back")`.
    Iteration cap : `max_iterations` → `_LoopOutcome(kind="aborted")`.
    """
    tools_payload = _build_tools_payload(agent, tools_registry)
    model = agent.model or SUBAGENT_DEFAULT_MODEL
    # Counter for empty assistant turns (no tool_calls AND no content).
    # The LLM sometimes finishes everything in its `thinking` channel and
    # emits an empty content turn — we nudge it once or twice before
    # giving up.
    empty_main_turns = 0
    plan_no_todo_turns = 0  # PLAN mode : tried to conclude without writing the plan
    no_tool_call_turns = 0  # subagent : emitted prose instead of a report_back tool_call

    def _persist() -> None:
        # Subagents must NOT clobber the main conv files (messages.json/state.json):
        # their audit is written once at the end via save_sub_messages. Only the
        # main agent owns these files.
        if is_main_agent:
            save_messages(conv_folder, messages)
            save_state(conv_folder, state)

    for _iteration in range(max_iterations):
        # PreLLMCall : compaction escalation (may mutate messages).
        level = hooks.pre_llm_call(messages, state)
        if level > 0:
            ratio = state.working_tokens_used / max(1, state.working_budget)
            _emit(
                event_emitter,
                conv_folder,
                WorkingBudgetUpdate(ratio=ratio, compaction_level_triggered=level),
            )

        _emit(
            event_emitter,
            conv_folder,
            LLMCallStarted(
                agent=agent.code,
                model=model,
                messages_count=len(messages),
                working_tokens_used=state.working_tokens_used,
            ),
        )

        # Live token stream → UI (best-effort). Emitted with conv_folder=None so it is
        # forwarded to the WebSocket but NEVER persisted to events.jsonl (cf. event doc).
        on_token = None
        if event_emitter is not None:
            def on_token(delta: str, _agent: str = agent.code) -> None:
                _emit(event_emitter, None, AgentTokenStreamed(agent=_agent, delta=delta))

        try:
            resp = llm_client.chat_messages(
                messages=messages,
                tools=tools_payload,
                temperature=agent.temperature,
                thinking=agent.thinking,
                model=model,
                stream_log_dir=conv_folder,        # per-conversation slop trace
                stream_log_label=agent.code,
                on_token=on_token,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("LLM call failed in %s: %s", agent.code, exc)
            return _LoopOutcome(kind="aborted", reason=f"llm_call_failed: {exc}")

        _emit(
            event_emitter,
            conv_folder,
            LLMCallCompleted(
                tokens_used=resp.prompt_eval_count + resp.eval_count,
                tool_call_count=len(resp.tool_calls),
            ),
        )

        # Surface the thinking channel (web frontend "thoughts" ; CLI shows it
        # only with --show-thoughts). Emitted only when non-empty.
        if (resp.thinking or "").strip():
            _emit(
                event_emitter,
                conv_folder,
                AgentThinking(agent=agent.code, text=resp.thinking),
            )

        _append_assistant_turn(messages, resp)

        # Termination — main agent : empty tool_calls is the final answer.
        if not resp.tool_calls:
            if is_main_agent:
                # Guard against empty content : the LLM sometimes finishes
                # in its `thinking` channel and emits an assistant turn
                # with content="". Nudge once or twice before accepting
                # the empty turn as a final answer.
                if not (resp.content or "").strip():
                    empty_main_turns += 1
                    if empty_main_turns <= 2:
                        _log.warning(
                            "%s emitted empty assistant turn (#%d), nudging.",
                            agent.code, empty_main_turns,
                        )
                        messages.append({
                            "role": "user",
                            "content": (
                                "[ORCHESTRATOR] (orchestrator control — not the "
                                "human user) Your last assistant turn was "
                                "empty. The user is waiting for an answer. "
                                "Produce the user-facing response NOW, in "
                                "plain text, based on the tool results above. "
                                "Do not return another empty turn."
                            ),
                        })
                        _persist()
                        continue
                    # 3 empty turns in a row : give up cleanly with an
                    # honest fallback instead of returning silence to the user.
                    fallback = (
                        "(Désolé, je n'ai pas réussi à formuler une réponse "
                        "à partir des informations collectées. Les détails "
                        "sont dans la conversation et le workspace.)"
                    )
                    _emit(
                        event_emitter,
                        conv_folder,
                        RequestCompleted(
                            agent=agent.code,
                            final_content_summary=fallback[:200],
                        ),
                    )
                    _persist()
                    return _LoopOutcome(kind="final_answer", content=fallback)

                # PLAN mode : the plan must be the structured todo.json (the
                # artifact the editor + the execute turn consume), not just prose.
                # Refuse to conclude until todo_write has been called this turn.
                if state.plan_mode and load_todo(conv_folder) is None and plan_no_todo_turns < 2:
                    plan_no_todo_turns += 1
                    # Distinct prefix (still transient) so the PLAN nudge refresher
                    # in PreLLMCall doesn't strip it as its own nudge.
                    messages.append({"role": "user", "content": (
                        "[ORCHESTRATOR] (orchestrator control — not the human user) You tried to "
                        "conclude the PLAN turn without recording the "
                        "plan. Call todo_write(goal, items) with 3-7 scoped steps BEFORE your summary "
                        "— a prose plan is not usable for review or execution."
                    )})
                    _persist()
                    continue

                _emit(
                    event_emitter,
                    conv_folder,
                    RequestCompleted(
                        agent=agent.code,
                        final_content_summary=resp.content[:200],
                    ),
                )
                _persist()
                return _LoopOutcome(kind="final_answer", content=resp.content)
            # Subagent emitted NO tool_call. A specialist's only valid exit is
            # report_back, but small models routinely CONCLUDE IN PROSE instead of
            # emitting the tool_call (qwen3:14b did exactly this — wrote a full analysis
            # report as prose then never called report_back, conv b2701c32). We neither
            # inject a "[ORCHESTRATOR] must terminate" corrective (a role=user nudge makes
            # the model mistake orchestrator control for the user and spiral, conv
            # 9f428b47) NOR silently discard the work. Instead, treat a SUBSTANTIVE prose
            # turn as an IMPLICIT report_back so the analysis/diff is preserved.
            prose = (resp.content or "").strip()
            if prose:
                return _LoopOutcome(
                    kind="report_back",
                    sub_result=SubResult(
                        agent=agent.code,
                        summary=prose,
                        confidence="medium",  # implicit conclusion, not a self-asserted verdict
                    ),
                )
            # Truly empty turn (no content, no tool_call) : nothing to salvage. Retry a
            # couple of times (the model sees its own dangling turn), then abort low.
            no_tool_call_turns += 1
            if no_tool_call_turns > 2:
                return _LoopOutcome(
                    kind="aborted",
                    reason="subagent produced neither a tool_call nor any content",
                )
            _persist()
            continue

        # Process each tool_call sequentially.
        report_back_outcome: _LoopOutcome | None = None
        for call in resp.tool_calls:
            outcome = _handle_tool_call(
                call=call,
                conv_folder=conv_folder,
                agent=agent,
                messages=messages,
                state=state,
                tools_registry=tools_registry,
                llm_client=llm_client,
                hooks=hooks,
                dedup_cache=dedup_cache,
                is_main_agent=is_main_agent,
                ask_human_callback=ask_human_callback,
                agent_resolver=agent_resolver,
                event_emitter=event_emitter,
            )
            if outcome is not None:
                # Only `report_back` produces an outcome inside the per-call loop.
                report_back_outcome = outcome

        _persist()

        if report_back_outcome is not None:
            return report_back_outcome

    return _LoopOutcome(
        kind="aborted",
        reason=f"max_iterations reached ({max_iterations})",
    )


def _handle_tool_call(
    *,
    call: ToolCall,
    conv_folder: Path,
    agent: AgentSpec,
    messages: list[dict[str, Any]],
    state: ConversationState,
    tools_registry: dict[str, Any],
    llm_client: Any,
    hooks: HookRegistry,
    dedup_cache: dict[str, dict[str, Any]],
    is_main_agent: bool,
    ask_human_callback: AskHumanCallback | None,
    agent_resolver: AgentResolver | None,
    event_emitter: EventEmitter | None,
) -> _LoopOutcome | None:
    """Execute one tool_call. Returns a `_LoopOutcome` only for `report_back`.

    For every other call (native tool, delegate_to, ask_human, or denied),
    appends the result message and returns None.
    """
    # report_back is a subagent-only terminator.
    if call.name == "report_back":
        if is_main_agent:
            _append_tool_message(messages, call.name, {
                "error": "report_back_not_available_for_main_agent",
                "summary": (
                    "The main agent does not use report_back. Conclude by emitting "
                    "an assistant turn without tool_calls."
                ),
            })
            return None
        err = validate_report_back_args(call.arguments)
        if err is not None:
            _emit(
                event_emitter,
                conv_folder,
                HookFired(hook_name="OnReportBack", action="reject", reason=err),
            )
            _append_tool_message(messages, call.name, {
                "error": "invalid_report_back",
                "summary": err,
            })
            return None
        sub_result = SubResult(
            agent=agent.code,
            summary=call.arguments.get("summary", ""),
            files_produced=list(call.arguments.get("files_produced") or []),
            confidence=call.arguments.get("confidence", "high"),
            low_confidence_reason=call.arguments.get("low_confidence_reason", "") or "",
            suggested_todo_updates=list(call.arguments.get("suggested_todo_updates") or []),
        )
        return _LoopOutcome(kind="report_back", sub_result=sub_result)

    # ask_human is router-only.
    if call.name == "ask_human":
        if not is_main_agent or ask_human_callback is None:
            _append_tool_message(messages, call.name, {
                "error": "ask_human_not_available",
                "summary": (
                    "ask_human is only available to the main agent and requires a "
                    "callback. Subagents conclude via report_back(confidence='low', "
                    "low_confidence_reason='...') if they need clarification."
                ),
            })
            return None
        question = call.arguments.get("question", "")
        why = call.arguments.get("why", "")
        choices = [str(c) for c in (call.arguments.get("choices") or [])]
        multi = bool(call.arguments.get("multi"))
        try:
            answer = ask_human_callback(question, why, choices, multi)
        except Exception as exc:  # noqa: BLE001
            _append_tool_message(messages, call.name, {
                "error": "ask_human_failed",
                "summary": f"Callback raised: {exc}",
            })
            return None
        # The human reply is appended as a natural role=user message, not as
        # a tool result — it IS a user contribution.
        messages.append({"role": "user", "content": answer})
        # Capture it for a possible subagent resume (P5) : if a specialist blocked on
        # a human question, the router asks here, then re-delegates to the SAME agent —
        # which resumes its trace with this answer instead of starting fresh.
        state.pending_human_answer = answer
        return None

    # PreToolUse — grant/dedup/budget/depth checks (handled uniformly).
    ctx_tc = ToolCallContext(
        agent_code=agent.code,
        call=call,
        agent_grants=frozenset(agent.tool_grants) | _CONTROL_VERBS,
        delegation_targets=agent.delegation_targets,
    )
    decision = hooks.pre_tool_use(ctx_tc, state, dedup_cache)
    if decision.deny:
        _emit(
            event_emitter,
            conv_folder,
            HookFired(
                hook_name="PreToolUse",
                action="deny",
                reason=decision.reason or "",
            ),
        )
        _append_tool_message(messages, call.name, {
            "error": "denied",
            "summary": decision.reason or "denied",
        })
        return None

    # delegate_to → spawn subagent.
    if call.name == "delegate_to":
        if agent_resolver is None:
            _append_tool_message(messages, call.name, {
                "error": "no_agent_resolver",
                "summary": (
                    "delegate_to is not wired in this execution context "
                    "(no agent_resolver)."
                ),
            })
            return None

        target_code = call.arguments.get("agent_code", "")
        sub_agent = agent_resolver(target_code)
        if sub_agent is None:
            _append_tool_message(messages, call.name, {
                "error": "unknown_agent",
                "summary": f"agent_resolver returned None for '{target_code}'",
            })
            return None

        briefing = call.arguments.get("briefing", "")
        support_files = call.arguments.get("support_files") or []
        expected = call.arguments.get("expected", "")

        # P5 — RESUME vs fresh spawn. If this specialist previously blocked on a human
        # question (returned low + "HUMAN INPUT NEEDED:") and the router has since
        # relayed the human's answer, re-delegating to the SAME agent resumes its OWN
        # saved trace (subagent_<id>.json) with that answer — no fresh context, no lost
        # work. Each subagent owns its `subagent_<request_id>.json`; the router's
        # messages.json is never touched by a (sub)agent loop (is_main_agent=False).
        resume = bool(
            state.blocked_subagent_code == target_code
            and state.blocked_subagent_request_id
            and state.pending_human_answer is not None
        )

        # Validate support_files exist (workspace handoff artifact OR repo source)
        # BEFORE spawning — a phantom path otherwise sends the worker into a doomed
        # lookup (bug C). Skipped on resume : the agent re-reads its own trace (which
        # already holds its files), so the re-delegation's support_files are moot.
        if not resume:
            missing = _missing_support_files(conv_folder, support_files)
            if missing:
                _append_tool_message(messages, call.name, {
                    "error": "missing_support_file",
                    "summary": (
                        f"support_file(s) not found: {', '.join(missing)}. Reference only files a previous "
                        "specialist actually produced (report_back.files_produced) or real repo paths — do "
                        "not invent filenames."
                    ),
                })
                return None

        # Deliberation spawn helper (fresh-context critical-coder / sergent-kiss).
        # Validators only — invoked DOWNSTREAM on a concrete deliverable (cf. below) ;
        # never upstream to "pre-plan" (that drifts on small models).
        def _delib_spawn(agent_code: str, brief: str, sf_arg: Any = None) -> Any:
            spec = agent_resolver(agent_code)
            if spec is None:
                return None
            return spawn_subagent(
                conv_folder=conv_folder, sub_agent=spec, tools_registry=tools_registry,
                llm_client=llm_client, briefing=brief, support_files=list(sf_arg or []),
                expected="", parent_state=state, agent_resolver=agent_resolver,
                event_emitter=event_emitter, parent_agent_code=agent.code,
            )

        try:
            if resume:
                rid = state.blocked_subagent_request_id or ""
                answer = state.pending_human_answer or ""
                # Consume the round-trip state BEFORE running, so a second block re-arms it.
                state.blocked_subagent_code = None
                state.blocked_subagent_request_id = None
                state.pending_human_answer = None
                sub_result = resume_subagent(
                    conv_folder=conv_folder,
                    sub_agent=sub_agent,
                    request_id=rid,
                    human_answer=answer,
                    tools_registry=tools_registry,
                    llm_client=llm_client,
                    parent_state=state,
                    agent_resolver=agent_resolver,
                    event_emitter=event_emitter,
                    parent_agent_code=agent.code,
                )
            else:
                sub_result = spawn_subagent(
                    conv_folder=conv_folder,
                    sub_agent=sub_agent,
                    tools_registry=tools_registry,
                    llm_client=llm_client,
                    briefing=briefing,
                    support_files=list(support_files),
                    expected=str(expected) if expected else "",
                    parent_state=state,
                    agent_resolver=agent_resolver,
                    event_emitter=event_emitter,
                    parent_agent_code=agent.code,
                )
        except Exception as exc:  # noqa: BLE001
            _append_tool_message(messages, call.name, {
                "error": "subagent_crash",
                "summary": f"Subagent {target_code!r} raised: {exc}",
            })
            return None

        # P5 — if the specialist needs a human decision, it returns low + the marker.
        # Remember WHICH subagent (its trace id) so the next re-delegation resumes it.
        if (
            sub_result.confidence == "low"
            and sub_result.low_confidence_reason.strip().startswith(_HUMAN_INPUT_MARKER)
            and sub_result.request_id
        ):
            state.blocked_subagent_code = target_code
            state.blocked_subagent_request_id = sub_result.request_id

        # A1 — grounding : a code WORKER must actually CHANGE the repo. If it reports
        # success but the worktree is UNCHANGED, it described the edits instead of
        # applying them (conv 825fb5b3 : 9 "high" reports, empty diff, "✅ fait" mensonger)
        # → downgrade to low so the router re-delegates and demands real tool calls.
        # (Worktree diff is cumulative : only fires while NOTHING has been written yet —
        # exactly the "the whole turn produced nothing" failure we must never rubber-stamp.)
        code_diff: str | None = None
        if target_code in deliberation.CODE_WORKERS and _repo.worktree_root(conv_folder) is not None:
            code_diff = deliberation.current_diff(conv_folder)
            if sub_result.confidence in ("high", "medium") and not code_diff.strip():
                sub_result.confidence = "low"
                sub_result.low_confidence_reason = (
                    "You reported success but the repository is UNCHANGED — you described the "
                    "changes instead of applying them. Redo it and ACTUALLY modify the files via "
                    "repo_edit / repo_write / repo_exec."
                )

        result_payload = sub_result.to_dict()
        # DOWNSTREAM VALIDATION (important phases only) : the critics VALIDATE a
        # CONCRETE deliverable against the real repo (grounding/correctness/simplicity
        # + PASS/REWORK), they never pre-plan. code-runner → its diff ; code-analyst →
        # its analysis/audit report. The verdict rides on the result the router sees,
        # which handles any rework via its normal PDCA ACT.
        if deliberation.complexity_probe(briefing, support_files):
            kind, content = "", ""
            if target_code in deliberation.CODE_WORKERS:
                kind, content = "diff", (code_diff if code_diff is not None else deliberation.current_diff(conv_folder))
            elif target_code == "code-analyst":
                kind, content = "analysis report", (sub_result.summary or "")
            if kind and content.strip():
                try:
                    rev = deliberation.validate_deliverable(
                        spawn=_delib_spawn, task=briefing, kind=kind, content=content,
                        support_files=list(sub_result.files_produced or support_files),
                    )
                    if rev.verdict == "rework":
                        result_payload["kiss_review"] = {"verdict": "rework", "cuts": rev.critique}
                except Exception as exc:  # noqa: BLE001
                    _log.warning("validate_deliverable failed: %s", exc)
        # PostToolUse handles counters + cache.
        hooks.post_tool_use(
            call, result_payload, messages, state, dedup_cache, agent.code
        )
        _append_tool_message(messages, "delegate_to", result_payload)
        return None

    # Native registry tool.
    _emit(
        event_emitter,
        conv_folder,
        ToolCallStarted(
            agent=agent.code,
            tool_name=call.name,
            args_summary=_args_summary(call.arguments),
        ),
    )
    result = _execute_native_tool(call, tools_registry)
    _emit(
        event_emitter,
        conv_folder,
        ToolCallCompleted(
            tool_name=call.name,
            result_summary=_result_summary(result),
            duration_ms=0,  # tracking deferred to a later phase
        ),
    )

    hooks.post_tool_use(
        call, result, messages, state, dedup_cache, agent.code
    )
    _append_tool_message(messages, call.name, result)
    return None


# =============================================================================
# Public entry points
# =============================================================================


def run_main_loop(
    *,
    conv_folder: Path,
    agent: AgentSpec,
    tools_registry: dict[str, Any],
    llm_client: Any,
    user_text: str,
    images: list[str] | None = None,
    initial_messages: list[dict[str, Any]] | None = None,
    ask_human_callback: AskHumanCallback | None = None,
    agent_resolver: AgentResolver | None = None,
    event_emitter: EventEmitter | None = None,
    max_iterations: int = 50,
    plan_mode: bool = False,
) -> str:
    """Run the main agent loop on a deep request.

    Returns the user-facing answer string. The conversation is persisted
    incrementally to `conv_folder/messages.json`, `state.json`, `events.jsonl`.

    `initial_messages` is used by the CLI `--resume` flow to seed the loop
    with the previously-persisted history.

    `agent_resolver` is required if the agent's payload includes `delegate_to`
    (i.e. for any router or specialist). Without it, delegations fail
    structurally with `error=no_agent_resolver`.
    """
    conv_folder.mkdir(parents=True, exist_ok=True)

    if initial_messages is not None:
        messages = list(initial_messages)
    else:
        messages = [{"role": "system", "content": agent.system_prompt}]
    user_msg: dict[str, Any] = {"role": "user", "content": user_text}
    if images:
        user_msg["images"] = images  # transient vision input ; stripped on save
    messages.append(user_msg)

    state = ConversationState(depth_current=0, plan_mode=plan_mode)
    hooks = build_hook_registry(
        llm_client=llm_client, conv_folder=conv_folder, is_main_agent=True
    )
    dedup_cache: dict[str, dict[str, Any]] = {}
    tools_payload = _build_tools_payload(agent, tools_registry)
    _initialize_state(
        state, messages, tools_payload, agent.model or SUBAGENT_DEFAULT_MODEL
    )

    _emit(
        event_emitter,
        conv_folder,
        RequestStarted(
            agent=agent.code,
            depth=0,
            briefing_summary=user_text[:200],
        ),
    )

    outcome = _run_agent_loop(
        conv_folder=conv_folder,
        agent=agent,
        messages=messages,
        state=state,
        tools_registry=tools_registry,
        llm_client=llm_client,
        hooks=hooks,
        dedup_cache=dedup_cache,
        is_main_agent=True,
        max_iterations=max_iterations,
        ask_human_callback=ask_human_callback,
        agent_resolver=agent_resolver,
        event_emitter=event_emitter,
    )

    if outcome.kind == "final_answer":
        return outcome.content
    if outcome.kind == "aborted":
        return f"[Orchestrator aborted: {outcome.reason}]"
    # Should not happen — main agent doesn't emit report_back.
    return "[Orchestrator: unexpected outcome from main loop]"


def spawn_subagent(
    *,
    conv_folder: Path,
    sub_agent: AgentSpec,
    tools_registry: dict[str, Any],
    llm_client: Any,
    briefing: str,
    support_files: list[str],
    expected: str,
    parent_state: ConversationState,
    agent_resolver: AgentResolver | None = None,
    event_emitter: EventEmitter | None = None,
    parent_agent_code: str = "",
    max_iterations: int = 50,
) -> SubResult:
    """Spawn a subagent in an isolated `messages[]` context.

    The subagent runs until it emits `report_back`. The full sub_messages[]
    is persisted to `conv_folder/subagent_<request_id>.json` for audit.
    Returns the structured `SubResult`.

    Failure modes (returned as a low-confidence SubResult rather than raising) :
    - `report_back` validation rejected → loop continues with corrective msg ;
      if `max_iterations` is reached, returns confidence=low.
    - Subagent abort (LLM crash, etc.) → returns confidence=low with reason.
    """
    request_id = uuid.uuid4().hex[:12]

    # Build subagent messages[] from scratch.
    briefing_block = _format_subagent_briefing(briefing, support_files, expected)
    # CRP (P2): in code mode (a worktree exists) prepend a deterministically
    # assembled context packet so the worker executes context instead of
    # reconstructing it. Best-effort — a CRP failure never breaks delegation,
    # and it returns "" outside code mode (no worktree).
    try:
        _packet = context_packet.build_context_packet(
            conv_folder, briefing=briefing, support_files=list(support_files),
        )
    except Exception as _exc:  # noqa: BLE001
        _log.warning("context_packet failed for %s: %s", sub_agent.code, _exc)
        _packet = ""
    if _packet:
        briefing_block = f"{briefing_block}\n\n{_packet}"
    sub_messages: list[dict[str, Any]] = [
        {"role": "system", "content": sub_agent.system_prompt},
        {"role": "user", "content": briefing_block},
    ]

    return _run_subagent_on_messages(
        conv_folder=conv_folder,
        sub_agent=sub_agent,
        sub_messages=sub_messages,
        request_id=request_id,
        tools_registry=tools_registry,
        llm_client=llm_client,
        parent_state=parent_state,
        agent_resolver=agent_resolver,
        event_emitter=event_emitter,
        parent_agent_code=parent_agent_code,
        max_iterations=max_iterations,
    )


def _run_subagent_on_messages(
    *,
    conv_folder: Path,
    sub_agent: AgentSpec,
    sub_messages: list[dict[str, Any]],
    request_id: str,
    tools_registry: dict[str, Any],
    llm_client: Any,
    parent_state: ConversationState,
    agent_resolver: AgentResolver | None = None,
    event_emitter: EventEmitter | None = None,
    parent_agent_code: str = "",
    max_iterations: int = 50,
) -> SubResult:
    """Run a subagent loop on a prepared `sub_messages[]` (fresh OR resumed) and map
    its outcome to a `SubResult`. Shared by `spawn_subagent` (fresh context) and
    `resume_subagent` (the agent's own reloaded trace).

    The subagent always runs with `ask_human_callback=None` — it conveys a need for a
    human decision via `report_back(confidence='low', low_confidence_reason='HUMAN
    INPUT NEEDED: ...')`, and the router (the edge) does the asking, then resumes it.
    """
    # Fresh state for the subagent — depth incremented ; PLAN mode propagates so the
    # no-mutation gate applies to delegated specialists too (read-only exploration).
    sub_state = ConversationState(
        depth_current=parent_state.depth_current + 1,
        plan_mode=parent_state.plan_mode,
    )
    sub_hooks = build_hook_registry(
        llm_client=llm_client, conv_folder=conv_folder, is_main_agent=False
    )
    sub_dedup: dict[str, dict[str, Any]] = {}

    tools_payload = _build_tools_payload(sub_agent, tools_registry)
    _initialize_state(
        sub_state,
        sub_messages,
        tools_payload,
        sub_agent.model or SUBAGENT_DEFAULT_MODEL,
    )

    _emit(
        event_emitter,
        conv_folder,
        DelegationStarted(
            parent_agent=parent_agent_code,
            child_agent=sub_agent.code,
            depth=sub_state.depth_current,
            child_working_budget=sub_state.working_budget,
        ),
    )

    outcome = _run_agent_loop(
        conv_folder=conv_folder,
        agent=sub_agent,
        messages=sub_messages,
        state=sub_state,
        tools_registry=tools_registry,
        llm_client=llm_client,
        hooks=sub_hooks,
        dedup_cache=sub_dedup,
        is_main_agent=False,
        max_iterations=max_iterations,
        ask_human_callback=None,           # subagents have no ask_human
        agent_resolver=agent_resolver,     # propagated for nested delegation
        event_emitter=event_emitter,
    )

    # Persist the subagent's full messages[] for audit AND for a possible resume.
    try:
        save_sub_messages(conv_folder, request_id, sub_messages)
    except Exception as exc:  # noqa: BLE001
        _log.warning("save_sub_messages failed for %s: %s", sub_agent.code, exc)

    if outcome.kind == "report_back" and outcome.sub_result is not None:
        result = outcome.sub_result
    elif outcome.kind == "aborted":
        result = SubResult(
            agent=sub_agent.code,
            summary=f"Subagent aborted: {outcome.reason}",
            confidence="low",
            low_confidence_reason=outcome.reason or "subagent_aborted",
        )
    else:
        # Defensive : unexpected outcome → low-confidence fallback.
        result = SubResult(
            agent=sub_agent.code,
            summary="Subagent produced no usable result.",
            confidence="low",
            low_confidence_reason="unexpected_outcome",
        )
    result.request_id = request_id

    _emit(
        event_emitter,
        conv_folder,
        DelegationCompleted(
            child_agent=sub_agent.code,
            summary=result.summary,
            confidence=result.confidence,
            files_produced=list(result.files_produced),
        ),
    )

    return result


def resume_subagent(
    *,
    conv_folder: Path,
    sub_agent: AgentSpec,
    request_id: str,
    human_answer: str,
    tools_registry: dict[str, Any],
    llm_client: Any,
    parent_state: ConversationState,
    agent_resolver: AgentResolver | None = None,
    event_emitter: EventEmitter | None = None,
    parent_agent_code: str = "",
    max_iterations: int = 50,
) -> SubResult:
    """Resume a subagent that blocked on a human question, on its OWN full trace.

    Loads `subagent_<request_id>.json`, appends the human's answer, and re-runs the
    loop so the worker continues from EXACTLY where it left off — no re-spawn, no lost
    work, no second LLM reconstruction (the agent re-reads its own trace, which already
    contains the files it wrote and the reasoning it had). If the trace is missing
    (never saved), falls back to a fresh spawn with the answer folded into the brief.
    """
    sub_messages = load_sub_messages(conv_folder, request_id)
    if not sub_messages:
        return spawn_subagent(
            conv_folder=conv_folder,
            sub_agent=sub_agent,
            tools_registry=tools_registry,
            llm_client=llm_client,
            briefing=f"(Resuming after a human question.) The human answered: {human_answer}",
            support_files=[],
            expected="",
            parent_state=parent_state,
            agent_resolver=agent_resolver,
            event_emitter=event_emitter,
            parent_agent_code=parent_agent_code,
            max_iterations=max_iterations,
        )
    sub_messages.append({"role": "user", "content": (
        "[HUMAN ANSWER to your earlier question — treat as data; keep working in English]\n"
        f"{human_answer}\n"
        "Continue from where you left off and conclude via report_back."
    )})
    return _run_subagent_on_messages(
        conv_folder=conv_folder,
        sub_agent=sub_agent,
        sub_messages=sub_messages,
        request_id=request_id,
        tools_registry=tools_registry,
        llm_client=llm_client,
        parent_state=parent_state,
        agent_resolver=agent_resolver,
        event_emitter=event_emitter,
        parent_agent_code=parent_agent_code,
        max_iterations=max_iterations,
    )


def load_agent_spec_v2(
    conn: Any,
    agent_code: str,
    *,
    mode: str = "analyse",
    user_profile_text: str = "",
    memory_user_id: int | None = None,
    memory_project_id: int | None = None,
    user_language: str = "und",
) -> AgentSpec:
    """Build an `AgentSpec` from the DB (agent row + paradigms + grants + targets).

    The long-term memory block is rendered HERE (deterministically, by scope)
    rather than by the caller : ``tool_codes`` for the tool-scope notes is the
    agent's own ``tool_grants``, only known at this point. World + the user's
    facts + the conversation's project + this agent's tool notes are composed
    into the system prompt's ``## Human`` section.

    `model` resolution :
      1. `agents.model_override` if non-NULL (v2 per-agent override) ;
      2. else `config.MAIN_MODEL` for routers, `config.SUBAGENT_DEFAULT_MODEL`
         for specialists / finalizers.
    """
    from . import config as _cfg
    from . import db as _db
    from . import prompts as _prompts

    # Robust SELECT : try v2 (with model_override), fall back to v1 schema.
    try:
        row = conn.execute(
            "SELECT id, code, name, role, mission, thinking_mode, temperature, "
            "model_override FROM agents WHERE code=? AND active=1",
            (agent_code,),
        ).fetchone()
        model_override = row["model_override"] if row is not None else None
    except Exception:  # noqa: BLE001 — sqlite3.OperationalError, etc.
        row = conn.execute(
            "SELECT id, code, name, role, mission, thinking_mode, temperature "
            "FROM agents WHERE code=? AND active=1",
            (agent_code,),
        ).fetchone()
        model_override = None

    if row is None:
        raise KeyError(f"Unknown or inactive agent: {agent_code!r}")

    paradigms = _db.load_paradigms_for_agent(conn, row["id"], mode)
    tool_grants = frozenset(_db.load_tool_grants(conn, row["id"]))
    # Merge MCP tools granted to this agent (by category). No-op when MCP is
    # off/unconfigured. Single chokepoint → covers router AND subagents, and the
    # gate (PreToolUse) + the LLM tools payload both read tool_grants.
    from . import mcp_client
    tool_grants = tool_grants | mcp_client.get_manager().granted_tool_names_for(agent_code)
    # Tool-gated paradigms : a paradigm with `requires_tool` is injected ONLY when
    # the agent actually has a matching tool this session (generic mechanism — a
    # paradigm advising an MCP tool shows only when that tool is granted). Keeps
    # prompts honest — no advice about tools the agent can't call.
    paradigms = [
        p for p in paradigms
        if not p.requires_tool or any(t.startswith(p.requires_tool) for t in tool_grants)
    ]
    delegation_targets = frozenset(_db.load_delegation_targets(conn, row["id"]))

    # Load (code, role, mission) for each delegation target so the system
    # prompt can literally list the agent codes the LLM may pass to
    # `delegate_to`. Without this block, the LLM hallucinates target names or
    # gives up delegating altogether.
    delegation_targets_meta: list[tuple[str, str, str]] = []
    if delegation_targets:
        placeholders = ",".join("?" * len(delegation_targets))
        target_rows = conn.execute(
            f"SELECT code, role, mission FROM agents "  # noqa: S608 — placeholders generated above
            f"WHERE active=1 AND code IN ({placeholders}) ORDER BY code",
            tuple(sorted(delegation_targets)),
        ).fetchall()
        delegation_targets_meta = [
            (r["code"], r["role"], r["mission"]) for r in target_rows
        ]

    # Long-term memory : deterministic scope-driven inclusion. tool_codes = this
    # agent's grants → tool-scope notes load automatically for whoever holds the
    # tool (router AND subagents, since both pass through here).
    memory_block, _ = _prompts.render_memory_block(
        conn,
        user_id=memory_user_id,
        project_id=memory_project_id,
        tool_codes=tool_grants,
    )

    system_prompt = _prompts.render_system_prompt_v2(
        agent_code=row["code"],
        agent_name=row["name"],
        agent_role=row["role"],
        agent_mission=row["mission"],
        paradigms=paradigms,
        user_profile_text=user_profile_text,
        memory_block=memory_block,
        user_language=user_language,
        mode=mode,
        delegation_targets_meta=delegation_targets_meta,
    )

    # Resolve the model.
    if model_override:
        model = model_override
    elif row["role"] == "router":
        model = _cfg.MAIN_MODEL
    else:
        model = _cfg.SUBAGENT_DEFAULT_MODEL

    return AgentSpec(
        code=row["code"],
        role=row["role"],
        system_prompt=system_prompt,
        tool_grants=tool_grants,
        delegation_targets=delegation_targets,
        model=model,
        thinking=bool(row["thinking_mode"]),
        temperature=float(row["temperature"]),
    )


def _format_subagent_briefing(
    briefing: str, support_files: list[str], expected: str
) -> str:
    """Compose the user-message text passed to a subagent.

    Mirrors the legacy "## Inbound briefing" block but as a flat user
    message (the subagent's system prompt is supplied via `AgentSpec`).
    """
    parts: list[str] = []
    if expected:
        parts.append(f"## Expected\n{expected}\n")
    if support_files:
        files_block = "\n".join(f"- {p}" for p in support_files)
        parts.append(f"## support_files\n{files_block}\n")
    parts.append(f"## Briefing\n{briefing}")
    return "\n".join(parts)
