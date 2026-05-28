"""Token estimation utilities.

Used by the budget partitioning logic (cf. §1.7 doc 06) and the compaction
escalation (cf. §7 doc 06). The orchestrator needs to estimate token counts
*before* an LLM call to decide whether to compact — Ollama's own
`prompt_eval_count` is only available *after* the call.

Strategy : a simple heuristic of 4 chars ≈ 1 token. Accurate enough for
threshold decisions (we're comparing usage to budget at the 5-10 % bucket
level, not counting individual tokens). When a model returns its own
`prompt_eval_count`, we can recalibrate per-call.
"""

from __future__ import annotations

from typing import Any

# Rough chars-per-token ratio. Sane for English/French prose with code mixed in.
# Tighter estimators would need a per-model tokenizer, which we avoid for now
# to keep the foundation light and tokenizer-agnostic.
_CHARS_PER_TOKEN = 4


def estimate_text_tokens(text: str | None) -> int:
    """Estimate token count for a single string."""
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate token count for an Ollama-shaped messages array.

    Counts the `content` field of each message plus structural overhead for
    role markers and tool_call descriptions. The overhead constants are
    deliberate over-estimates so the budget decision errs on the safe side
    (trigger compaction slightly early rather than too late).
    """
    if not messages:
        return 0
    total = 0
    for msg in messages:
        # Per-message overhead (role, separators) — small constant.
        total += 4
        content = msg.get("content") or ""
        total += estimate_text_tokens(content)
        # Tool calls add structural tokens.
        tool_calls = msg.get("tool_calls") or []
        for tc in tool_calls:
            total += 8  # function name, brackets, etc.
            fn = tc.get("function") if isinstance(tc, dict) else None
            if fn:
                total += estimate_text_tokens(fn.get("name", ""))
                args = fn.get("arguments") or {}
                # Crude: count each value's string length.
                for v in (args.values() if isinstance(args, dict) else []):
                    total += estimate_text_tokens(str(v))
        # Tool messages have a tool_name field.
        if msg.get("role") == "tool":
            total += estimate_text_tokens(msg.get("tool_name", ""))
    return total


def estimate_tools_payload_tokens(tools: list[dict[str, Any]]) -> int:
    """Estimate token count for an Ollama tools payload (function schemas).

    Each tool definition includes name + description + parameter schema.
    Tools live in the system reserve (immutable for the turn), not in WORKING.
    """
    if not tools:
        return 0
    total = 0
    for t in tools:
        fn = t.get("function") if isinstance(t, dict) else None
        if not fn:
            continue
        total += estimate_text_tokens(fn.get("name", ""))
        total += estimate_text_tokens(fn.get("description", ""))
        # Parameter schema is roughly proportional to JSON-encoded size.
        params = fn.get("parameters") or {}
        # Use the JSON string length as a proxy.
        import json
        total += estimate_text_tokens(json.dumps(params, ensure_ascii=False))
    return total
