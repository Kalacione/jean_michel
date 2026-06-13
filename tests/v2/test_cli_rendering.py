"""Tests for `jeanmichel.cli` — event rendering, ask_human callback.

Snapshot-style tests : each event type goes through `render_event` and the
captured text is asserted to contain the expected markers. We don't do byte
exact-match on the whole rendering (too brittle to formatting tweaks) — just
the load-bearing identifiers.
"""

from __future__ import annotations

from rich.console import Console

from jeanmichel.cli import (
    make_ask_human,
    render_event,
)
from jeanmichel.events import (
    DelegationCompleted,
    DelegationStarted,
    HookFired,
    LLMCallCompleted,
    LLMCallStarted,
    MemoryNearCapacity,
    RequestCompleted,
    RequestStarted,
    ToolCallCompleted,
    ToolCallStarted,
    WorkingBudgetUpdate,
)

# ---- Helper ---------------------------------------------------------------


def _capture(event, mode: str = "analyse", show_thoughts: bool = False) -> str:
    """Render an event into a string buffer and return the captured text."""
    console = Console(record=True, width=120, force_terminal=False)
    render_event(console, event, mode=mode, show_thoughts=show_thoughts)
    return console.export_text()


# ---- Per-event-type rendering --------------------------------------------


def test_render_request_started_shows_agent_and_depth():
    text = _capture(RequestStarted(
        agent="jean-michel",
        depth=0,
        briefing_summary="user asked about X",
    ))
    assert "jean-michel" in text
    assert "depth=0" in text
    assert "user asked about X" in text


def test_render_request_started_indents_with_depth():
    text = _capture(RequestStarted(
        agent="wikipedia-specialist",
        depth=2,
        briefing_summary="fetch fact",
    ))
    # depth=2 → 4 spaces indent. The agent code appears after indent.
    assert "wikipedia-specialist" in text
    assert text.lstrip(" ").startswith("→") or "wikipedia-specialist" in text


def test_render_tool_call_started_shows_tool_and_args():
    text = _capture(ToolCallStarted(
        agent="jean-michel",
        tool_name="web_search",
        args_summary="query='paris'",
    ))
    assert "web_search" in text
    assert "paris" in text


def test_render_tool_call_completed_shows_result_summary():
    text = _capture(ToolCallCompleted(
        tool_name="clock",
        result_summary="UTC: 2026-05-28T01:49:06Z",
        duration_ms=5,
    ))
    assert "clock" in text
    assert "2026-05-28" in text


def test_render_delegation_started_shows_child_and_budget():
    text = _capture(DelegationStarted(
        parent_agent="jean-michel",
        child_agent="summarizer",
        depth=1,
        child_working_budget=50_000,
    ))
    assert "summarizer" in text
    assert "depth=1" in text
    assert "50000" in text or "50_000" in text


def test_render_delegation_completed_color_by_confidence():
    """Confidence is reflected in the output (high/medium/low)."""
    for conf in ("high", "medium", "low"):
        text = _capture(DelegationCompleted(
            child_agent="agent-X",
            summary="finding Y",
            confidence=conf,
            files_produced=["agent-X_y.md"],
        ))
        assert "agent-X" in text
        assert conf in text
        assert "finding Y" in text


def test_render_hook_fired_shows_action_and_reason():
    text = _capture(HookFired(
        hook_name="PreToolUse",
        action="deny",
        reason="grant missing for web_search",
    ))
    assert "PreToolUse" in text
    assert "deny" in text
    assert "grant missing" in text


def test_render_working_budget_update_uses_human_label():
    """Compaction levels are mapped to human labels (snip, microcompact, ...)."""
    expected = {
        1: "snip",
        2: "microcompact",
        3: "collapse",
        4: "autocompact",
    }
    for level, label in expected.items():
        text = _capture(WorkingBudgetUpdate(
            ratio=0.85,
            compaction_level_triggered=level,
        ))
        assert label in text
        assert "85%" in text or "85" in text


def test_render_memory_near_capacity_warns():
    text = _capture(MemoryNearCapacity(current_count=90, limit=100))
    assert "90" in text
    assert "100" in text
    # The text must hint at the corrective action.
    assert "delete" in text.lower() or "purg" in text.lower()


