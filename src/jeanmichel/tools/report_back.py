"""`report_back` — control-verb schema declaration + argument validation.

The subagent loop in `orchestrator_v2` intercepts this tool call and uses it
as the termination signal. The validation here mirrors the rule in
`OnDelegateReturn` hook (belt and suspenders) so a malformed report_back is
caught before it pollutes the parent's messages[].
"""

from __future__ import annotations

from typing import Any

REPORT_BACK_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "report_back",
        "description": (
            "Conclude your work and return a structured report to your caller. "
            "This is the only way for a specialist to exit. The main agent "
            "(jean-michel) does NOT use this — it concludes by emitting an "
            "assistant turn without tool_calls."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": (
                        "1-3 sentences naming the headline finding. Not 'I did X' — "
                        "the actual conclusion or the content of what you produced."
                    ),
                },
                "files_produced": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Workspace files you wrote, relative to workspace root."
                    ),
                },
                "confidence": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Self-assessment of how completely you delivered the briefing.",
                },
                "low_confidence_reason": {
                    "type": "string",
                    "description": (
                        "REQUIRED when confidence='low'. One synthetic sentence "
                        "explaining what's missing or uncertain. Not a recap of your "
                        "reasoning — just the gap."
                    ),
                },
                "suggested_todo_updates": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "OPTIONAL. If, while doing your sub-task, you discovered work the "
                        "overall plan likely needs — a missing prerequisite, a step that "
                        "should be split, an extra step, or a blocker — list those needs "
                        "here as short phrases, in terms of the WORK (not plan item numbers; "
                        "you don't see the plan). Your caller owns the plan and will fold "
                        "them in. Omit when there's nothing to flag."
                    ),
                },
            },
            "required": ["summary", "confidence"],
        },
    },
}


def validate_report_back_args(args: dict[str, Any]) -> str | None:
    """Validate `report_back` arguments. Returns an error message or None.

    Used by the subagent loop to reject malformed calls before they cristallise
    as a SubResult. The same rules are re-enforced by the `OnDelegateReturn`
    hook on the parent side.
    """
    if not isinstance(args, dict):
        return "report_back arguments must be an object."

    summary = args.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return "report_back requires a non-empty 'summary' string."

    confidence = args.get("confidence")
    if confidence not in ("low", "medium", "high"):
        return (
            "report_back 'confidence' must be one of 'low', 'medium', 'high'. "
            f"Got: {confidence!r}."
        )

    if confidence == "low":
        reason = args.get("low_confidence_reason")
        if not isinstance(reason, str) or not reason.strip():
            return (
                "report_back with confidence='low' requires a non-empty "
                "'low_confidence_reason' field — one synthetic sentence "
                "explaining what is missing."
            )

    files = args.get("files_produced")
    if files is not None and not isinstance(files, list):
        return "report_back 'files_produced' must be a list if provided."

    suggestions = args.get("suggested_todo_updates")
    if suggestions is not None and (
        not isinstance(suggestions, list)
        or not all(isinstance(s, str) for s in suggestions)
    ):
        return "report_back 'suggested_todo_updates' must be a list of strings if provided."

    return None
