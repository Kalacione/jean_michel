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

from .config import (
    DEFAULT_MODEL_CONTEXT_WINDOW,
    OUTPUT_RESERVE_RATIO,
    SUBAGENT_BUDGET_RATIO,  # noqa: F401 — reserved for later use
    SUBAGENT_DEFAULT_MODEL,
    model_context_window,
)
from .events import (
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
from .persistence import append_event, save_messages, save_state, save_sub_messages
from .tokens import estimate_messages_tokens, estimate_tools_payload_tokens
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
            "Pause the loop and ask the human for clarification. Use sparingly — "
            "only when an ambiguity actually blocks progress. The `why` field is "
            "mandatory and must explain what is blocked without the answer. "
            "Subagents do NOT have access to this tool ; they conclude with "
            "report_back(confidence='low', low_confidence_reason='...') instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "why": {"type": "string"},
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

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "agent": self.agent,
            "summary": self.summary,
            "files_produced": list(self.files_produced),
            "confidence": self.confidence,
        }
        if self.confidence == "low" and self.low_confidence_reason:
            out["low_confidence_reason"] = self.low_confidence_reason
        return out


# Optional callbacks the loop may invoke.
EventEmitter = Callable[[Any], None]
AskHumanCallback = Callable[[str, str], str]      # (question, why) → answer
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

    for iteration in range(max_iterations):
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

        try:
            resp = llm_client.chat_messages(
                messages=messages,
                tools=tools_payload,
                temperature=agent.temperature,
                thinking=agent.thinking,
                model=model,
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

        _append_assistant_turn(messages, resp)

        # Termination — main agent : empty tool_calls is the final answer.
        if not resp.tool_calls:
            if is_main_agent:
                _emit(
                    event_emitter,
                    conv_folder,
                    RequestCompleted(
                        agent=agent.code,
                        final_content_summary=resp.content[:200],
                    ),
                )
                save_messages(conv_folder, messages)
                save_state(conv_folder, state)
                return _LoopOutcome(kind="final_answer", content=resp.content)
            # Subagent emitted no tool_call → it MUST terminate via report_back.
            # Inject a corrective user message and continue.
            messages.append({
                "role": "user",
                "content": (
                    "[ORCHESTRATOR] You must terminate via report_back. "
                    "Re-emit your conclusion using report_back(summary, "
                    "files_produced, confidence, low_confidence_reason?)."
                ),
            })
            save_messages(conv_folder, messages)
            save_state(conv_folder, state)
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

        save_messages(conv_folder, messages)
        save_state(conv_folder, state)

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
        try:
            answer = ask_human_callback(question, why)
        except Exception as exc:  # noqa: BLE001
            _append_tool_message(messages, call.name, {
                "error": "ask_human_failed",
                "summary": f"Callback raised: {exc}",
            })
            return None
        # The human reply is appended as a natural role=user message, not as
        # a tool result — it IS a user contribution.
        messages.append({"role": "user", "content": answer})
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

        try:
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

        result_payload = sub_result.to_dict()
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
    initial_messages: list[dict[str, Any]] | None = None,
    ask_human_callback: AskHumanCallback | None = None,
    agent_resolver: AgentResolver | None = None,
    event_emitter: EventEmitter | None = None,
    max_iterations: int = 50,
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
    messages.append({"role": "user", "content": user_text})

    state = ConversationState(depth_current=0)
    hooks = build_hook_registry(llm_client=llm_client)
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
    sub_messages: list[dict[str, Any]] = [
        {"role": "system", "content": sub_agent.system_prompt},
        {"role": "user", "content": briefing_block},
    ]

    # Fresh state for the subagent — depth incremented.
    sub_state = ConversationState(
        depth_current=parent_state.depth_current + 1,
    )
    sub_hooks = build_hook_registry(llm_client=llm_client)
    sub_dedup: dict[str, dict[str, Any]] = {}

    tools_payload = _build_tools_payload(sub_agent, tools_registry)
    _initialize_state(
        sub_state,
        sub_messages,
        tools_payload,
        sub_agent.model or SUBAGENT_DEFAULT_MODEL,
    )

    # Allocate a fraction of the parent's working budget to the subagent.
    # NOTE : the budget is per-call context (each subagent has its own).
    # We pass the ratio just to be explicit ; the partitioning is recomputed
    # from the model's own context window in _initialize_state.

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

    # Persist the subagent's full messages[] for audit, regardless of outcome.
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
