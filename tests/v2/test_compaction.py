"""Tests for `jeanmichel.compaction` — 4-level escalation."""

from __future__ import annotations

from jeanmichel.compaction import (
    _MICROCOMPACTABLE_TOOLS,
    compact_autocompact,
    compact_collapse,
    compact_microcompact,
    compact_snip,
    compute_working_ratio,
    escalate_compaction,
)
from jeanmichel.config import COMPACTION_THRESHOLDS
from jeanmichel.llm import MockClient
from jeanmichel.models import ConversationState, LLMResponse

# ---- Helpers --------------------------------------------------------------


def _msg(role: str, content: str = "", **kwargs):
    """Build a message dict."""
    out = {"role": role, "content": content, **kwargs}
    return out


def _state(working_budget: int = 10_000, system_reserve: int = 50) -> ConversationState:
    return ConversationState(
        system_reserve_tokens=system_reserve,
        output_reserve_tokens=2_000,
        working_budget=working_budget,
    )


def _build_messages_at_ratio(target_ratio: float, state: ConversationState, n_turns: int = 50):
    """Construct a messages[] that yields approximately `target_ratio` working usage.

    - system message at idx 0 (sized to match `state.system_reserve_tokens`).
    - `n_turns` user/assistant turns of equal size.
    """
    # System message of size ~system_reserve_tokens (rough)
    system_chars = max(4, (state.system_reserve_tokens - 4) * 4)
    messages = [_msg("system", "s" * system_chars)]

    target_used = int(state.working_budget * target_ratio)
    per_msg_tokens = max(2, target_used // n_turns)
    per_msg_chars = (per_msg_tokens - 4) * 4
    for i in range(n_turns):
        role = "assistant" if i % 2 == 0 else "user"
        messages.append(_msg(role, "x" * per_msg_chars))
    return messages


# ---- compute_working_ratio -----------------------------------------------


def test_compute_working_ratio_zero_when_no_budget():
    s = ConversationState(working_budget=0)
    assert compute_working_ratio([_msg("user", "x")], s) == 0.0


def test_compute_working_ratio_excludes_system_reserve():
    s = _state(working_budget=1_000, system_reserve=100)
    messages = [_msg("system", "s" * 400)]  # ~100 tokens
    # messages alone have ~104 tokens, minus 100 reserve = ~4 tokens used.
    # ratio = ~4 / 1000 = ~0.004
    r = compute_working_ratio(messages, s)
    assert 0.0 <= r < 0.1


# ---- Level 1 : Snip -------------------------------------------------------


def test_snip_drops_orchestrator_nudges_from_middle():
    s = _state()
    # Each assistant reply is long enough to avoid the "empty assistant" rule.
    long_reply = "this is a substantive assistant reply that has content"
    messages = [
        _msg("system", "sys"),
        _msg("user", "real user q"),
        _msg("assistant", long_reply, tool_calls=[]),
        _msg("user", "[ORCHESTRATOR] You've done N searches, persist now."),
        _msg("assistant", long_reply, tool_calls=[]),
        _msg("user", "follow up 1 with more text"),
        _msg("assistant", long_reply, tool_calls=[]),
        _msg("user", "follow up 2 with more text"),
        _msg("assistant", long_reply, tool_calls=[]),
    ]
    before = len(messages)
    compact_snip(messages, s)
    after = len(messages)
    # The synthetic ORCHESTRATOR nudge is gone — exactly 1 message removed.
    assert after == before - 1
    assert not any(
        m.get("content", "").startswith("[ORCHESTRATOR")
        for m in messages
    )


def test_snip_does_not_touch_system():
    s = _state()
    messages = [_msg("system", "sys")] + [_msg("user", "x") for _ in range(10)]
    compact_snip(messages, s)
    assert messages[0]["role"] == "system"


def test_snip_keeps_last_three_turns_even_if_low_value():
    s = _state()
    messages = [
        _msg("system", "sys"),
        _msg("assistant", "", tool_calls=[]),  # Empty middle → drop
        _msg("user", "[ORCHESTRATOR] nudge in middle"),  # drop
        _msg("user", "[ORCHESTRATOR] nudge in tail 1"),  # KEEP (last 3)
        _msg("user", "[ORCHESTRATOR] nudge in tail 2"),  # KEEP
        _msg("user", "[ORCHESTRATOR] nudge in tail 3"),  # KEEP
    ]
    compact_snip(messages, s)
    # System + 3 tail = 4
    assert len(messages) == 4
    assert messages[-1]["content"].startswith("[ORCHESTRATOR]")


def test_snip_no_op_on_short_messages():
    s = _state()
    messages = [
        _msg("system", "sys"),
        _msg("user", "hi"),
        _msg("assistant", "hello", tool_calls=[]),
    ]
    before = list(messages)
    compact_snip(messages, s)
    assert messages == before


def test_snip_preserves_report_back_returns():
    s = _state()
    messages = [
        _msg("system", "sys"),
        # An assistant turn with no tool calls and no content — normally dropped.
        _msg("assistant", "", tool_calls=[]),
        # A report_back return in the middle — MUST be preserved.
        _msg(
            "tool",
            content='{"summary":"x","confidence":"high"}',
            tool_name="report_back",
        ),
        _msg("user", "next q"),
        _msg("assistant", "answer", tool_calls=[]),
        _msg("user", "another"),
    ]
    compact_snip(messages, s)
    # The report_back tool message must still be there.
    report_backs = [m for m in messages if m.get("tool_name") == "report_back"]
    assert len(report_backs) == 1


# ---- Level 2 : Microcompact ----------------------------------------------


def test_microcompact_replaces_large_tool_results():
    s = _state()
    big_content = "RESULT " * 1000  # ~7000 chars ≈ 1750 tokens > 1500 threshold
    messages = [
        _msg("system", "sys"),
        _msg("user", "search"),
        _msg("assistant", "calling", tool_calls=[]),
        _msg("tool", big_content, tool_name="web_search"),
        _msg("user", "next 1"),
        _msg("user", "next 2"),
        _msg("user", "next 3"),
    ]
    compact_microcompact(messages, s)
    # The big web_search result is now a stub
    tool_msg = next(m for m in messages if m.get("tool_name") == "web_search")
    assert tool_msg["content"].startswith("[MICROCOMPACTED]")
    assert "web_search" in tool_msg["content"]


def test_microcompact_skips_small_results():
    s = _state()
    small = "ok"
    messages = [
        _msg("system", "sys"),
        _msg("tool", small, tool_name="web_search"),
        _msg("user", "u1"),
        _msg("user", "u2"),
        _msg("user", "u3"),
    ]
    compact_microcompact(messages, s)
    tool_msg = next(m for m in messages if m.get("tool_name") == "web_search")
    assert tool_msg["content"] == small


def test_microcompact_skips_non_microcompactable_tools():
    s = _state()
    big_content = "RESULT " * 1000
    messages = [
        _msg("system", "sys"),
        _msg("tool", big_content, tool_name="clock"),  # not microcompactable
        _msg("user", "u1"),
        _msg("user", "u2"),
        _msg("user", "u3"),
    ]
    compact_microcompact(messages, s)
    tool_msg = next(m for m in messages if m.get("tool_name") == "clock")
    assert tool_msg["content"] == big_content


def test_microcompact_preserves_last_three_tool_results():
    s = _state()
    big = "X" * 8000
    messages = [
        _msg("system", "sys"),
        _msg("tool", big, tool_name="web_search"),  # middle — compact
        _msg("user", "u"),
        _msg("tool", big, tool_name="web_search"),  # last 3 — preserve
        _msg("user", "u2"),
        _msg("tool", big, tool_name="web_search"),  # last 3 — preserve
    ]
    compact_microcompact(messages, s)
    tools_idx = [i for i, m in enumerate(messages) if m.get("tool_name") == "web_search"]
    # First (middle) → compacted
    assert messages[tools_idx[0]]["content"].startswith("[MICROCOMPACTED]")
    # Last two (in tail) → untouched
    assert messages[tools_idx[1]]["content"] == big
    assert messages[tools_idx[2]]["content"] == big


def test_microcompactable_tools_set_is_what_we_expect():
    # Defensive: make sure we don't accidentally microcompact a critical tool
    # like clock or workspace_create_file.
    assert "web_search" in _MICROCOMPACTABLE_TOOLS
    assert "wikipedia_get_page" in _MICROCOMPACTABLE_TOOLS
    assert "workspace_view" in _MICROCOMPACTABLE_TOOLS
    assert "clock" not in _MICROCOMPACTABLE_TOOLS
    assert "workspace_create_file" not in _MICROCOMPACTABLE_TOOLS


# ---- Level 3 : Context Collapse -------------------------------------------


def test_collapse_uses_compactor_llm_and_preserves_report_back():
    s = _state()
    summary_resp = LLMResponse(thinking="", content="middle was about X and Y")
    mock = MockClient(script=[summary_resp])

    # 10 messages in the middle (turns 1..8 between system and tail of 5)
    # Add a report_back return in the middle — must be preserved.
    messages = [
        _msg("system", "sys"),
        _msg("user", "u1"),
        _msg("assistant", "a1", tool_calls=[]),
        _msg("user", "u2"),
        _msg("tool", '{"summary":"important finding"}', tool_name="report_back"),
        _msg("user", "u3"),
        _msg("assistant", "a3", tool_calls=[]),
        _msg("user", "u-tail-1"),
        _msg("assistant", "a-tail-1", tool_calls=[]),
        _msg("user", "u-tail-2"),
        _msg("assistant", "a-tail-2", tool_calls=[]),
        _msg("user", "u-tail-3"),
    ]
    n_before = len(messages)

    compact_collapse(messages, s, mock)

    # Structure : system + collapsed + report_back + tail_5
    assert messages[0]["role"] == "system"
    assert messages[1]["content"].startswith("[ORCHESTRATOR CONTEXT COLLAPSE]")
    assert "middle was about X and Y" in messages[1]["content"]
    # report_back return survives
    report_backs = [m for m in messages if m.get("tool_name") == "report_back"]
    assert len(report_backs) == 1
    # Tail preserved verbatim
    assert messages[-1]["content"] == "u-tail-3"
    # Overall length reduced
    assert len(messages) < n_before


def test_collapse_no_op_on_short_message_history():
    s = _state()
    mock = MockClient(script=[])  # would raise if called
    messages = [_msg("system", "s"), _msg("user", "u")]
    compact_collapse(messages, s, mock)
    assert len(messages) == 2  # untouched


def test_collapse_handles_llm_failure_gracefully():
    s = _state()
    mock = MockClient(script=[])  # exhausted → RuntimeError

    messages = (
        [_msg("system", "s")]
        + [_msg("user", f"u{i}") for i in range(10)]
        + [_msg("user", f"tail{i}") for i in range(5)]
    )
    before = list(messages)
    # Should not raise — just log and bail.
    compact_collapse(messages, s, mock)
    assert messages == before  # untouched


# ---- Level 4 : Autocompact ------------------------------------------------


def test_autocompact_keeps_only_system_and_last_two_turns():
    s = _state()
    summary_resp = LLMResponse(thinking="", content="brief: user wanted X, got Y")
    mock = MockClient(script=[summary_resp])

    messages = (
        [_msg("system", "s")]
        + [_msg("user", f"u{i}") for i in range(20)]
        + [_msg("user", "almost-last")]
        + [_msg("user", "last")]
    )

    compact_autocompact(messages, s, mock)

    # Structure : system + autocompact + last_2 = 4 messages
    assert len(messages) == 4
    assert messages[0]["role"] == "system"
    assert messages[1]["content"].startswith("[ORCHESTRATOR AUTOCOMPACT]")
    assert "brief: user wanted X" in messages[1]["content"]
    assert messages[-1]["content"] == "last"
    assert messages[-2]["content"] == "almost-last"


def test_autocompact_synthetic_fallback_on_llm_failure():
    s = _state()
    mock = MockClient(script=[])  # exhausted

    messages = [_msg("system", "s")] + [_msg("user", f"u{i}") for i in range(10)]

    compact_autocompact(messages, s, mock)

    # Even on LLM failure, autocompact produces a synthetic fallback so the
    # loop never gets stuck waiting for a summary that won't come.
    assert messages[1]["content"].startswith("[ORCHESTRATOR AUTOCOMPACT]")
    assert "compaction failed" in messages[1]["content"].lower()


# ---- Escalation entry point ----------------------------------------------


def test_escalate_noop_below_lowest_threshold():
    s = _state()
    messages = _build_messages_at_ratio(0.50, s)  # below 0.70
    n_before = len(messages)
    level = escalate_compaction(messages, s)
    assert level == 0
    assert len(messages) == n_before


def test_escalate_at_75_percent_triggers_snip():
    s = _state()
    messages = _build_messages_at_ratio(0.75, s)
    # Add a synthetic nudge so Snip has something to remove.
    messages.insert(
        len(messages) // 2,
        _msg("user", "[ORCHESTRATOR] persist your findings"),
    )
    level = escalate_compaction(messages, s)
    assert level >= 1
    # No microcompactable tool results, no LLM client → stops at level 1 (or 2 if microcompact ran trivially).
    assert level <= 2


def test_escalate_at_85_percent_includes_microcompact():
    """DoD : at ~85 % of WORKING, the escalade goes through at least Microcompact."""
    s = _state()
    messages = _build_messages_at_ratio(0.85, s)
    level = escalate_compaction(messages, s)
    # 85 % > t1 (0.70) and > t2 (0.80) → at least level 2 triggered.
    # 85 % < t3 (0.90) → does not need to climb to Collapse.
    assert level >= 2


def test_escalate_at_92_percent_triggers_collapse_with_llm():
    """At ~92 % of WORKING, Context Collapse must fire (LLM call)."""
    s = _state()
    messages = _build_messages_at_ratio(0.92, s)
    mock = MockClient(script=[
        LLMResponse(thinking="", content="collapsed summary of middle"),
    ])
    level = escalate_compaction(messages, s, llm_client=mock)
    assert level >= 3
    # Compactor was called
    assert len(mock.calls_v2) >= 1


def test_escalate_at_97_percent_can_reach_collapse_or_autocompact():
    """At ~97 % of WORKING (>= t4 = 0.95) the escalade goes deep.

    In practice level 3 (Collapse) often frees enough to stop before
    Autocompact — that's by design (cheaper is better). We assert the
    escalade reached at least level 3.
    """
    s = _state()
    messages = _build_messages_at_ratio(0.97, s)
    mock = MockClient(script=[
        # Provide enough scripted responses for both collapse and a possible autocompact.
        LLMResponse(thinking="", content="collapsed summary"),
        LLMResponse(thinking="", content="autocompact summary"),
    ])
    level = escalate_compaction(messages, s, llm_client=mock)
    assert level >= 3
    assert len(mock.calls_v2) >= 1


def test_escalate_reaches_level_4_when_collapse_insufficient():
    """When Context Collapse's output is still huge, escalade climbs to Autocompact."""
    s = _state()
    messages = _build_messages_at_ratio(0.97, s)
    # Collapse produces a huge output (won't actually free enough WORKING).
    huge_summary = "x" * 200_000  # ~50_000 tokens — way more than the budget
    mock = MockClient(script=[
        LLMResponse(thinking="", content=huge_summary),
        LLMResponse(thinking="", content="brief autocompact"),
    ])
    level = escalate_compaction(messages, s, llm_client=mock)
    assert level == 4
    assert len(mock.calls_v2) == 2


def test_escalate_thresholds_match_config():
    # Sanity : the 4 thresholds defined in config.py are the right shape.
    assert COMPACTION_THRESHOLDS == (0.70, 0.80, 0.90, 0.95)


def test_escalate_no_llm_caps_at_level_2():
    """Without an LLM client, the escalade cannot run Collapse/Autocompact."""
    s = _state()
    messages = _build_messages_at_ratio(0.95, s)  # would normally trigger level 4
    level = escalate_compaction(messages, s, llm_client=None)
    assert level <= 2
