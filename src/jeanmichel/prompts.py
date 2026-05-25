"""Render the prompt skeleton from agent + paradigms + runtime context."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import plan_writer
from .config import MAX_RECURSION_DEPTH, UserProfile
from .models import Agent, Paradigm
from .tools import ToolSpec

# Maximum characters of plan.md injected into the router's system prompt.
# Truncates from the end (most recent steps matter most).
_PLAN_INJECTION_MAX_CHARS = 3000

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
                        "Artifact filenames (relative to the conversation folder) "
                        "for the receiving agent to read with conv_read_file. "
                        "ONLY use this for orchestrator-written artifacts "
                        "(e.g. the `artifact` value from a previous delegate_to result). "
                        "For data you fetched or computed (e.g. Wikipedia text), "
                        "write it to the workspace with workspace_create_file first, "
                        "then reference the workspace path in the briefing text "
                        "— NOT in support_files."
                    ),
                },
                "expected": {
                    "type": "object",
                    "description": (
                        "Structured contract for what the child agent must produce. "
                        "Legacy string values are accepted and auto-converted."
                    ),
                    "properties": {
                        "completion_verb": {
                            "type": "string",
                            "enum": [
                                "gather_done", "critic_done", "build_done",
                                "return_to_user", "report_findings",
                            ],
                            "description": (
                                "The control verb the child should use to conclude. "
                                "Use 'report_findings' as the default for specialist delegations."
                            ),
                        },
                        "workspace_artifacts": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Workspace paths the child MUST create "
                                "(relative to workspace root)."
                            ),
                        },
                        "summary_format": {
                            "type": "string",
                            "description": "Expected structure of the completion summary.",
                        },
                    },
                    "required": ["completion_verb"],
                },
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
# - router: full set including planner_done phase verb
# - specialist: report_findings + phase verbs (NO return_to_user)
# - finalizer: only return_to_user
_REPORT_FINDINGS: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "report_findings",
        "description": (
            "Specialist completion verb. Call this when you finish (or reach the limit of) "
            "the work the parent agent delegated to you. "
            "Provide a structured report so the parent can update the global plan and decide next steps. "
            "Do NOT use return_to_user — that verb is reserved for the router answering the human."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": (
                        "One concise paragraph (3–8 sentences) summarising what you did and found. "
                        "Be specific: name the key findings, sources consulted, files written."
                    ),
                },
                "files_produced": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Workspace-relative paths of files you created or modified. "
                        "Used by the parent to mark deliverables in the plan."
                    ),
                },
                "sub_questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "why": {
                                "type": "string",
                                "description": "Why this needs follow-up.",
                            },
                            "suggested_agent": {
                                "type": "string",
                                "description": "Optional: which specialist would best handle it.",
                            },
                        },
                        "required": ["question"],
                    },
                    "description": (
                        "Unresolved questions / ambiguities / promising leads. "
                        "The parent decides whether to spawn follow-up delegations. "
                        "Leave empty if work is fully closed."
                    ),
                },
                "blockers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Hard blockers preventing completion (missing tool, missing grant, "
                        "external service down). Empty list if none."
                    ),
                },
                "confidence": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": (
                        "Self-assessment of completeness against the briefing. "
                        "low = significant gaps, medium = main goal met but some uncertainty, "
                        "high = fully delivered."
                    ),
                },
            },
            "required": ["summary", "confidence"],
        },
    },
}

def _phase_verb(name: str, desc: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "One-paragraph summary of what was achieved in this phase.",
                    },
                    "artifacts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Workspace paths produced during this phase (relative to workspace root).",
                    },
                    "next_hint": {
                        "type": "string",
                        "description": "Optional hint for the orchestrator about what should logically happen next.",
                    },
                },
                "required": ["summary"],
            },
        },
    }


_PLANNER_DONE = _phase_verb(
    "planner_done",
    "Signal that the plan is up to date and you are ready to enter the GATHER phase.",
)
_GATHER_DONE = _phase_verb(
    "gather_done",
    "Signal that research collection is complete and findings are written to the workspace.",
)
_CRITIC_DONE = _phase_verb(
    "critic_done",
    "Signal that critical review is complete: biases and gaps have been identified.",
)
_BUILD_DONE = _phase_verb(
    "build_done",
    "Signal that the final document has been written to the workspace.",
)

_CONTROL_TOOLS_BY_ROLE: dict[str, list[dict[str, Any]]] = {
    "router":     [_ASK_HUMAN, _DELEGATE_TO, _RETURN_TO_USER],
    "specialist": [_ASK_HUMAN, _DELEGATE_TO, _REPORT_FINDINGS],
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
    conv_budget: str | None = None  # pre-computed budget block injected by the orchestrator


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
    if role == "specialist":
        return (
            "# OUTPUT CONTRACT\n"
            "- Reflect first in your thought channel; surface assumptions, traps, biases.\n"
            "- You CANNOT call return_to_user. You report back to the agent that delegated to you.\n"
            "- WORKSPACE IS YOUR MEMORY. Before calling report_findings, persist your findings "
            "to the workspace via workspace_create_file. The workspace file is what the parent "
            "agent will read — your report_findings summary is just a pointer/headline.\n"
            "- WRITE PROGRESSIVELY: after every 3-4 information-gathering tool calls "
            "(web_search, wikipedia_get_page, etc.), call workspace_create_file or "
            "workspace_str_replace to append what you've learned. Never accumulate everything "
            "in your context and write at the end — your context will be lost if you run out "
            "of budget.\n"
            "- If you need clarification: call ask_human(question, why). Only one ask_human per "
            "request; `why` is mandatory.\n"
            "- If a sub-task belongs to another specialist: call delegate_to(...).\n"
            "- When your work is done, call report_findings(summary, files_produced, "
            "sub_questions?, blockers?, confidence). Required fields:\n"
            "    - summary: 1-3 sentences. What you established. NOT 'I did X' — the actual "
            "headline finding.\n"
            "    - files_produced: list of workspace files you wrote. The parent will read these.\n"
            "    - confidence: low | medium | high.\n"
            "    - sub_questions: list of follow-ups the parent might want to pursue (optional).\n"
            "    - blockers: list of obstacles (optional).\n"
            "- Inter-agent briefings: English. Workspace files: English unless explicitly requested otherwise.\n"
        )
    # router
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
        "- A delegate_to result is a structured object {agent, artifact, answer, converged, files_produced}. "
        "`files_produced` lists workspace files the specialist wrote — read them with workspace_view "
        "or pass them in support_files to the next delegation. "
        "Do NOT copy another agent's `answer` field inline into a subsequent briefing — "
        "use the artifact filename or workspace paths instead. "
        "Your own fetched data (tool results, retrieved content, etc.) MUST be "
        "embedded directly in the briefing text when passing it to a downstream agent.\n"
        "- If task is yours and complete: call return_to_user(answer).\n"
        "- Inter-agent briefings: English. Human-facing output: see ## Human detected language.\n"
    )


def _render_prior_clarifications(clarifications: list[tuple[str, str]]) -> str:
    """Render the clarifications exchanged so far this turn, or empty string."""
    if not clarifications:
        return ""
    lines = "\n".join(f'- Q: "{q}" → A: "{a}"' for q, a in clarifications)
    return f"## Prior clarifications this turn\n{lines}\n\n"


def _render_plan_block(role: str, conversation_folder: str) -> str:
    """Render the current plan.md content for any agent.

    Injected verbatim (with tail-truncation) so specialists can see what
    peer steps have already explored and avoid redundant work.
    """
    try:
        p = plan_writer.plan_path(Path(conversation_folder))
        if not p.exists():
            return ""
        content = p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not content:
        return ""
    if len(content) > _PLAN_INJECTION_MAX_CHARS:
        # Keep the header + truncated body. Truncate from the start of the body,
        # not the end — most recent steps are at the bottom of the table.
        content = "(plan truncated — showing tail)\n…\n" + content[-_PLAN_INJECTION_MAX_CHARS:]
    if role == "router":
        intro = (
            "This is the live plan.md (maintained automatically by the orchestrator from your "
            "delegate_to calls and the resulting report_findings). Read it before deciding "
            "what to do next — do NOT re-delegate work that already has a ✅ done row."
        )
    else:
        intro = (
            "This is the live plan.md (maintained automatically by the orchestrator). "
            "It shows the parent's full delegation tree, including peer steps that have "
            "already searched / fetched / written things. Review the **Actions** logs "
            "of sibling steps to avoid redundant tool calls. If a sibling already produced "
            "files you need, reference them via conv_read_file or workspace_view instead "
            "of re-searching."
        )
    return (
        "## Plan so far\n"
        f"{intro}\n"
        f"```markdown\n{content}\n```\n\n"
    )


def render_plan_recap(conversation_folder: str, current_step_id: str | None = None) -> str:
    """Render a compact plan recap to inject into the user message between
    iterations of a single agent's request.

    Unlike ``_render_plan_block`` (used in the system prompt, rendered ONCE
    per request), this is meant to be re-injected at every iteration so the
    agent sees its own most recent tool calls and avoids re-searching.

    When ``current_step_id`` is provided, only that step's section is shown
    (the agent's own work-in-progress). Otherwise we show the tail of the
    full plan. Returns "" when there is nothing useful to show.
    """
    try:
        p = plan_writer.plan_path(Path(conversation_folder))
        if not p.exists():
            return ""
        content = p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not content:
        return ""

    if current_step_id:
        # Extract only the section for the current step.
        marker = f"## {current_step_id} "
        idx = content.find(marker)
        if idx >= 0:
            section = content[idx:]
            # Stop at the next "## " (sibling step) — but keep nested headers (### …).
            for i in range(1, len(section)):
                if section[i] == "\n" and section[i + 1:i + 4] == "## ":
                    section = section[:i]
                    break
            content = section

    if len(content) > _PLAN_INJECTION_MAX_CHARS:
        content = "(recap truncated — showing tail)\n…\n" + content[-_PLAN_INJECTION_MAX_CHARS:]

    return (
        "## Recap of your progress in the plan\n"
        "Below is what the orchestrator has recorded for you so far this "
        "request (your own tool calls + their summarised results). Review "
        "it before issuing the next call — do NOT repeat searches whose "
        "results are already listed here.\n"
        f"```markdown\n{content}\n```\n\n"
    )


def render_system_prompt(ctx: PromptContext) -> str:
    """Render the consolidated system block."""
    a = ctx.agent
    support_files_block = (
        "\n".join(f"- {p}" for p in ctx.support_files) if ctx.support_files else "(none)"
    )
    has_delegation = any(
        t.get("function", {}).get("name") == "delegate_to"
        for t in _CONTROL_TOOLS_BY_ROLE.get(a.role, [])
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
    if has_delegation:
        delegation_section = (
            f"## Delegation targets\n"
            f"These are AGENTS, not tools. To use one, call delegate_to(agent_code='...'). "
            f"Never use an agent code as a direct tool function name — it will always fail.\n"
            f"{agents_block}\n\n"
        )
    else:
        delegation_section = ""

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
        + _render_plan_block(a.role, ctx.conversation_folder)
        + (f"## Budget\n{ctx.conv_budget}\n\n" if ctx.conv_budget else "")
        + f"## Machine\n"
        f"- os: {platform.system()} {platform.release()}\n"
        f"- cwd: {os.getcwd()}\n"
        f"- utc_now: {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}\n\n"
        f"## Inbound briefing\n"
        f"from: {ctx.sender}\n"
        f"expected: {ctx.expected_outcome or '(unspecified)'}\n"
        f"support_files:\n{support_files_block}\n\n"
        f"{ctx.inbound_text}\n\n"
        + _render_prior_clarifications(ctx.turn_clarifications)
        + delegation_section
        + f"# DIRECTIVES\n"
        f"{render_directives(ctx.paradigms)}\n\n"
        f"{_render_output_contract(a.role)}"
    )


def tools_payload_for_agent(agent_role: str,
                            tool_grants: list[str],
                            registry: dict[str, ToolSpec],
                            depth: int = 0) -> list[dict[str, Any]]:
    """Build the tools payload (control tools filtered by role + native tools)."""
    payload: list[dict[str, Any]] = list(_CONTROL_TOOLS_BY_ROLE.get(agent_role, []))
    # No legacy signal_convergence offered — report_findings replaces it entirely.
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
