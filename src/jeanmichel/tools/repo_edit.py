"""Tool: repo_edit — exact-string replacement in a worktree file (in place).

Mirrors workspace_str_replace + Claude Code's Edit: ``old_str`` must match
exactly (once, unless ``replace_all``), replaced literally, atomic write. Adds
the code-mode gates: protected-path deny + read-before-edit + freshness (the
file must have been repo_read this conversation and be unchanged since).
"""

from __future__ import annotations

from pathlib import Path

from . import _repo
from ._base import ToolSpec
from ._errors import tool_error, tool_ok


def make_spec(conv_folder: Path) -> ToolSpec:
    """Return a ToolSpec bound to ``conv_folder`` (edits its git worktree)."""

    def _handler(relative_path: str, old_str: str, new_str: str = "", replace_all: bool = False) -> str:
        root = _repo.worktree_root(conv_folder)
        if root is None:
            return tool_error("no_worktree", "No code worktree for this conversation.")
        if _repo.is_protected(relative_path):
            return tool_error("protected_path",
                              f"'{relative_path}' is protected and cannot be edited.")
        try:
            target = _repo.safe_resolve(root, relative_path)
        except ValueError as e:
            msg = str(e)
            code = "absolute_path" if "absolute" in msg.lower() else "path_escape"
            return tool_error(code, msg)
        if not target.exists() or not target.is_file():
            return tool_error("file_not_found", f"File not found: {relative_path}",
                              relative_path=relative_path)
        canonical = target.relative_to(root.resolve()).as_posix()
        gate = _repo.edit_preflight(conv_folder, target, canonical)
        if gate is not None:
            code = "stale_read" if gate.startswith("stale_read") else "read_before_edit"
            return tool_error(code, gate, relative_path=canonical)
        try:
            original = target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return tool_error("not_utf8", "File is not valid UTF-8.", relative_path=canonical)
        count = original.count(old_str)
        if count == 0:
            return tool_error("old_str_not_found",
                              "old_str not found. Copy it verbatim from repo_read "
                              "(without the line-number prefix).", occurrences=0)
        if count > 1 and not replace_all:
            return tool_error("old_str_not_unique",
                              f"old_str appears {count} times — add surrounding context to "
                              "make it unique, or pass replace_all=true.", occurrences=count)
        replaced = original.replace(old_str, new_str) if replace_all else original.replace(old_str, new_str, 1)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(replaced, encoding="utf-8")
        tmp.replace(target)
        # Keep the ledger fresh so a follow-up edit in the same turn still passes.
        _repo.mark_read(conv_folder, canonical, target.stat().st_mtime_ns)
        return tool_ok(
            f"edited {canonical} ({count if replace_all else 1} replacement(s), "
            f"{len(replaced.encode('utf-8'))} bytes)",
            path=canonical,
            occurrences_replaced=count if replace_all else 1,
            bytes_after=len(replaced.encode("utf-8")),
        )

    return ToolSpec(
        name="repo_edit",
        description=(
            "SIGNATURE: repo_edit(relative_path, old_str, new_str, replace_all?). "
            "Replace an exact string in a project-repo file IN PLACE. Param names are "
            "EXACT: 'old_str' and 'new_str' (not old_string/new_string). old_str must "
            "appear exactly once (or pass replace_all=true to replace every occurrence). "
            "new_str empty deletes the match. You MUST repo_read the file first; the edit "
            "is refused if you did not, or if the file changed since you read it. "
            "NEVER include repo_read's line-number prefix in old_str. Writes atomically."
        ),
        parameters={
            "type": "object",
            "properties": {
                "relative_path": {"type": "string", "description": "Path relative to the repo root."},
                "old_str": {"type": "string", "description": "Exact string to replace (verbatim, no line-number prefix)."},
                "new_str": {"type": "string", "description": "Replacement string. Empty to delete."},
                "replace_all": {"type": "boolean", "description": "Replace every occurrence (default false)."},
            },
            "required": ["relative_path", "old_str"],
        },
        handler=_handler,
    )
