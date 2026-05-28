"""Tests for the v2 persistence layer."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from jeanmichel.events import RequestStarted, ToolCallCompleted
from jeanmichel.models import ConversationState
from jeanmichel.persistence import (
    append_event,
    load_events,
    load_messages,
    load_state,
    load_sub_messages,
    save_messages,
    save_state,
    save_sub_messages,
)


# ---- messages.json -------------------------------------------------------


def test_save_and_load_messages_roundtrip(conv_folder: Path):
    messages = [
        {"role": "system", "content": "you are jean-michel"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    save_messages(conv_folder, messages)
    loaded = load_messages(conv_folder)
    assert loaded == messages


def test_load_messages_missing_file_returns_empty_list(conv_folder: Path):
    assert load_messages(conv_folder) == []


def test_save_messages_creates_parent_dir(tmp_path: Path):
    # Use a sub-folder that doesn't exist yet.
    folder = tmp_path / "new_conv"
    save_messages(folder, [{"role": "user", "content": "hi"}])
    assert (folder / "messages.json").exists()


def test_save_messages_is_atomic_no_partial_state(conv_folder: Path, monkeypatch):
    """Crash mid-write must leave the previous valid version on disk."""
    # First write : success.
    save_messages(conv_folder, [{"role": "user", "content": "v1"}])
    assert (conv_folder / "messages.json").exists()

    # Patch os.replace to fail. The atomic write writes a tempfile FIRST,
    # then renames. If rename fails, the original file stays intact.
    from jeanmichel import persistence as p

    real_replace = p.os.replace

    def failing_replace(*args, **kwargs):
        raise RuntimeError("disk gremlin")

    monkeypatch.setattr(p.os, "replace", failing_replace)

    with pytest.raises(RuntimeError, match="disk gremlin"):
        save_messages(conv_folder, [{"role": "user", "content": "v2-incomplete"}])

    # Restore real replace for the assertion read.
    monkeypatch.setattr(p.os, "replace", real_replace)

    # Original content is intact — atomicity preserved.
    assert load_messages(conv_folder) == [{"role": "user", "content": "v1"}]
    # No leftover tempfile in the folder.
    leftovers = [f for f in conv_folder.iterdir() if f.name.startswith(".messages.json.")]
    assert leftovers == [], f"tempfile leftovers: {leftovers}"


# ---- state.json ----------------------------------------------------------


def test_save_and_load_state(conv_folder: Path):
    state = ConversationState(
        system_reserve_tokens=12_400,
        output_reserve_tokens=19_200,
        working_budget=96_400,
        working_tokens_used=28_500,
        depth_current=1,
        search_calls_total=4,
        search_calls_since_last_persist=2,
        active_subagent="wikipedia-specialist",
        last_iteration_at_utc="2026-05-27T18:42:13Z",
    )
    save_state(conv_folder, state)
    loaded = load_state(conv_folder)
    assert loaded["system_reserve_tokens"] == 12_400
    assert loaded["working_budget"] == 96_400
    assert loaded["depth_current"] == 1
    assert loaded["active_subagent"] == "wikipedia-specialist"


def test_load_state_missing_returns_empty(conv_folder: Path):
    assert load_state(conv_folder) == {}


# ---- events.jsonl --------------------------------------------------------


def test_append_event_creates_jsonl_file(conv_folder: Path):
    ev = RequestStarted(agent="jean-michel", depth=0, briefing_summary="hello")
    append_event(conv_folder, ev)

    path = conv_folder / "events.jsonl"
    assert path.exists()
    line = path.read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    assert parsed["type"] == "RequestStarted"
    assert parsed["agent"] == "jean-michel"


def test_append_event_appends_multiple_lines(conv_folder: Path):
    events = [
        RequestStarted(agent="jean-michel", depth=0, briefing_summary="x"),
        ToolCallCompleted(tool_name="clock", result_summary="ok", duration_ms=5),
        RequestStarted(agent="wikipedia-specialist", depth=1, briefing_summary="y"),
    ]
    for ev in events:
        append_event(conv_folder, ev)

    loaded = load_events(conv_folder)
    assert len(loaded) == 3
    assert [e["type"] for e in loaded] == [
        "RequestStarted",
        "ToolCallCompleted",
        "RequestStarted",
    ]


def test_append_event_accepts_raw_dict(conv_folder: Path):
    append_event(conv_folder, {"type": "Custom", "foo": 1})
    loaded = load_events(conv_folder)
    assert loaded == [{"type": "Custom", "foo": 1}]


def test_append_event_concurrent_safe_1000_writes(conv_folder: Path):
    """1000 concurrent appends from 10 threads → 1000 valid JSON lines."""
    n_threads = 10
    n_per_thread = 100

    def writer(thread_id: int) -> None:
        for i in range(n_per_thread):
            ev = ToolCallCompleted(
                tool_name=f"tool_{thread_id}",
                result_summary=f"call_{i}",
                duration_ms=i,
            )
            append_event(conv_folder, ev)

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    path = conv_folder / "events.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == n_threads * n_per_thread

    # Every line is a valid JSON object — no torn writes.
    for line in lines:
        obj = json.loads(line)
        assert obj["type"] == "ToolCallCompleted"
        assert "tool_name" in obj


# ---- subagent_<id>.json --------------------------------------------------


def test_save_and_load_sub_messages(conv_folder: Path):
    sub_messages = [
        {"role": "system", "content": "you are wikipedia-specialist"},
        {"role": "user", "content": "find facts about paris"},
        {"role": "assistant", "content": "researching"},
    ]
    save_sub_messages(conv_folder, "req-abc123", sub_messages)
    loaded = load_sub_messages(conv_folder, "req-abc123")
    assert loaded == sub_messages


def test_load_sub_messages_missing_returns_empty(conv_folder: Path):
    assert load_sub_messages(conv_folder, "nope") == []


def test_subagent_files_are_isolated_per_request_id(conv_folder: Path):
    save_sub_messages(conv_folder, "req-A", [{"role": "user", "content": "A"}])
    save_sub_messages(conv_folder, "req-B", [{"role": "user", "content": "B"}])

    assert load_sub_messages(conv_folder, "req-A") == [{"role": "user", "content": "A"}]
    assert load_sub_messages(conv_folder, "req-B") == [{"role": "user", "content": "B"}]
