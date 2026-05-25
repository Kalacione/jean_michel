"""Tool: self_inspect_config — agent roster + paradigm configuration.

Scope-restricted variant of self_inspect. Returns only structural/config data:
agents (with tools, paradigm counts, sandbox grants) and paradigms.
Safe to grant to any agent that needs to understand the system's configuration.
"""

from __future__ import annotations

from . import self_inspect as _core
from ._base import ToolSpec

SPEC = ToolSpec(
    name="self_inspect_config",
    description=(
        "Query Jean-Michel's structural configuration. "
        "scope='agents': full agent roster with tool grants, paradigm counts, sandbox config. "
        "scope='paradigms': all active paradigms grouped by section and category."
    ),
    parameters={
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["agents", "paradigms"],
                "description": "'agents' for roster + grants, 'paradigms' for paradigm catalog.",
            },
        },
        "required": ["scope"],
    },
    handler=lambda scope: _core._handler(scope=scope),
)
