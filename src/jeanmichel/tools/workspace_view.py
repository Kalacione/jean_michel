"""Tool: workspace_view — read a file (workspace or conversation root) or list a directory.

Read-only. Accepts files from both the workspace/ subfolder and the conversation root.
Supports view_range=[start, end] for line-range reads (1-indexed; -1 = last line).
"""

from __future__ import annotations

import json
from pathlib import Path

from ._base import ToolSpec
from ._workspace import workspace_root_for


def make_spec(conv_folder: Path) -> ToolSpec:
    """Return a ToolSpec bound to `conv_folder`. No write grant required."""

    def _handler(
        relative_path: str,
        view_range: list[int] | None = None,
        max_bytes: int = 100_000,
    ) -> str:
        ws_root = workspace_root_for(conv_folder)
        conv_root = conv_folder.resolve()

        # Resolve inside workspace first, then fall back to conversation root.
        candidate_ws = (ws_root / relative_path).resolve()
        candidate_conv = (conv_root / relative_path).resolve()

        if candidate_ws.is_relative_to(ws_root) and (candidate_ws.exists()):
            target = candidate_ws
            display_path = str(candidate_ws.relative_to(ws_root))
        elif candidate_conv.is_relative_to(conv_root) and (candidate_conv.exists()):
            target = candidate_conv
            display_path = str(candidate_conv.relative_to(conv_root))
        elif candidate_ws.is_relative_to(ws_root):
            # Path valid but doesn't exist yet — treat as "not found"
            return json.dumps({"error": f"Not found: {relative_path}"})
        elif candidate_conv.is_relative_to(conv_root):
            return json.dumps({"error": f"Not found: {relative_path}"})
        else:
            return json.dumps({"error": "Path escapes allowed roots."})

        if target.is_dir():
            entries = []
            for p in sorted(target.iterdir()):
                entry: dict = {"name": p.name, "type": "directory" if p.is_dir() else "file"}
                if p.is_file():
                    entry["size_bytes"] = p.stat().st_size
                entries.append(entry)
            return json.dumps({"directory": display_path, "entries": entries})

        # File read
        data = target.read_bytes()
        truncated = len(data) > max_bytes
        data = data[:max_bytes]
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            return json.dumps({"error": "File is not valid UTF-8."})

        if view_range is not None:
            lines = content.splitlines(keepends=True)
            start = max(1, view_range[0]) - 1  # convert to 0-indexed
            end = view_range[1] if view_range[1] != -1 else len(lines)
            content = "".join(lines[start:end])
            truncated = False  # range read is already bounded

        return json.dumps({"path": display_path, "content": content, "truncated": truncated})

    return ToolSpec(
        name="workspace_view",
        description=(
            "Read a file from the workspace or the conversation root folder, "
            "or list a directory. "
            "Supports view_range=[start_line, end_line] (1-indexed; -1 = last line). "
            "Read-only — does not require a write grant."
        ),
        parameters={
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": (
                        "Path relative to workspace root (for workspace files) "
                        "or conversation folder (for conversation artifacts). "
                        "Pass empty string '' to list the workspace root."
                    ),
                },
                "view_range": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Optional [start_line, end_line] (1-indexed). -1 = last line.",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Maximum bytes to read. Default 100000.",
                },
            },
            "required": ["relative_path"],
        },
        handler=_handler,
    )
