"""Tool: workspace_create_dir — create a directory in the conversation workspace.

Sandboxed: cannot escape the workspace/ folder. Idempotent (no error if the
directory already exists). Parents are created as needed.
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
        if target.exists() and not target.is_dir():
            return tool_error(
                "file_exists",
                f"A file already exists at {canonical} — cannot create a directory there.",
                path=canonical,
            )
        target.mkdir(parents=True, exist_ok=True)
        return tool_ok(f"created directory {canonical}", path=canonical)

    return ToolSpec(
        name="workspace_create_dir",
        description=(
            "SIGNATURE: workspace_create_dir(relative_path). "
            "Create a directory (and any missing parents) in the conversation workspace. "
            "Idempotent — succeeds if the directory already exists. Cannot create outside "
            "the workspace folder. (Files auto-create their parent dirs, so you only need "
            "this for empty directories.)"
        ),
        parameters={
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": "Directory path relative to workspace root (e.g. 'src/utils').",
                },
            },
            "required": ["relative_path"],
        },
        handler=_handler,
    )
