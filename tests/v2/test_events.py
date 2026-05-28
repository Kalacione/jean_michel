"""Tests for `jeanmichel.events` — 11 typed event dataclasses + (de)serialisation."""

from __future__ import annotations

import json

import pytest

from jeanmichel.events import (
    EVENT_CLASSES,
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
    event_from_dict,
    event_from_jsonl_line,
    event_to_jsonl_line,
)


# ---- 11 event types are registered ---------------------------------------


def test_event_catalogue_has_eleven_entries():
    expected = {
        "RequestStarted",
        "LLMCallStarted",
        "LLMCallCompleted",
        "ToolCallStarted",
        "ToolCallCompleted",
        "DelegationStarted",
        "DelegationCompleted",
        "HookFired",
        "WorkingBudgetUpdate",
        "MemoryNearCapacity",
        "RequestCompleted",
    }
    assert set(EVENT_CLASSES.keys()) == expected
    assert len(EVENT_CLASSES) == 11


# ---- Each event has utc auto-populated -----------------------------------


def test_utc_is_auto_populated():
    ev = RequestStarted(agent="jean-michel", depth=0, briefing_summary="hello")
    assert ev.utc, "utc field should be auto-populated"
    assert "T" in ev.utc, "utc should look like an ISO-8601 timestamp"


# ---- to_dict() / serialisation ------------------------------------------


def test_request_started_to_dict_includes_type():
    ev = RequestStarted(agent="jean-michel", depth=0, briefing_summary="hello world")
    d = ev.to_dict()
    assert d["type"] == "RequestStarted"
    assert d["agent"] == "jean-michel"
    assert d["depth"] == 0
    assert d["briefing_summary"] == "hello world"
    assert "utc" in d


def test_jsonl_line_is_valid_json_with_newline():
    ev = LLMCallStarted(
        agent="jean-michel",
        model="gemma4:latest",
        messages_count=3,
        working_tokens_used=1024,
    )
    line = event_to_jsonl_line(ev)
    assert line.endswith("\n")
    parsed = json.loads(line)
    assert parsed["type"] == "LLMCallStarted"
    assert parsed["model"] == "gemma4:latest"
    assert parsed["messages_count"] == 3
    assert parsed["working_tokens_used"] == 1024


# ---- Roundtrip serialisation/desérialisation for every event class ------


@pytest.mark.parametrize(
    "event",
    [
        RequestStarted(agent="jean-michel", depth=0, briefing_summary="x"),
        LLMCallStarted(agent="jm", model="m", messages_count=1, working_tokens_used=0),
        LLMCallCompleted(tokens_used=42, tool_call_count=1),
        ToolCallStarted(agent="jm", tool_name="clock", args_summary=""),
        ToolCallCompleted(tool_name="clock", result_summary="ok", duration_ms=12),
        DelegationStarted(
            parent_agent="jm",
            child_agent="wikipedia-specialist",
            depth=1,
            child_working_budget=50_000,
        ),
        DelegationCompleted(
            child_agent="wikipedia-specialist",
            summary="found 3 facts",
            confidence="high",
            files_produced=["wikipedia-specialist_paris.md"],
        ),
        HookFired(hook_name="PreToolUse", action="deny", reason="grant missing"),
        WorkingBudgetUpdate(ratio=0.72, compaction_level_triggered=1),
        MemoryNearCapacity(current_count=90, limit=100),
        RequestCompleted(agent="jean-michel", final_content_summary="here is the answer"),
    ],
)
def test_event_roundtrip_via_jsonl(event):
    line = event_to_jsonl_line(event)
    parsed = event_from_jsonl_line(line)
    # Reconstructed dataclass has the same fields/values.
    assert type(parsed) is type(event)
    assert parsed == event


# ---- Unknown event type raises a clear error ----------------------------


def test_event_from_dict_unknown_type_raises():
    with pytest.raises(ValueError, match="Unknown event type"):
        event_from_dict({"type": "NotARealEvent", "agent": "jm"})


# ---- Default mutable field (files_produced) is independent per instance --


def test_files_produced_default_is_independent():
    a = DelegationCompleted(child_agent="x", summary="s", confidence="medium")
    b = DelegationCompleted(child_agent="y", summary="s2", confidence="low")
    assert a.files_produced == []
    assert b.files_produced == []
    # Frozen dataclass — verifying they're separate instances, not shared default.
    assert a.files_produced is not b.files_produced
