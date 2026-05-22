"""Render the prompt skeleton from agent + paradigms + runtime context."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .config import MAX_RECURSION_DEPTH, UserProfile
from .models import Agent, Paradigm
from .tools import ToolSpec

# ---- Control tool declarations -------------------------------------------
#
# Each control tool declares which agent roles may use it. The orchestrator's
# system prompt only exposes the relevant tools to each agent, so a finalizer
# (synthesizer, archivist) does not see delegate_to / ask_human.

_ASK_HUMAN: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "ask_human",
        "description": (
            "Pause the request and ask the human for clarification. "
            "Use a single, focused call per request. If multiple "
            "questions are genuinely needed and share the same "
            "blocker, group them in `question` as a coherent list "
            "with one shared `why`. "
            "`why` is mandatory and must explain what is blocked without it. "
            "IMPORTANT: `question` and `why` MUST be written in the human's "
            "detected language (see ## Human section), which may differ from "
            "the internal working language (English)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "why": {"type": "string"},
            },
            "required": ["question", "why"],
        },
    },
}

_DELEGATE_TO: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "delegate_to",
        "description": (
            "Delegate a subtask to another specialist agent. "
            "Multiple delegate_to calls in the same turn run in parallel "
            "(when the orchestrator supports it).\n"
            "The tool result is a structured object: "
            "{agent, artifact, answer, error?}. "
            "Pass the `artifact` value as a support_file when forwarding the "
            "specialist's output to a finalizer (the finalizer reads it via "
            "conv_read_file). Do NOT copy the `answer` content into the "
            "next briefing — pass the artifact path instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_code": {"type": "string"},
                "briefing": {
                    "type": "string",
                    "description": (
                        "Mission text in English. "
                        "Do NOT include language instructions — "
                        "the receiving agent handles output language automatically."
                    ),
                },
                "support_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Filenames (relative to the conversation folder) the "
                        "receiving agent should read with conv_read_file."
                    ),
                },
                "expected": {"type": "string", "description": "Expected outcome shape."},
            },
            "required": ["agent_code", "briefing", "expected"],
        },
    },
}

_RETURN_TO_USER: dict[str, Any] = {
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
}

# Per-role grants for control tools.
# - router: full set
# - specialist: full set (may delegate further, may need clarification)
# - finalizer: only return_to_user (mechanical, no human interaction, no delegation)
_SIGNAL_CONVERGENCE: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "signal_convergence",
        "description": (
            "Call this when you have reached the limit of what you can contribute "
            "at this recursion depth and further analysis would be redundant. "
            "Provide your best synthesis so far and list any questions that remain "
            "open for the parent agent to resolve. "
            "Do NOT call this if you still have meaningful work to do."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "synthesis": {
                    "type": "string",
                    "description": "Your best current synthesis or conclusion.",
                },
                "open_questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Questions or gaps that remain unresolved and should be "
                        "addressed by the parent agent."
                    ),
                },
            },
            "required": ["synthesis"],
        },
    },
}

# Per-role grants for control tools.
# - router: full set
# - specialist: full set (may delegate further, may need clarification)
# - finalizer: only return_to_user (mechanical, no human interaction, no delegation)
_CONTROL_TOOLS_BY_ROLE: dict[str, list[dict[str, Any]]] = {
    "router":     [_ASK_HUMAN, _DELEGATE_TO, _RETURN_TO_USER],
    "specialist": [_ASK_HUMAN, _DELEGATE_TO, _RETURN_TO_USER],
    "finalizer":  [_RETURN_TO_USER],
}


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
    mode: str
    turn_index: int
    sender: str
    expected_outcome: str | None
    support_files: list[str]
    inbound_text: str
    tool_registry: dict[str, ToolSpec]
    available_agents: list[Agent]
    turn_clarifications: list[tuple[str, str]]  # (question, answer) pairs from ask_human this turn


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


def _render_output_contract(role: str) -> str:
    """The OUTPUT CONTRACT block adapts to the agent's role."""
    if role == "finalizer":
        return (
            "# OUTPUT CONTRACT\n"
            "- Reflect first in your thought channel; surface assumptions, traps, biases.\n"
            "- Produce the deliverable and return it via return_to_user(answer).\n"
            "- You do not delegate, you do not call ask_human. Work with the inputs provided.\n"
            "- Inter-agent text: English. Human-facing output: see ## Human detected language.\n"
        )
    # router or specialist
    return (
        "# OUTPUT CONTRACT\n"
        "- Reflect first in your thought channel; surface assumptions, traps, biases.\n"
        "- AGENTS ≠ TOOLS: Entries under 'Delegation targets' are agent codes. "
        "They are NEVER callable as direct tool function names. "
        "Use delegate_to(agent_code='...', briefing='...', expected='...') exclusively.\n"
        "- If you must clarify with the user: call ask_human(question, why). "
        "Only one ask_human call per request; group related questions "
        "sharing the same blocker into a single call. `why` is mandatory.\n"
        "- If task belongs to another specialist: call delegate_to(...). "
        "Multiple parallel delegate_to calls allowed in the same turn.\n"
        "- A delegate_to result is a structured object {agent, artifact, answer}. "
        "When forwarding to a finalizer, pass the `artifact` filename in support_files. "
        "Do NOT copy specialist `answer` content inline into the next briefing.\n"
        "- If task is yours and complete: call return_to_user(answer).\n"
        "- Inter-agent briefings: English. Human-facing output: see ## Human detected language.\n"
    )


