"""`delegate_to` — control-verb schema declaration.

The execution lives in `orchestrator_v2.spawn_subagent`. This module exposes
the Ollama tool schema so the orchestrator can include it in the tools
payload sent to the LLM.

Nothing here knows about agents, conversations, or the call stack — it is a
pure descriptor.
"""

from __future__ import annotations

from typing import Any

DELEGATE_TO_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "delegate_to",
        "description": (
            "Delegate a sub-task to another agent. Multiple delegate_to calls "
            "in the same assistant turn are processed sequentially. The return "
            "value is a structured object "
            "{agent, summary, files_produced, confidence, low_confidence_reason?}. "
            "Use the files_produced paths via workspace_view if you need the "
            "raw findings; do NOT re-delegate the same question with a wider "
            "scope hoping for more — narrow the scope instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_code": {
                    "type": "string",
                    "description": (
                        "Code of the agent to delegate to. Must appear in the "
                        "## Delegation targets section of your system prompt."
                    ),
                },
                "briefing": {
                    "type": "string",
                    "description": (
                        "Mission text in English. Include the concrete deliverable, "
                        "the entity/topic, time window if any. Do NOT include "
                        "language instructions for the receiving agent — output "
                        "language is handled automatically."
                    ),
                },
                "expected": {
                    "type": "string",
                    "description": (
                        "Brief description of the deliverable expected from the "
                        "subagent (1 line)."
                    ),
                },
                "support_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Workspace file paths the subagent should read for context. "
                        "Only reference files that physically exist (written by you "
                        "or a previous specialist this turn)."
                    ),
                },
            },
            "required": ["agent_code", "briefing"],
        },
    },
}
