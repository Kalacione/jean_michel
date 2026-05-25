"""Tool: workspace_list — list workspace contents as a tree (max 2 levels deep).

Read-only. Supports optional sub-path to list a specific subdirectory.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ._base import ToolSpec
from ._errors import tool_error, tool_ok
from ._workspace import safe_resolve, workspace_root_for


def make_spec(conv_folder: Path) -> ToolSpec:
    """Return a ToolSpec bound to `conv_folder`. No write grant required."""

    def _handler(sub_path: str = "") -> str:
        ws_root = workspace_root_for(conv_folder)
        if sub_path:
            try:
                start = safe_resolve(ws_root, sub_path)
            except ValueError as e:
                msg = str(e)
                code = "absolute_path" if "absolute" in msg.lower() else "path_escape"
                return tool_error(code, msg)
            if not start.exists():
                return tool_error("file_not_found", f"Not found: {sub_path}",
                                  relative_path=sub_path)
            if not start.is_dir():
                return tool_error("file_not_found", f"Not a directory: {sub_path}",
                                  relative_path=sub_path)
        else:
            start = ws_root

        def _entry(p: Path, depth: int) -> dict:
            stat = p.stat()
            node: dict = {
                "name": p.name,
                "type": "directory" if p.is_dir() else "file",
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            if p.is_file():
                node["size_bytes"] = stat.st_size
            if p.is_dir() and depth < 2:
                node["children"] = [_entry(child, depth + 1) for child in sorted(p.iterdir())]
            return node

        children = [_entry(p, 1) for p in sorted(start.iterdir())]
        display = sub_path or ""
        label = f" in {display}" if display else ""
        return tool_ok(
            f"{len(children)} entries{label}",
            workspace=display,
            entries=children,
        )

    return ToolSpec(
        name="workspace_list",
        description=(
            "List the workspace directory as a tree (max 2 levels deep). "
            "Each entry includes name, type, size_bytes (files), and modified_at. "
            "Pass sub_path to list a specific subdirectory. "
            "Read-only — does not require a write grant."
        ),
        parameters={
            "type": "object",
            "properties": {
                "sub_path": {
                    "type": "string",
                    "description": "Sub-directory to list, e.g. 'src/tools'. Leave empty to list the workspace root.",
                },
            },
            "required": [],
        },
        handler=_handler,
    )
