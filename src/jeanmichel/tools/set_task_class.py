"""Tool: set_task_class — persist the router's complexity classification for a request.

The router (jean-michel) calls this once before any delegation to record
whether the request is single_fact, medium_task, or deep_research.
The orchestrator uses this classification to enforce structural gates:
  - deep_research → manage_todo_list required before first delegate_to.
"""

from __future__ import annotations

from .. import db as _db
from ._base import ToolSpec
from ._errors import tool_error, tool_ok

_VALID = frozenset({"single_fact", "medium_task", "deep_research"})


def make_spec(conv_id: str) -> ToolSpec:
    """Return a ToolSpec bound to the given conversation id."""

    def _handler(task_class: str) -> str:
        if task_class not in _VALID:
            return tool_error(
                "invalid_task_class",
                f"Unknown task_class {task_class!r}. Must be one of: {sorted(_VALID)}",
            )
        with _db.connect() as conn:
            _db.set_task_class(conn, conv_id, task_class)
        return tool_ok(f"Request classified as {task_class!r}.", task_class=task_class)

    return ToolSpec(
        name="set_task_class",
        description=(
            "Persist your complexity classification for this request. "
            "Call this once, before any delegation, with one of: "
            "'single_fact' (one-step direct answer or single tool call), "
            "'medium_task' (2-3 independent delegations, no dependent chain), "
            "'deep_research' (chained phases, or structured document output, or 3+ agents)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task_class": {
                    "type": "string",
                    "enum": ["single_fact", "medium_task", "deep_research"],
                    "description": "Complexity class of the request.",
                },
            },
            "required": ["task_class"],
        },
        handler=_handler,
    )
