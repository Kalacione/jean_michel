"""Tool: repo_read — read a file from the code-mode worktree (cat -n format).

Read-only. Records the read in the per-conversation ledger so repo_edit /
repo_write can enforce read-before-edit + freshness. Mirrors Claude Code's Read
contract: 1-indexed line numbers, ``offset``/``limit`` line window.
"""

from __future__ import annotations

from pathlib import Path

from . import _repo
from ._base import ToolSpec
from ._errors import tool_error, tool_ok


def make_spec(conv_folder: Path) -> ToolSpec:
    """Return a ToolSpec bound to ``conv_folder`` (reads its git worktree)."""

    def _handler(relative_path: str, offset: int = 1, limit: int = 2000) -> str:
        root = _repo.worktree_root(conv_folder)
        if root is None:
            return tool_error("no_worktree", "No code worktree for this conversation.")
        try:
            target = _repo.safe_resolve(root, relative_path)
        except ValueError as e:
            msg = str(e)
            code = "absolute_path" if "absolute" in msg.lower() else "path_escape"
            return tool_error(code, msg)
        if not target.exists() or not target.is_file():
            return tool_error("file_not_found", f"File not found: {relative_path}",
                              relative_path=relative_path)
        try:
            content = target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return tool_error("not_utf8", "File is not valid UTF-8 text.",
                              relative_path=relative_path)
        canonical = target.relative_to(root.resolve()).as_posix()
        # Record the read against the file's current mtime → enables the
        # read-before-edit + freshness gate in repo_edit / repo_write.
        _repo.mark_read(conv_folder, canonical, target.stat().st_mtime_ns)

        lines = content.splitlines()
        start = max(1, offset)
        window = lines[start - 1: start - 1 + max(1, limit)]
        truncated = (start - 1 + len(window)) < len(lines)
        rendered = _repo.cat_n("\n".join(window), start_line=start)
        suffix = " (truncated — raise limit/offset to read more)" if truncated else ""
        return tool_ok(
            f"read {canonical} (lines {start}-{start + len(window) - 1} of {len(lines)}){suffix}",
            path=canonical,
            content=rendered,
            total_lines=len(lines),
            truncated=truncated,
        )

    return ToolSpec(
        name="repo_read",
        description=(
            "SIGNATURE: repo_read(relative_path, offset?, limit?). "
            "Read a file from the project repo (the code worktree), returned in "
            "`cat -n` format (1-indexed line numbers + a tab). `offset` is the "
            "first line (default 1), `limit` the max number of lines (default 2000). "
            "You MUST repo_read a file before repo_edit/repo_write-overwrite it. "
            "When forming an edit's old_string, NEVER include the line-number prefix."
        ),
        parameters={
            "type": "object",
            "properties": {
                "relative_path": {"type": "string", "description": "Path relative to the repo root."},
                "offset": {"type": "integer", "description": "First line to read (1-indexed). Default 1."},
                "limit": {"type": "integer", "description": "Max lines to read. Default 2000."},
            },
            "required": ["relative_path"],
        },
        handler=_handler,
    )
