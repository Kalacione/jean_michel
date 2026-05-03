"""Tool: workspace_create_file — create a new file in the conversation workspace.

Sandboxed: cannot escape the workspace/ folder. Refuses to overwrite.
Quota-aware: refuses if the write would exceed WORKSPACE_QUOTA_BYTES.
"""

from __future__ import annotations

import json
from pathlib import Path

from ._base import ToolSpec
from ._workspace import quota_remaining, safe_resolve, workspace_root_for


def make_spec(conv_folder: Path, has_write_grant: bool = False) -> ToolSpec:
    """Return a ToolSpec bound to `conv_folder`."""

    def _handler(relative_path: str, content: str, description: str = "") -> str:
        if not has_write_grant:
            return json.dumps({"error": "Write access not granted for this agent."})
        ws_root = workspace_root_for(conv_folder)
        try:
            target = safe_resolve(ws_root, relative_path)
        except ValueError as e:
            return json.dumps({"error": str(e)})
        if target.exists():
            return json.dumps({"error": f"File already exists: {relative_path}. Use workspace_str_replace to edit."})
        encoded = content.encode("utf-8")
        if len(encoded) > quota_remaining(ws_root):
            return json.dumps({"error": "Quota exceeded. No space left in workspace."})
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded)
        return json.dumps({"path": relative_path, "bytes_written": len(encoded)})

    return ToolSpec(
        name="workspace_create_file",
        description=(
            "Create a new file in the conversation workspace. "
            "The workspace is a sandbox folder; cannot write outside it. "
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
