"""Tool: conv_read_file — reads a file inside the current conversation folder.

Sandboxed: cannot escape the conversation folder (path traversal guard).
"""

from __future__ import annotations

from pathlib import Path

from ._base import ToolSpec
from ._errors import tool_error, tool_ok


def make_spec(conv_folder: Path) -> ToolSpec:
    """Return a ToolSpec bound to `conv_folder`. Must be called per-request."""

    def _handler(relative_path: str, max_bytes: int = 100_000) -> str:
        target = (conv_folder / relative_path).resolve()
        # Path traversal guard — hard boundary at the conversation folder.
        if not str(target).startswith(str(conv_folder.resolve())):
            return tool_error("path_escape", "Path escapes conversation folder.")
        if not target.exists():
            return tool_error("file_not_found", f"Not found: {relative_path}",
                              relative_path=relative_path)
        data = target.read_bytes()[:max_bytes]
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            return tool_error("not_utf8", "File is not valid UTF-8.",
                              relative_path=relative_path)
        return tool_ok(
            f"read {relative_path} ({len(content)} chars)",
            path=relative_path,
            content=content,
        )

    return ToolSpec(
        name="conv_read_file",
        description=(
            "Read a file located inside the current conversation folder. "
            "Use the relative path provided as a support_file in the briefing. "
            "Cannot access files outside this conversation's folder."
        ),
        parameters={
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": "Path relative to the conversation folder.",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Maximum number of bytes to read. Default 100000.",
                },
            },
            "required": ["relative_path"],
        },
        handler=_handler,
    )
