"""Tool: repo_write — create or overwrite a file in the worktree (in place).

New file → written directly. Overwrite of an existing file → gated like
repo_edit (protected-path deny + read-before-edit + freshness), so you never
clobber content you haven't seen. Atomic write.
"""

from __future__ import annotations

from pathlib import Path

from . import _repo
from ._base import ToolSpec
from ._errors import tool_error, tool_ok


def make_spec(conv_folder: Path) -> ToolSpec:
    """Return a ToolSpec bound to ``conv_folder`` (writes its git worktree)."""

    def _handler(relative_path: str, content: str) -> str:
        root = _repo.worktree_root(conv_folder)
        if root is None:
            return tool_error("no_worktree", "No code worktree for this conversation.")
        if _repo.is_protected(relative_path):
            return tool_error("protected_path",
                              f"'{relative_path}' is protected and cannot be written.")
        try:
            target = _repo.safe_resolve(root, relative_path)
        except ValueError as e:
            msg = str(e)
            code = "absolute_path" if "absolute" in msg.lower() else "path_escape"
            return tool_error(code, msg)
        if target.is_dir():
            return tool_error("is_a_directory", f"{relative_path} is a directory.")
        canonical = target.relative_to(root.resolve()).as_posix()
        existed = target.exists()
        if existed:
            gate = _repo.edit_preflight(conv_folder, target, canonical)
            if gate is not None:
                code = "stale_read" if gate.startswith("stale_read") else "read_before_edit"
                return tool_error(code, gate, relative_path=canonical)
        encoded = content.encode("utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(encoded)
        tmp.replace(target)
        # New or rewritten: record current mtime so a follow-up edit passes.
        _repo.mark_read(conv_folder, canonical, target.stat().st_mtime_ns)
        verb = "overwrote" if existed else "created"
        return tool_ok(
            f"{verb} {canonical} ({len(encoded)} bytes)",
            path=canonical,
            bytes_written=len(encoded),
            created=not existed,
        )

    return ToolSpec(
        name="repo_write",
        description=(
            "SIGNATURE: repo_write(relative_path, content). "
            "Create a new file (or fully rewrite an existing one) in the project repo. "
            "Param name is EXACT: 'content'. For a small targeted change, prefer repo_edit. "
            "Overwriting an existing file requires you to repo_read it first (and it must be "
            "unchanged since) — new files do not. Sub-directories are created. Writes atomically."
        ),
        parameters={
            "type": "object",
            "properties": {
                "relative_path": {"type": "string", "description": "Path relative to the repo root."},
                "content": {"type": "string", "description": "Full file content (UTF-8)."},
            },
            "required": ["relative_path", "content"],
        },
        handler=_handler,
    )