def _render_prior_clarifications(clarifications: list[tuple[str, str]]) -> str:
    """Render the clarifications exchanged so far this turn, or empty string."""
    if not clarifications:
        return ""
    lines = "\n".join(f'- Q: "{q}" → A: "{a}"' for q, a in clarifications)
    return f"## Prior clarifications this turn\n{lines}\n\n"


def render_system_prompt(ctx: PromptContext) -> str:
    """Render the consolidated system block."""
    a = ctx.agent
    support_files_block = (
        "\n".join(f"- {p}" for p in ctx.support_files) if ctx.support_files else "(none)"
    )
    specialists = [
        ag for ag in ctx.available_agents
        if ag.code != ctx.agent.code and ag.role in ("specialist", "finalizer")
        and ag.code != "archivist"  # archivist is orchestrator-only, never user-callable
    ]
    agents_block = (
        "\n".join(f"- {ag.code}: {ag.mission}" for ag in specialists)
        if specialists else "(none)"
    )

    return (
        f"# IDENTITY\n"
        f"You are {a.name} ({a.code}).\n"
        f"Role: {a.role}.\n"
        f"Mission: {a.mission}\n\n"
        f"# CONTEXT\n"
        f"## Human\n"
        f"{ctx.user_profile.render()}\n"
        f"Detected language — use for ALL human-facing output "
        f"(return_to_user answer, ask_human question and why): {ctx.detected_language}\n"
        f"Working language for everything else (internal reasoning, tool queries, "
        f"briefings to other agents): English only.\n\n"
        f"## Conversation\n"
        f"- conversation_id: {ctx.conversation_id}\n"
        f"- request_id: {ctx.request_id}\n"
        f"- parent_request_id: {ctx.parent_request_id or 'none'}\n"
        f"- recursion_depth: {ctx.depth}/{MAX_RECURSION_DEPTH}\n"
        f"- mode: {ctx.mode}\n"
        f"- turn_index: {ctx.turn_index}\n"
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
        + _render_prior_clarifications(ctx.turn_clarifications)
        + f"## Delegation targets\n"
        f"These are AGENTS, not tools. To use one, call delegate_to(agent_code='...'). "
        f"Never use an agent code as a direct tool function name — it will always fail.\n"
        f"{agents_block}\n\n"
        f"# DIRECTIVES\n"
        f"{render_directives(ctx.paradigms)}\n\n"
        f"{_render_output_contract(a.role)}"
    )


def tools_payload_for_agent(agent_role: str,
                            tool_grants: list[str],
                            registry: dict[str, ToolSpec],
                            depth: int = 0) -> list[dict[str, Any]]:
    """Build the tools payload (control tools filtered by role + native tools)."""
    payload: list[dict[str, Any]] = list(_CONTROL_TOOLS_BY_ROLE.get(agent_role, []))
    if depth >= 2 and agent_role != "finalizer":
        payload.append(_SIGNAL_CONVERGENCE)
    for tool_name in tool_grants:
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
