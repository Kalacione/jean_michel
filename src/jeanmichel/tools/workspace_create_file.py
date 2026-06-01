"""Tool: workspace_create_file — create a new file in the conversation workspace.

Sandboxed: cannot escape the workspace/ folder. Refuses to overwrite.
Quota-aware: refuses if the write would exceed WORKSPACE_QUOTA_BYTES.
"""

from __future__ import annotations

from pathlib import Path

from ._base import ToolSpec
from ._errors import tool_error, tool_ok
from ._workspace import quota_remaining, safe_resolve, workspace_root_for


def make_spec(conv_folder: Path, has_write_grant: bool = False) -> ToolSpec:
    """Return a ToolSpec bound to `conv_folder`."""

    def _handler(relative_path: str, content: str, description: str = "") -> str:
        if not has_write_grant:
            return tool_error("no_write_grant", "Write access not granted for this agent.")
        ws_root = workspace_root_for(conv_folder)
        try:
            target = safe_resolve(ws_root, relative_path)
        except ValueError as e:
            msg = str(e)
            code = "absolute_path" if "absolute" in msg.lower() else "path_escape"
            return tool_error(code, msg)
        if target.exists():
            try:
                existing = target.read_text(encoding="utf-8")[:6000]
            except OSError:
                existing = None
            extra: dict = {
                "action_required": "workspace_append",
                "alternatives": ["workspace_append", "workspace_str_replace"],
            }
            if existing is not None:
                extra["existing_content"] = existing
            canonical = target.relative_to(ws_root).as_posix()
            return tool_error(
                "file_exists",
                (
                    f"File already exists: {canonical}. "
                    "DO NOT call workspace_create_file again. "
                    "To ADD new content at the end, call workspace_append(relative_path, content). "
                    "To MODIFY existing content, call workspace_str_replace(relative_path, old_str, new_str)."
                ),
                path=canonical,
                **extra,
            )
        encoded = content.encode("utf-8")
        if len(encoded) > quota_remaining(ws_root):
            return tool_error("quota_exceeded", "Quota exceeded. No space left in workspace.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded)
        canonical = target.relative_to(ws_root).as_posix()
        return tool_ok(
            f"wrote {canonical} ({len(encoded)} bytes)",
            path=canonical,
            bytes_written=len(encoded),
        )

    return ToolSpec(
        name="workspace_create_file",
        description=(
            "SIGNATURE: workspace_create_file(relative_path, content, description?). "
            "Create a new file in the conversation workspace (the 'workspace/' folder "
            "of the current conversation — this is where deliverable output files go). "
            "Parameter names are EXACT — the body parameter is 'content', not 'file_content' or 'text'. "
            "Cannot write outside this folder. "
            "Fails if the file already exists — use workspace_str_replace to edit. "
            "Sub-directories are created automatically."
        ),
        parameters={
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": "Path relative to workspace root (e.g. 'notes.md' or 'data/results.json').",
                },
                "content": {
                    "type": "string",
                    "description": "File content (UTF-8 text).",
                },
                "description": {
                    "type": "string",
                    "description": "Optional one-line description of the file's purpose.",
                },
            },
            "required": ["relative_path", "content"],
        },
        handler=_handler,
    )