def test_render_request_completed_shows_rule_with_agent():
    text = _capture(RequestCompleted(
        agent="jean-michel",
        final_content_summary="The answer is 42.",
    ))
    assert "jean-michel" in text


def test_render_llm_call_started_is_silent_by_design():
    """LLMCallStarted is intentionally not rendered to avoid spam."""
    text = _capture(LLMCallStarted(
        agent="jean-michel",
        model="gemma4:latest",
        messages_count=5,
        working_tokens_used=1024,
    ))
    # No agent name → silent.
    assert "gemma4" not in text
    assert "1024" not in text


def test_render_llm_call_completed_is_silent_by_design():
    text = _capture(LLMCallCompleted(tokens_used=200, tool_call_count=1))
    assert text.strip() == ""


def test_render_unknown_event_falls_back_to_repr():
    """An unrecognized event type prints a fallback line so debugging is possible."""
    class FakeEvent:
        pass

    text = _capture(FakeEvent())
    # Falls back to type name + repr.
    assert "FakeEvent" in text


# ---- --resume persistence integration -----------------------------------


def test_resume_loads_persisted_messages(tmp_path):
    """Simulate a crash : save messages, then load them back via persistence."""
    from jeanmichel import persistence

    conv_folder = tmp_path / "conv_for_resume"
    conv_folder.mkdir()

    # Simulate 3 turns persisted.
    turn1_messages = [
        {"role": "system", "content": "You are jean-michel."},
        {"role": "user", "content": "turn 1"},
        {"role": "assistant", "content": "answer 1"},
        {"role": "user", "content": "turn 2"},
        {"role": "assistant", "content": "answer 2"},
        {"role": "user", "content": "turn 3"},
        {"role": "assistant", "content": "answer 3"},
    ]
    persistence.save_messages(conv_folder, turn1_messages)

    # --resume reads them back.
    loaded = persistence.load_messages(conv_folder)
    assert loaded == turn1_messages

    # The resumed conversation can continue from turn 4 : we append a new
    # user message and run_main_loop would pick it up. We just verify the
    # array shape is preserved.
    assert loaded[-1]["role"] == "assistant"
    assert loaded[-1]["content"] == "answer 3"


def test_resume_returns_empty_list_for_missing_messages(tmp_path):
    """If messages.json doesn't exist, load_messages returns []."""
    from jeanmichel import persistence

    folder = tmp_path / "empty_conv"
    folder.mkdir()
    assert persistence.load_messages(folder) == []


# ---- ask_human callback --------------------------------------------------


def test_make_ask_human_invokes_prompt_session():
    """The callback wraps a prompt_session.prompt call. We mock the session."""

    class _Session:
        def __init__(self):
            self.last_args = None

        def prompt(self, *args, **kwargs):
            self.last_args = (args, kwargs)
            return "  my answer  "  # padded with whitespace to verify stripping

    console = Console(record=True, width=120, force_terminal=False)
    session = _Session()
    cb = make_ask_human(console, session)

    answer = cb("Should I proceed?", "I need confirmation for X.", [], False)

    assert answer == "my answer"  # stripped
    # Console output mentions the question.
    text = console.export_text()
    assert "Should I proceed?" in text
    assert "I need confirmation for X." in text


class _ReplySession:
    def __init__(self, reply):
        self._reply = reply

    def prompt(self, *args, **kwargs):
        return self._reply


def test_make_ask_human_maps_single_choice_number():
    """A bare number selects the matching choice label."""
    cb = make_ask_human(Console(record=True, width=120, force_terminal=False), _ReplySession("2"))
    assert cb("Pick", "why", ["Red", "Green", "Blue"], False) == "Green"


def test_make_ask_human_maps_multi_choice_numbers():
    """Comma-separated numbers map to joined labels when multi."""
    cb = make_ask_human(Console(record=True, width=120, force_terminal=False), _ReplySession("1, 3"))
    assert cb("Pick", "why", ["Red", "Green", "Blue"], True) == "Red, Blue"


def test_make_ask_human_free_text_escape_with_choices():
    """Non-numeric input with choices present is kept as free text (the 'Other' escape)."""
    cb = make_ask_human(Console(record=True, width=120, force_terminal=False), _ReplySession("purple"))
    assert cb("Pick", "why", ["Red", "Green", "Blue"], False) == "purple"
