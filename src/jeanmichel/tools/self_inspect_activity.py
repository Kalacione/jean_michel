"""Tool: self_inspect_activity — conversation history + sandbox audit.

Scope-restricted variant of self_inspect. Returns runtime/operational data:
conversation stats, failure counts, ask_human frequency, and sandbox execution
audit. More sensitive than config — exposes conversation history and user patterns.
"""

from __future__ import annotations

from ._base import ToolSpec
from . import self_inspect as _core

SPEC = ToolSpec(
    name="self_inspect_activity",
    description=(
        "Query Jean-Michel's runtime activity and operational history. "
        "scope='conversations': activity stats (failure counts, ask_human frequency, top agents). "
        "scope='sandbox': execution audit of the last 50 sandbox runs. "
        "scope='recent_summaries': content of the last 5 conversation summary.md files — "
        "use this to observe actual conversation quality and recurring user needs."
    ),
    parameters={
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["conversations", "sandbox", "recent_summaries"],
                "description": (
                    "'conversations' for stats, 'sandbox' for execution audit, "
                    "'recent_summaries' for conversation content."
                ),
            },
        },
        "required": ["scope"],
    },
    handler=lambda scope: _core._handler(scope=scope),
)
