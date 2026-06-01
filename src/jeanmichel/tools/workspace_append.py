"""Tool: workspace_append — append text to an existing workspace file.

Sandboxed: cannot escape the workspace/ folder. Quota-aware.
No string matching: the content is appended at the end of the file.
A separator newline is inserted if the existing file doesn't end with one,
so successive appends don't run lines together.
"""

from __future__ import annotations

from pathlib import Path

from ._base import ToolSpec
from ._errors import tool_error, tool_ok
from ._workspace import quota_remaining, safe_resolve, workspace_root_for


def make_spec(conv_folder: Path, has_write_grant: bool = False) -> ToolSpec:
    """Return a ToolSpec bound to `conv_folder`."""

    def _handler(relative_path: str, content: str) -> str:
        if not has_write_grant:
            return tool_error("no_write_grant", "Write access not granted for this agent.")
        ws_root = workspace_root_for(conv_folder)
        try:
            target = safe_resolve(ws_root, relative_path)
        except ValueError as e:
            msg = str(e)
            code = "absolute_path" if "absolute" in msg.lower() else "path_escape"
            return tool_error(code, msg)
        if not target.exists():
            canonical = target.relative_to(ws_root).as_posix()
            return tool_error(
                "file_not_found",
                (
                    f"File not found: {canonical}. "
                    "To create a new file, call workspace_create_file(relative_path, content)."
                ),
                action_required="workspace_create_file",
                path=canonical,
            )
        try:
            existing = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return tool_error("not_utf8", "File is not valid UTF-8.", relative_path=relative_path)
        # Auto-separator: ensure the existing content ends with a newline before appending.
        payload = "\n" + content if existing and not existing.endswith("\n") else content
        encoded_payload = payload.encode("utf-8")
        if len(encoded_payload) > quota_remaining(ws_root):
            return tool_error(
                "quota_exceeded",
                "Quota exceeded. The append would not fit in the workspace.",
            )
        # Append atomically via read-modify-write to a tmp file.
        updated = existing + payload
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(updated, encoding="utf-8")
        tmp.replace(target)
        bytes_after = len(updated.encode("utf-8"))
        canonical = target.relative_to(ws_root).as_posix()
        return tool_ok(
            f"appended {len(encoded_payload)} bytes to {canonical} "
            f"(file now {bytes_after} bytes)",
            path=canonical,
            bytes_appended=len(encoded_payload),
            bytes_after=bytes_after,
        )

    return ToolSpec(
        name="workspace_append",
        description=(
            "SIGNATURE: workspace_append(relative_path, content). "
            "Append text to the end of an existing workspace file. "
            "Use this to add new findings, sections, or rows to a file you've already created — "
            "this is the natural way to extend a deliverable progressively. "
            "Parameter names are EXACT — the body parameter is 'content', not 'text' or 'data'. "
            "No string matching: the content is appended as-is at the end of the file. "
            "If the file does not end with a newline, one is inserted automatically before the "
            "appended content so successive appends don't run together. "
            "Fails if the file does not exist — call workspace_create_file first."
        ),
        parameters={
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": "Path relative to workspace root (e.g. 'notes.md').",
                },
                "content": {
                    "type": "string",
                    "description": "Text to append at the end of the file (UTF-8).",
                },
            },
            "required": ["relative_path", "content"],
        },
        handler=_handler,
    )
