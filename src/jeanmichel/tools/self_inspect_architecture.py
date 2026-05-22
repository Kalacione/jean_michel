"""Tool: self_inspect_architecture — README + DB schema.

Scope-restricted variant of self_inspect. Returns only the project's structural
documentation: README.md and db/schema.sql. Safe to grant to any agent that
needs to understand the project's architecture before producing code or proposals.
No activity data, no conversation history, no agent config.
"""

from __future__ import annotations

from ._base import ToolSpec
from . import self_inspect as _core

SPEC = ToolSpec(
    name="self_inspect_architecture",
    description=(
        "Read Jean-Michel's architecture documentation: README.md and db/schema.sql. "
        "Use this to understand table names, column names, agent IDs, and project "
        "structure before writing code, SQL proposals, or documentation. "
        "No parameters required."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    handler=lambda: _core._handler(scope="architecture"),
)
