"""4-level compaction escalation on the WORKING context (cf. §7 doc 06).

Each call to `escalate_compaction(messages, state, llm_client)` evaluates the
current `working_tokens_used / working_budget` ratio against the four
thresholds and applies the appropriate level (and all lower levels) :

    ratio < 0.70  → no-op
    0.70 ≤ ratio  → level 1 : Snip (deterministic, no LLM)
    0.80 ≤ ratio  → level 1 + 2 : Snip + Microcompact (deterministic)
    0.90 ≤ ratio  → levels 1+2+3 : Snip + Microcompact + Context Collapse (LLM)
    0.95 ≤ ratio  → levels 1+2+3+4 : ... + Autocompact (LLM, last resort)

After each level, the ratio is recomputed. If a cheaper level freed enough
space, the expensive ones are skipped.

The protected indices (system prompt, last N turns, report_back returns) are
never touched, so the most recent context is preserved across all levels.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import (
    COMPACTION_THRESHOLDS,
    COMPACTOR_MODEL,
    MICROCOMPACT_TOKEN_THRESHOLD,
)
from .models import ConversationState
from .tokens import estimate_messages_tokens, estimate_text_tokens

_log = logging.getLogger(__name__)


# ---- Constants ------------------------------------------------------------

# Tool results that are safely microcompactable (large + recomputable from
# disk via a workspace file or a re-call of the same tool).
_MICROCOMPACTABLE_TOOLS: frozenset[str] = frozenset({
    "web_search",
    "wikipedia_search",
    "wikipedia_get_page",
    "wikipedia_fetch",
    "workspace_view",
})

# Last N turns kept untouched by Snip / Microcompact (the "fresh tail").
_SNIP_TAIL_KEEP = 3
# Last N turns kept by Context Collapse (slightly larger tail because the
# collapse is more aggressive on the middle).
_COLLAPSE_TAIL_KEEP = 5
# Last N turns kept by Autocompact (absolute minimum).
_AUTOCOMPACT_TAIL_KEEP = 2


# ---- Helpers --------------------------------------------------------------


def _is_orchestrator_nudge(msg: dict[str, Any]) -> bool:
    if msg.get("role") != "user":
        return False
    content = msg.get("content") or ""
    return isinstance(content, str) and content.lstrip().startswith("[ORCHESTRATOR")


def _is_empty_assistant(msg: dict[str, Any]) -> bool:
    if msg.get("role") != "assistant":
        return False
    content = (msg.get("content") or "").strip()
    tool_calls = msg.get("tool_calls") or []
    return not tool_calls and len(content) < 20


def _is_report_back_return(msg: dict[str, Any]) -> bool:
    return msg.get("role") == "tool" and msg.get("tool_name") == "report_back"


def _protected_indices(messages: list[dict[str, Any]], tail_keep: int) -> set[int]:
    """Indices never touched : system (idx 0), last `tail_keep` turns,
    and every report_back return found in the middle."""
    n = len(messages)
    protected = set()
    if n > 0:
        protected.add(0)
    for i in range(max(0, n - tail_keep), n):
        protected.add(i)
    for i, msg in enumerate(messages):
        if _is_report_back_return(msg):
            protected.add(i)
    return protected


def _microcompact_stub(tool_name: str, original_tokens: int, args_hint: str = "") -> str:
    """Stub text replacing a large tool content."""
    args = f" ({args_hint})" if args_hint else ""
    return (
        f"[MICROCOMPACTED] {tool_name}{args} previously returned "
        f"~{original_tokens} tokens. Re-call the tool or read the workspace "
        f"file if the content is needed."
    )


def _render_messages_as_text(messages: list[dict[str, Any]]) -> str:
    """Compact human-readable rendering of a messages slice, for LLM compaction."""
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "?")
        if role == "tool":
            tool_name = msg.get("tool_name", "?")
            content = msg.get("content", "")
            lines.append(f"[tool:{tool_name}] {content}")
        elif role == "assistant":
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                tc_summary = ", ".join(
                    (tc.get("function") or {}).get("name", "?")
                    for tc in tool_calls
                )
                lines.append(f"[assistant calls: {tc_summary}] {content}")
            else:
                lines.append(f"[assistant] {content}")
        else:
            lines.append(f"[{role}] {msg.get('content', '')}")
    return "\n".join(lines)


def compute_working_ratio(messages: list[dict[str, Any]], state: ConversationState) -> float:
    """Estimate the current `working_tokens_used / working_budget` ratio.

    Recomputed from `messages` directly (not from `state.working_tokens_used`
    which may be stale) — minus the immutable system reserve.
    """
    if state.working_budget <= 0:
        return 0.0
    total = estimate_messages_tokens(messages)
    used = max(0, total - state.system_reserve_tokens)
    return used / state.working_budget


def _sync_state(messages: list[dict[str, Any]], state: ConversationState) -> None:
    """Recompute `state.working_tokens_used` from `messages`."""
    total = estimate_messages_tokens(messages)
    state.working_tokens_used = max(0, total - state.system_reserve_tokens)


# ---- Level 1 : Snip -------------------------------------------------------


def compact_snip(messages: list[dict[str, Any]], state: ConversationState) -> None:
    """Drop messages with null informational value going forward.

    Mutates `messages` in place. Never touches protected indices.
    """
    if len(messages) <= _SNIP_TAIL_KEEP + 1:
        return

    protected = _protected_indices(messages, _SNIP_TAIL_KEEP)
    new_messages = []
    for i, msg in enumerate(messages):
        if i in protected:
            new_messages.append(msg)
            continue
        if _is_orchestrator_nudge(msg):
            continue  # Drop — the LLM has already seen and reacted (or ignored)
        if _is_empty_assistant(msg):
            continue  # Drop — empty thinking turn
        new_messages.append(msg)

    messages[:] = new_messages
    _sync_state(messages, state)


# ---- Level 2 : Microcompact -----------------------------------------------


def compact_microcompact(
    messages: list[dict[str, Any]],
    state: ConversationState,
    threshold_tokens: int | None = None,
) -> None:
    """Replace large tool results with a stub when the tool is microcompactable.

    Mutates `messages` in place. Replacement is non-destructive at the array
    level — the message structure stays (role, tool_name) so the LLM still
    sees that the tool was called.
    """
    if threshold_tokens is None:
        threshold_tokens = MICROCOMPACT_TOKEN_THRESHOLD

    if len(messages) <= _SNIP_TAIL_KEEP + 1:
        return

    protected = _protected_indices(messages, _SNIP_TAIL_KEEP)
    for i, msg in enumerate(messages):
        if i in protected:
            continue
        if msg.get("role") != "tool":
            continue
        tool_name = msg.get("tool_name", "")
        if tool_name not in _MICROCOMPACTABLE_TOOLS:
            continue
        content = msg.get("content") or ""
        if not isinstance(content, str):
            continue
        tokens = estimate_text_tokens(content)
        if tokens <= threshold_tokens:
            continue
        msg["content"] = _microcompact_stub(tool_name, tokens)

    _sync_state(messages, state)


# ---- Level 3 : Context Collapse (LLM call) -------------------------------


_COLLAPSE_SYSTEM_PROMPT = (
    "You compact a slice of a conversation between an assistant and its tools. "
    "Produce a concise summary (~500 tokens) that preserves: workspace file "
    "paths, key conclusions, sub-tasks completed, blockers encountered. "
    "Discard: verbose tool outputs, intermediate reasoning. Output prose only, "
    "no JSON, no markdown headers."
)


def compact_collapse(
    messages: list[dict[str, Any]],
    state: ConversationState,
    llm_client: Any,
    compactor_model: str | None = None,
) -> None:
    """Summarize the middle window via a targeted LLM call.

    Keeps: system (idx 0), last `_COLLAPSE_TAIL_KEEP` turns, and every
    report_back return from the middle. Compacts the rest into a single
    role=user message tagged `[ORCHESTRATOR CONTEXT COLLAPSE]`.

    Mutates `messages` in place. Requires `llm_client` (an LLMClientV2).
    """
    if len(messages) <= _COLLAPSE_TAIL_KEEP + 2:
        return

    n = len(messages)
    head = messages[0]
    tail = messages[-_COLLAPSE_TAIL_KEEP:]
    middle = messages[1:-_COLLAPSE_TAIL_KEEP]

    # Preserve report_back returns from the middle
    preserved_returns: list[dict[str, Any]] = []
    middle_to_collapse: list[dict[str, Any]] = []
    for msg in middle:
        if _is_report_back_return(msg):
            preserved_returns.append(msg)
        else:
            middle_to_collapse.append(msg)

    if not middle_to_collapse:
        return  # Nothing left to compact

    middle_text = _render_messages_as_text(middle_to_collapse)
    model = compactor_model or COMPACTOR_MODEL

    try:
        resp = llm_client.chat_messages(
            messages=[
                {"role": "system", "content": _COLLAPSE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Summarize the following conversation segment:\n\n"
                        f"{middle_text}"
                    ),
                },
            ],
            tools=[],
            temperature=0.0,
            thinking=False,
            model=model,
        )
        summary_text = resp.content.strip()
    except Exception as exc:  # noqa: BLE001
        _log.warning("Context Collapse LLM call failed: %s — keeping messages intact", exc)
        return

    if not summary_text:
        _log.warning("Context Collapse produced an empty summary — keeping messages intact")
        return

    collapsed_msg = {
        "role": "user",
        "content": (
            "[ORCHESTRATOR CONTEXT COLLAPSE]\n"
            f"{summary_text}\n"
            "[END COMPACTED]"
        ),
    }

    messages[:] = [head, collapsed_msg, *preserved_returns, *tail]
    _sync_state(messages, state)


# ---- Level 4 : Autocompact (last resort, LLM call) -----------------------


_AUTOCOMPACT_SYSTEM_PROMPT = (
    "You produce a minimal context summary when a conversation must be "
    "drastically compressed. Output at most ~1500 tokens. Preserve only : "
    "the user's original goal, any workspace files produced, the most "
    "recent conclusions. Everything else is dropped. Output prose only."
)


def compact_autocompact(
    messages: list[dict[str, Any]],
    state: ConversationState,
    llm_client: Any,
    compactor_model: str | None = None,
) -> None:
    """Last-resort summarization of the entire middle history.

    Keeps: system + last `_AUTOCOMPACT_TAIL_KEEP` turns. Compacts the rest
    (including report_back returns) into a single message.

    Mutates `messages` in place. Always produces something (LLM failure →
    falls back to a synthetic notice that lets the loop continue).
    """
    if len(messages) <= _AUTOCOMPACT_TAIL_KEEP + 1:
        return

    head = messages[0]
    tail = messages[-_AUTOCOMPACT_TAIL_KEEP:]
    middle = messages[1:-_AUTOCOMPACT_TAIL_KEEP]

    if not middle:
        return

    middle_text = _render_messages_as_text(middle)
    model = compactor_model or COMPACTOR_MODEL

    summary_text = ""
    try:
        resp = llm_client.chat_messages(
            messages=[
                {"role": "system", "content": _AUTOCOMPACT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Compress this conversation history into the briefest "
                        "useful summary:\n\n"
                        f"{middle_text}"
                    ),
                },
            ],
            tools=[],
            temperature=0.0,
            thinking=False,
            model=model,
        )
        summary_text = resp.content.strip()
    except Exception as exc:  # noqa: BLE001
        _log.warning("Autocompact LLM call failed: %s — synthetic fallback", exc)

    if not summary_text:
        # Graceful degradation : at least produce something so the loop
        # doesn't stall waiting for a usable context.
        summary_text = (
            "(Context compaction failed. Earlier turns dropped to recover "
            "working budget. Proceed with the most recent context only.)"
        )

    autocompact_msg = {
        "role": "user",
        "content": (
            "[ORCHESTRATOR AUTOCOMPACT]\n"
            f"{summary_text}\n"
            "[END COMPACTED]"
        ),
    }

    messages[:] = [head, autocompact_msg, *tail]
    _sync_state(messages, state)


# ---- Escalation entry point ----------------------------------------------


def escalate_compaction(
    messages: list[dict[str, Any]],
    state: ConversationState,
    llm_client: Any | None = None,
) -> int:
    """Run the appropriate compaction level(s) given the current WORKING ratio.

    Returns the highest level triggered (0 = no-op, 1 = Snip, 2 = Microcompact,
    3 = Context Collapse, 4 = Autocompact). The orchestrator uses this return
    to emit a `WorkingBudgetUpdate` event (cf. §6 bis doc 06).
    """
    if state.working_budget <= 0:
        return 0

    t1, t2, t3, t4 = COMPACTION_THRESHOLDS

    ratio = compute_working_ratio(messages, state)
    if ratio < t1:
        return 0

    level = 1
    compact_snip(messages, state)
    ratio = compute_working_ratio(messages, state)
    if ratio < t2:
        return level

    level = 2
    compact_microcompact(messages, state)
    ratio = compute_working_ratio(messages, state)
    if ratio < t3:
        return level

    if llm_client is None:
        # Cannot escalate further without an LLM. Return whatever we did.
        return level

    level = 3
    compact_collapse(messages, state, llm_client)
    ratio = compute_working_ratio(messages, state)
    if ratio < t4:
        return level

    level = 4
    compact_autocompact(messages, state, llm_client)
    return level
