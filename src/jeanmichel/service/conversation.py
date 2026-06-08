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

from .. import config, db, snapshot


def make_conv_folder(conv_id: str) -> Path:
    """Create and return the timestamped folder for a conversation."""
    name = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M") + f"_{conv_id}"
    # Resolve via the config module (NOT an import-time binding) so test fixtures
    # that redirect config.CONVERSATIONS_DIR to a tmp dir actually take effect —
    # otherwise every test that creates a conversation pollutes the repo's
    # conversations/ folder (this is what produced the ~1200 stray folders).
    folder = config.CONVERSATIONS_DIR / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def create_conversation(
    mode: str, *, user_language: str | None = None, project_id: int | None = None
) -> tuple[str, Path]:
    """Create a new conversation (DB row + folder). Returns (conv_id, folder).

    ``project_id`` (optional) attaches the conversation to a project so its
    scope='project' memory is injected for every turn."""
    conv_id = uuid.uuid4().hex
    conv_folder = make_conv_folder(conv_id)
    with db.connect() as conn:
        db.create_conversation(
            conn,
            conv_id=conv_id,
            folder_path=str(conv_folder),
            user_language=user_language,
            mode=mode,
            project_id=project_id,
        )
    # Init the per-conversation git repo (no-op unless snapshots are enabled).
    snapshot.init_repo(conv_folder, conv_id)
    return conv_id, conv_folder


def fork_conversation(src_conv_id: str, commit: str) -> tuple[str, Path]:
    """Create a new conversation from the tree of ``commit`` in the source.

    Copies the source conversation's content AS OF ``commit`` (workspace +
    messages + state + events, minus ``.git``) into a fresh conversation
    folder + DB row, then re-inits it as its own repo. Returns (new_id, folder).
    Raises ValueError if the source is missing, RuntimeError if the snapshot
    copy fails (snapshots disabled, git absent, or unknown commit). The owner
    association is the caller's responsibility (mirrors create_conversation).
    """
    with db.connect() as conn:
        src = db.get_conversation(conn, src_conv_id)
    if src is None:
        raise ValueError(f"conversation not found: {src_conv_id!r}")

    new_id = uuid.uuid4().hex
    dst = make_conv_folder(new_id)
    if not snapshot.fork_at(Path(src["folder_path"]), dst, commit, new_id):
        shutil.rmtree(dst, ignore_errors=True)
        raise RuntimeError(
            "fork failed (snapshots disabled, git absent, or unknown commit)"
        )
    with db.connect() as conn:
        db.create_conversation(
            conn,
            conv_id=new_id,
            folder_path=str(dst),
            user_language=src["user_language"],
            mode=src["mode"],
        )
    return new_id, dst


def revert_conversation(conv_id: str, commit: str) -> bool:
    """Rewind a conversation to an earlier turn snapshot (destructive).

    Returns True on success. Raises ValueError if the conversation is missing.
    Returns False if snapshots are disabled / git absent / the commit is unknown.
    """
    with db.connect() as conn:
        row = db.get_conversation(conn, conv_id)
    if row is None:
        raise ValueError(f"conversation not found: {conv_id!r}")
    return snapshot.revert_to(Path(row["folder_path"]), commit)


def delete_conversation(conv_id: str) -> None:
    """Delete a conversation entirely : DB row (+ cascaded ownership links via
    migrate_114) and its on-disk folder (messages, events, workspace).
    Folder removal is best-effort."""
    with db.connect() as conn:
        row = db.get_conversation(conn, conv_id)
        db.delete_conversation(conn, conv_id)
    if row is not None:
        shutil.rmtree(Path(row["folder_path"]), ignore_errors=True)
