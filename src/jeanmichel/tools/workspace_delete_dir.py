"""Tool: workspace_delete_dir — delete a directory (recursive) in the workspace.

Sandboxed: cannot escape the workspace/ folder, and cannot delete the workspace
root itself (safe_resolve rejects it). Refuses plain files (use
workspace_delete_file).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ._base import ToolSpec
from ._errors import tool_error, tool_ok
from ._workspace import safe_resolve, workspace_root_for


def make_spec(conv_folder: Path, has_write_grant: bool = False) -> ToolSpec:
    """Return a ToolSpec bound to `conv_folder`."""

    def _handler(relative_path: str) -> str:
        if not has_write_grant:
            return tool_error("no_write_grant", "Write access not granted for this agent.")
        ws_root = workspace_root_for(conv_folder)
        try:
            target = safe_resolve(ws_root, relative_path)
        except ValueError as e:
            msg = str(e)
            code = "absolute_path" if "absolute" in msg.lower() else "path_escape"
            return tool_error(code, msg)
        canonical = target.relative_to(ws_root).as_posix()
        if not target.exists():
            return tool_error("file_not_found", f"Directory not found: {canonical}", path=canonical)
        if not target.is_dir():
            return tool_error(
                "not_a_directory",
                f"{canonical} is a file. Use workspace_delete_file to remove it.",
                path=canonical,
            )
        shutil.rmtree(target)
        return tool_ok(f"deleted directory {canonical} (recursive)", path=canonical)

    return ToolSpec(
        name="workspace_delete_dir",
        description=(
            "SIGNATURE: workspace_delete_dir(relative_path). "
            "Delete a directory and ALL its contents (recursive) from the conversation "
            "workspace. Cannot delete outside the workspace folder, nor the workspace root "
            "itself. Refuses plain files — use workspace_delete_file for those. "
            "Destructive: prefer deleting specific files unless you intend to remove the whole tree."
        ),
        parameters={
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": "Path of the directory to delete, relative to workspace root.",
                },
            },
            "required": ["relative_path"],
        },
        handler=_handler,
    )
