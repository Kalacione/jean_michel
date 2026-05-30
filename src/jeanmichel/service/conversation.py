"""Conversation lifecycle, transport-agnostic.

Extracted from ``cli`` so the CLI and the web daemon create conversations the
same way. Resume *messaging* stays in the CLI (terminal-specific) ; the daemon
will scope resume by owner via the association table (S1+).
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .. import db
from ..config import CONVERSATIONS_DIR


def make_conv_folder(conv_id: str) -> Path:
    """Create and return the timestamped folder for a conversation."""
    name = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M") + f"_{conv_id}"
    folder = CONVERSATIONS_DIR / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def create_conversation(
    mode: str, *, user_language: str | None = None
) -> tuple[str, Path]:
    """Create a new conversation (DB row + folder). Returns (conv_id, folder)."""
    conv_id = uuid.uuid4().hex
    conv_folder = make_conv_folder(conv_id)
    with db.connect() as conn:
        db.create_conversation(
            conn,
            conv_id=conv_id,
            folder_path=str(conv_folder),
            user_language=user_language,
            mode=mode,
        )
    return conv_id, conv_folder


def delete_conversation(conv_id: str) -> None:
    """Delete a conversation entirely : DB row (+ cascaded ownership links via
    migrate_114) and its on-disk folder (messages, events, workspace).
    Folder removal is best-effort."""
    with db.connect() as conn:
        row = db.get_conversation(conn, conv_id)
        db.delete_conversation(conn, conv_id)
    if row is not None:
        shutil.rmtree(Path(row["folder_path"]), ignore_errors=True)
