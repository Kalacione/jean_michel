"""Read-only workspace access for the web API.

Scoped to a conversation's ``workspace/`` subfolder. Conversation-root files
(messages.json, state.json, events.jsonl) are NOT exposed here — they have
dedicated endpoints. The path-traversal guard is the shared
``tools._workspace.safe_resolve`` (the security-critical part) ; this module
adds the API-shaped tree listing + file read on top.

Errors are signalled by raising ``WorkspaceError(code, message)``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..tools._workspace import safe_resolve, workspace_root_for

_MAX_TREE_DEPTH = 2
DEFAULT_MAX_BYTES = 100_000


class WorkspaceError(Exception):
    """A workspace read failed. ``code`` ∈ {invalid_path, not_found, not_utf8}."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _entry(p: Path, depth: int) -> dict[str, Any]:
    stat = p.stat()
    node: dict[str, Any] = {
        "name": p.name,
        "type": "directory" if p.is_dir() else "file",
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }
    if p.is_file():
        node["size_bytes"] = stat.st_size
    if p.is_dir() and depth < _MAX_TREE_DEPTH:
        node["children"] = [_entry(c, depth + 1) for c in sorted(p.iterdir())]
    return node


def list_tree(conv_folder: Path, sub_path: str = "") -> dict[str, Any]:
    """Tree of the conversation workspace (max 2 levels), optionally from sub_path."""
    ws_root = workspace_root_for(conv_folder)
    if sub_path:
        try:
            start = safe_resolve(ws_root, sub_path)
        except ValueError as exc:
            raise WorkspaceError("invalid_path", str(exc)) from exc
        if not start.is_dir():
            raise WorkspaceError("not_found", f"Not a directory: {sub_path}")
    else:
        start = ws_root
    entries = [_entry(p, 1) for p in sorted(start.iterdir())]
    return {"workspace": sub_path, "entries": entries}


def read_file(
    conv_folder: Path, relative_path: str, max_bytes: int = DEFAULT_MAX_BYTES
) -> dict[str, Any]:
    """Read a UTF-8 file under the conversation workspace. Workspace-scoped only."""
    ws_root = workspace_root_for(conv_folder)
    try:
        target = safe_resolve(ws_root, relative_path)
    except ValueError as exc:
        raise WorkspaceError("invalid_path", str(exc)) from exc
    if not target.is_file():
        raise WorkspaceError("not_found", f"Not found: {relative_path}")
    data = target.read_bytes()
    truncated = len(data) > max_bytes
    try:
        content = data[:max_bytes].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkspaceError("not_utf8", "File is not valid UTF-8.") from exc
    return {"path": relative_path, "content": content, "truncated": truncated}
