"""Tool: plan_write — author the rich plan document (the orchestrator's reasoning).

The PLAN turn's substantive deliverable. Where ``todo_write`` is a terse, status-
trackable checklist, ``plan_write`` is the DURABLE markdown analysis that carries the
intelligence : a Context/analysis section, the steps WITH detail and rationale, risks,
and a verification section. Stored at ``conv_folder/plan.md`` (conversation root). It is
what the human approves, and it is re-injected into every execution turn (the ``[PLAN]``
block) so the executor works from the reasoning, not from one-liners. The router is the
SOLE writer (same owner rule as todo_write).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..todo import save_plan
from ._base import ToolSpec
from ._errors import tool_error, tool_ok


def make_spec(conv_folder: Path) -> ToolSpec:
    """Return a ToolSpec bound to `conv_folder`."""

    def _handler(markdown: str = "", **_extra: Any) -> str:
        if not isinstance(markdown, str) or not markdown.strip():
            return tool_error(
                "invalid_plan",
                "plan_write requires a non-empty 'markdown' plan document.",
            )
        save_plan(conv_folder, markdown.strip())
        lines = markdown.strip().count("\n") + 1
        return tool_ok(f"plan saved ({lines} lines)", lines=lines)

    return ToolSpec(
        name="plan_write",
        description=(
            "SIGNATURE: plan_write(markdown). "
            "Author the DURABLE plan document — the reasoning the human approves and that "
            "guides execution. Write substantive markdown, NOT a bare checklist: "
            "a '## Context' section (the problem + your analysis and chosen approach), the "
            "concrete steps WITH detail and rationale (how each will be done and why), risks "
            "or open questions, and a '## Verification' section (how the result will be checked). "
            "Pair it with todo_write (the terse trackable steps). Re-call plan_write to revise "
            "the plan as exploration or execution reveals more. It overwrites the previous plan."
        ),
        parameters={
            "type": "object",
            "properties": {
                "markdown": {
                    "type": "string",
                    "description": (
                        "The full plan as markdown: Context/analysis, detailed steps with "
                        "rationale, risks, verification. Replaces the previous plan document."
                    ),
                },
            },
            "required": ["markdown"],
        },
        handler=_handler,
    )
