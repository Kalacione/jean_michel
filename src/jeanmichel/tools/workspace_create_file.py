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
        # plan.md is owned exclusively by plan_update
        if target == safe_resolve(ws_root, "plan.md"):
            return tool_error(
                "reserved_path",
                (
                    "plan.md is managed by the plan_update tool. "
                    "Use plan_update(action='init', ...) to create it, "
                    "plan_update(action='mark', ...) to update steps."
                ),
                action_required="plan_update",
            )
        if target.exists():
            try:
                existing = target.read_text(encoding="utf-8")[:6000]
            except OSError:
                existing = None
            extra: dict = {"action_required": "workspace_str_replace"}
            if existing is not None:
                extra["existing_content"] = existing
            return tool_error(
                "file_exists",
                (
                    f"File already exists: {relative_path}. "
                    "DO NOT call workspace_create_file again. "
                    "Call workspace_str_replace(relative_path, old_str, new_str) to update it."
                ),
                **extra,
            )
        encoded = content.encode("utf-8")
        if len(encoded) > quota_remaining(ws_root):
            return tool_error("quota_exceeded", "Quota exceeded. No space left in workspace.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded)
        return tool_ok(
            f"wrote {relative_path} ({len(encoded)} bytes)",
            path=relative_path,
            bytes_written=len(encoded),
        )

    return ToolSpec(
        name="workspace_create_file",
        description=(
            "Create a new file in the conversation workspace (the 'workspace/' folder "
            "of the current conversation — this is where deliverable output files go). "
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
