"""Unit tests for src/jeanmichel/persistence.py."""

from __future__ import annotations

from datetime import UTC, datetime

from jeanmichel.persistence import (
    append_to_journal,
    conversation_folder_name,
    write_artifact,
)


def test_write_artifact_creates_file(tmp_path):
    folder = tmp_path / "conv"
    folder.mkdir()
    filename = write_artifact(
        folder, conversation_id="c1", request_id="r1",
        agent="jean-michel", kind="thought", body="thinking hard",
    )
    path = folder / filename
    assert path.exists()
    content = path.read_text()
    assert "agent: jean-michel" in content
    assert "kind: thought" in content
    assert "thinking hard" in content


def test_write_artifact_filename_format(tmp_path):
    folder = tmp_path / "conv"
    folder.mkdir()
    filename = write_artifact(
        folder, conversation_id="c1", request_id="r1",
        agent="summarizer", kind="response", body="done",
    )
    # HHMMSSMMM_agent_kind.md
    assert filename.endswith("_summarizer_response.md")


def test_append_to_journal_creates_and_appends(tmp_path):
    folder = tmp_path / "conv"
    folder.mkdir()
    append_to_journal(folder, "## User\nhello")
    append_to_journal(folder, "## Jean-Michel\nbonjour")
    content = (folder / "conversation.md").read_text()
    assert "## User" in content
    assert "## Jean-Michel" in content


def test_conversation_folder_name():
    dt = datetime(2026, 4, 27, 14, 30, tzinfo=UTC)
    assert conversation_folder_name("abc123", dt) == "2026-04-27_14-30_abc123"
