"""Tool: todo_write — the orchestrator's living plan (PDCA).

Whole-list replace (idempotent): the router resends the COMPLETE intended plan
on every update — mark a step done, append a newly-discovered step, re-scope,
reorder, retry. At most one item may be ``in_progress``. Stored at
``conv_folder/todo.json`` (conversation root, NOT the workspace). The router is
the SOLE writer; workers propose changes via ``report_back.suggested_todo_updates``.
When every step is done, the plan is cleared.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..todo import clear_todo, normalize_items, save_todo
from ._base import ToolSpec
from ._errors import tool_error, tool_ok


def make_spec(conv_folder: Path) -> ToolSpec:
    """Return a ToolSpec bound to `conv_folder`."""

    def _handler(goal: str = "", items: Any = None, **_extra: Any) -> str:
        if not isinstance(goal, str) or not goal.strip():
            return tool_error("invalid_goal", "todo_write requires a non-empty 'goal' string.")
        norm, err = normalize_items(items)
        if err is not None:
            return tool_error("invalid_items", err)
        assert norm is not None  # err is None ⇒ norm is set
        if all(it["status"] == "done" for it in norm):
            clear_todo(conv_folder)
            return tool_ok(
                f"plan complete ({len(norm)} items done) — TODO cleared",
                items=len(norm),
                all_done=True,
            )
        save_todo(conv_folder, goal.strip(), norm)
        done = sum(1 for it in norm if it["status"] == "done")
        in_prog = next((it["text"] for it in norm if it["status"] == "in_progress"), None)
        summary = f"plan saved ({done}/{len(norm)} done)"
        if in_prog:
            summary += f"; in progress: {in_prog}"
        return tool_ok(summary, items=len(norm), done=done)

    return ToolSpec(
        name="todo_write",
        description=(
            "SIGNATURE: todo_write(goal, items). "
            "Maintain your living plan for a complex / multi-step task. You OWN this plan: "
            "decompose the task into 3-7 scoped steps BEFORE delegating, then call todo_write "
            "AGAIN after every worker report to keep it current — keeping the plan up to date is "
            "the key to a good result. "
            "WHOLE-LIST REPLACE: always pass the COMPLETE intended list (it overwrites the previous "
            "one). Use it to mark a step done, add a newly-discovered step, re-scope, reorder, or "
            "retry a failed one. Keep EXACTLY ONE step 'in_progress' (the one you are delegating "
            "now). Fold in any 'suggested_todo_updates' a worker returned. "
            "When every step is 'done' the plan is cleared and you write the final answer."
        ),
        parameters={
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "One sentence naming the overall objective of the plan.",
                },
                "items": {
                    "type": "array",
                    "description": (
                        "The COMPLETE ordered list of steps (REPLACES the previous list)."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Optional stable short id (assigned by position if omitted).",
                            },
                            "text": {
                                "type": "string",
                                "description": "Imperative one-line description of the step.",
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "done"],
                            },
                        },
                        "required": ["text", "status"],
                    },
                },
            },
            "required": ["goal", "items"],
        },
        handler=_handler,
    )
