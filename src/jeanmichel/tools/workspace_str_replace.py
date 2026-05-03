"""Tool: workspace_str_replace — atomic string replacement in a workspace file.

old_str must appear exactly once. Writes atomically via a .tmp file.
"""

from __future__ import annotations

import json
from pathlib import Path

from ._base import ToolSpec
from ._workspace import safe_resolve, workspace_root_for


def make_spec(conv_folder: Path, has_write_grant: bool = False) -> ToolSpec:
    """Return a ToolSpec bound to `conv_folder`."""

    def _handler(relative_path: str, old_str: str, new_str: str = "") -> str:
        if not has_write_grant:
            return json.dumps({"error": "Write access not granted for this agent."})
        ws_root = workspace_root_for(conv_folder)
        try:
            target = safe_resolve(ws_root, relative_path)
        except ValueError as e:
            return json.dumps({"error": str(e)})
        if not target.exists():
            return json.dumps({"error": f"File not found: {relative_path}"})
        try:
            original = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return json.dumps({"error": "File is not valid UTF-8."})
        count = original.count(old_str)
        if count == 0:
            return json.dumps({"error": "old_str not found in file.", "occurrences": 0})
        if count > 1:
            return json.dumps({"error": f"old_str appears {count} times — must be unique.", "occurrences": count})
        updated = original.replace(old_str, new_str, 1)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(updated, encoding="utf-8")
        tmp.replace(target)
        return json.dumps({
            "path": relative_path,
            "occurrences_replaced": 1,
            "bytes_after": len(updated.encode("utf-8")),
        })

    return ToolSpec(
        name="workspace_str_replace",
        description=(
            "Replace a unique string in a workspace file. "
            "old_str must appear exactly once in the file. "
            "new_str can be empty to delete the matched text. "
            "Writes atomically."
        ),
        parameters={
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": "Path relative to workspace root.",
                },
                "old_str": {
                    "type": "string",
                    "description": "Exact string to replace. Must appear exactly once.",
                },
                "new_str": {
                    "type": "string",
                    "description": "Replacement string. Omit or pass empty string to delete.",
                },
            },
            "required": ["relative_path", "old_str"],
        },
        handler=_handler,
    )
