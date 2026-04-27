"""Render the prompt skeleton from agent + paradigms + runtime context."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .config import MAX_RECURSION_DEPTH, UserProfile
from .models import Agent, Paradigm
from .tools import AGENT_TOOL_GRANTS, ToolSpec


# ---- Control tool declarations (always available to LLM agents) -----------

CONTROL_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "ask_human",
            "description": "Pause the request and ask the human a single question. "
                           "`why` is mandatory and must explain what is blocked without it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["question", "why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_to",
            "description": "Delegate a subtask to another specialist agent. "
                           "Multiple delegate_to calls in the same turn run in parallel.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_code": {"type": "string"},
                    "briefing": {"type": "string", "description": "Mission text in English."},
                    "support_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Paths relative to the conversation folder.",
                    },
                    "expected": {"type": "string", "description": "Expected outcome shape."},
                },
                "required": ["agent_code", "briefing", "expected"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "return_to_user",
            "description": "Deliver the final answer to the human. Use the human's detected language.",
            "parameters": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        },
    },
]


@dataclass
class PromptContext:
    agent: Agent
    paradigms: list[Paradigm]
    user_profile: UserProfile
    detected_language: str
    conversation_id: str
    conversation_folder: str
    request_id: str
    parent_request_id: str | None
    depth: int
    sender: str
    expected_outcome: str | None
    support_files: list[str]
    inbound_text: str
    tool_registry: dict[str, ToolSpec]


def render_directives(paradigms: list[Paradigm]) -> str:
    """Group paradigms by category and render as markdown."""
    out: list[str] = []
    current_category: tuple[str, str] | None = None
    for p in paradigms:
        cat_key = (p.section_code, p.category_code)
        if cat_key != current_category:
            out.append(f"\n## {p.category_title}")
            current_category = cat_key
        out.append(p.content.strip())
    return "\n".join(out).strip()


def render_system_prompt(ctx: PromptContext) -> str:
    """Render the consolidated system block."""
    a = ctx.agent
    support_files_block = (
        "\n".join(f"- {p}" for p in ctx.support_files) if ctx.support_files else "(none)"
    )

    return (
        f"# IDENTITY\n"
        f"You are {a.name} ({a.code}).\n"
        f"Role: {a.role}.\n"
        f"Mission: {a.mission}\n\n"
        f"# CONTEXT\n"
        f"## Human\n"
        f"{ctx.user_profile.description}\n"
        f"Detected language for user-facing reply: {ctx.detected_language}\n\n"
        f"## Conversation\n"
        f"- conversation_id: {ctx.conversation_id}\n"
        f"- request_id: {ctx.request_id}\n"
        f"- parent_request_id: {ctx.parent_request_id or 'none'}\n"
        f"- recursion_depth: {ctx.depth}/{MAX_RECURSION_DEPTH}\n"
        f"- conversation_folder: {ctx.conversation_folder}\n\n"
        f"## Machine\n"
        f"- os: {platform.system()} {platform.release()}\n"
        f"- cwd: {os.getcwd()}\n"
        f"- utc_now: {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}\n\n"
        f"## Inbound briefing\n"
        f"from: {ctx.sender}\n"
        f"expected: {ctx.expected_outcome or '(unspecified)'}\n"
        f"support_files:\n{support_files_block}\n\n"
        f"{ctx.inbound_text}\n\n"
        f"# DIRECTIVES\n"
        f"{render_directives(ctx.paradigms)}\n\n"
        f"# OUTPUT CONTRACT\n"
        f"- Reflect first in your thought channel; surface assumptions, traps, biases.\n"
        f"- If you must clarify with the user: call ask_human(question, why). "
        f"One question only. `why` is mandatory.\n"
        f"- If task belongs to another specialist: call delegate_to(...). "
        f"Multiple parallel delegate_to calls allowed in the same turn.\n"
        f"- If task is yours and complete: call return_to_user(answer).\n"
        f"- Inter-agent briefings: English. User-facing answer: "
        f"{ctx.detected_language}.\n"
    )


def tools_payload_for_agent(agent_code: str,
                            registry: dict[str, ToolSpec]) -> list[dict[str, Any]]:
    """Build the tools payload (control tools + agent-granted native tools)."""
    payload = list(CONTROL_TOOLS_SCHEMA)
    for tool_name in AGENT_TOOL_GRANTS.get(agent_code, []):
        spec = registry.get(tool_name)
        if spec is None:
            continue
        payload.append({
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        })
    return payload
