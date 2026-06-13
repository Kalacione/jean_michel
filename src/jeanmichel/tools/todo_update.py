"""Tool: todo_update — flip ONE plan item's status (granular PDCA update).

Granular complement to ``todo_write`` (whole-list replace). Small models struggle
to re-emit the COMPLETE list just to mark one step done, so they tend to only
append and never complete. ``todo_update(item_id, status)`` changes a single
item's status — the natural call after a delegation returns: mark the finished
step ``done``, set the next ``in_progress``. When every item is done the plan is
cleared. Same owner rule as todo_write (the router writes it).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..todo import clear_todo, set_status
from ._base import ToolSpec
from ._errors import tool_error, tool_ok


def make_spec(conv_folder: Path) -> ToolSpec:
    """Return a ToolSpec bound to `conv_folder`."""

    def _handler(item_id: str = "", status: str = "", **_extra: Any) -> str:
        if not str(item_id).strip():
            return tool_error("invalid_item", "todo_update requires an 'item_id'.")
        todo, err = set_status(conv_folder, str(item_id), status)
        if err is not None:
            return tool_error("todo_update_rejected", err)
        assert todo is not None
        items = todo["items"]
        if all(it["status"] == "done" for it in items):
            clear_todo(conv_folder)
            return tool_ok(
                f"item {item_id} done — plan complete ({len(items)} items), TODO cleared",
                all_done=True,
            )
        done = sum(1 for it in items if it["status"] == "done")
        nxt = next((it["text"] for it in items if it["status"] == "in_progress"), None) \
            or next((it["text"] for it in items if it["status"] == "pending"), None)
        summary = f"item {item_id} → {status} ({done}/{len(items)} done)"
        if nxt:
            summary += f"; next: {nxt}"
        return tool_ok(summary, done=done, items=len(items))

    return ToolSpec(
        name="todo_update",
        description=(
            "SIGNATURE: todo_update(item_id, status). "
            "Flip ONE plan item's status without re-sending the whole list (granular "
            "complement to todo_write). status ∈ {pending, in_progress, done}. "
            "Call it right after a delegation returns: mark the finished step 'done', then "
            "set the next step 'in_progress'. At most one item may be 'in_progress'. "
            "When the last item is marked done, the plan is cleared and you write the final answer."
        ),
        parameters={
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "string",
                    "description": "The id of the plan item to update (as shown in the TODO recap).",
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "done"],
                },
            },
            "required": ["item_id", "status"],
        },
        handler=_handler,
    )
