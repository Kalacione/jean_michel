"""Disk persistence: writes artifacts (prompts, thoughts, briefings, …) with frontmatter."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path


def _frontmatter(conversation_id: str, request_id: str, agent: str, kind: str) -> str:
    utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        "---\n"
        f"conversation_id: {conversation_id}\n"
        f"request_id: {request_id}\n"
        f"agent: {agent}\n"
        f"kind: {kind}\n"
        f"utc: {utc}\n"
        "---\n\n"
    )


def _hhmmssmmm() -> str:
    now = datetime.now(UTC)
    return now.strftime("%H%M%S") + f"{now.microsecond // 1000:03d}"


def write_artifact(conv_folder: Path, *, conversation_id: str, request_id: str,
                   agent: str, kind: str, body: str) -> str:
    """Write an artifact file. Returns its relative path inside conv_folder."""
    filename = f"{_hhmmssmmm()}_{agent}_{kind}.md"
    path = conv_folder / filename
    path.write_text(_frontmatter(conversation_id, request_id, agent, kind) + body, encoding="utf-8")
    return filename


def append_to_journal(conv_folder: Path, line: str) -> None:
    """Append a human-readable line to conversation.md."""
    journal = conv_folder / "conversation.md"
    if not journal.exists():
        journal.write_text("# Conversation journal\n\n", encoding="utf-8")
    with open(journal, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def conversation_folder_name(conv_id: str, started_at_utc: datetime) -> str:
    return started_at_utc.strftime("%Y-%m-%d_%H-%M") + f"_{conv_id}"
