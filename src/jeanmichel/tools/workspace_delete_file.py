"""Tool: workspace_delete_file — delete a single file in the conversation workspace.

Sandboxed: cannot escape the workspace/ folder. Refuses directories (use
workspace_delete_dir for those).
"""

from __future__ import annotations

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
            return tool_error("file_not_found", f"File not found: {canonical}", path=canonical)
        if target.is_dir():
            return tool_error(
                "is_a_directory",
                f"{canonical} is a directory. Use workspace_delete_dir to remove it.",
                path=canonical,
            )
        target.unlink()
        return tool_ok(f"deleted file {canonical}", path=canonical)

    return ToolSpec(
        name="workspace_delete_file",
        description=(
            "SIGNATURE: workspace_delete_file(relative_path). "
            "Delete a single file from the conversation workspace. Cannot delete outside "
            "the workspace folder. Refuses directories — use workspace_delete_dir for those."
        ),
        parameters={
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": "Path of the file to delete, relative to workspace root.",
                },
            },
            "required": ["relative_path"],
        },
        handler=_handler,
    )
