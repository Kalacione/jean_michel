"""v2 prompt rendering helpers.

Three exposed surfaces :

- ``DISPATCH_SYSTEM_PROMPT`` — static prompt for the Tier 0 dispatcher
  (cf. DevNotes/REVOLUCION/06_proposition_v2.md §3). JSON-forced output.
- ``render_memory_block(conn, *, user_id, project_id, tool_codes)`` — Markdown
  block listing the (code : description) index of the long-term ``memory``
  entries that DETERMINISTICALLY apply here : world (always) + the user's facts
  + the conversation's project + the notes of the tools this agent is granted.
  Injected into every agent's ``## Human`` section by ``render_system_prompt_v2``.
- ``render_system_prompt_v2(*, …)`` — produces the system message used by
  the v2 main loop. Composition : identity + context (human + conversation)
  + paradigms (grouped by category) + role-specific output contract.

The v1 prompt machinery (PromptContext / render_system_prompt / plan_recap
/ tool payload helpers) has been removed in Phase 8.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

from .config import (
    MEMORY_PROJECT_CAP,
    MEMORY_TOOL_CAP_PER_TOOL,
    MEMORY_USER_CAP,
    MEMORY_WORLD_CAP,
    USER_MEMORY_WARN_AT,
)

if TYPE_CHECKING:
    from .models import Paradigm

logger = logging.getLogger(__name__)


# =============================================================================
# Tier 0 dispatcher prompt
# =============================================================================

# Static, never composed dynamically. Used by ``jeanmichel.dispatcher.classify``
# with ``format="json"`` enforced on the Ollama side.
DISPATCH_SYSTEM_PROMPT = """You classify a user request. Reply with strict JSON of shape:
{
  "intent": "alexa" | "deep",
  "tool":   "clock" | "weather" | "wikipedia_search" | null,
  "args":   { ... }
}

IMPORTANT: `intent` is ALWAYS one of the two literal strings "alexa" or
"deep" — never a tool name. The tool name goes in the `tool` field, never
in `intent`.

intent="alexa" when ONE tool from the list can satisfy the request directly:
  - clock              : current time / date
        args: OMIT entirely for the user's own location (the default in
              Context is used). Set {"location": "<city>"} ONLY if the user
              names a different place.
  - weather            : current conditions or a forecast
        args:
          - "location": OMIT it unless the user names a place different from
            the default in Context. Do not guess a city.
          - "when": if the user mentions a day and/or time of day, COPY their
            words verbatim, in their own language ("ce soir", "demain matin",
            "jeudi soir", "this weekend", "next monday", "dans 3 jours"). Do NOT
            translate. NEVER compute a date. Omit for current weather.
  - wikipedia_search   : single factual lookup (definition, dates, identity)
        args: {"query": "<entity name>"}

intent="deep" for everything else (comparison, multi-step research,
codebase analysis, document production, debugging, advice).

If you cannot decide, answer "deep". Never invent a tool name not in
the list above."""


# =============================================================================
# Long-term memory block renderer (deterministic, scope-driven inclusion)
# =============================================================================


def _memory_section(
    conn: sqlite3.Connection, header: str, where: str, params: tuple, cap: int
) -> list[str]:
    """Render one ``## header`` index section (code : description), capped. [] if empty."""
    rows = conn.execute(
        f"SELECT code, description FROM memory WHERE {where} "
        "ORDER BY modified_at DESC LIMIT ?",
        (*params, cap),
    ).fetchall()
    if not rows:
        return []
    out = [f"## {header}"]
    out.extend(f"- {r['code']} : {r['description']}" for r in rows)
    return out


def render_memory_block(
    conn: sqlite3.Connection,
    *,
    user_id: int | None = None,
    project_id: int | None = None,
    tool_codes: frozenset[str] | set[str] | None = None,
    warn_at: int = USER_MEMORY_WARN_AT,
) -> tuple[str, int]:
    """Render the long-term memory index block + return the user-scope entry count.

    Inclusion is 100 % deterministic (pure SQL by scope) — no LLM in this path :
      - world   : always
      - user    : the given ``user_id`` (``None`` → reserved ``cli`` user)
      - project : the given ``project_id`` (skipped when None)
      - tool    : entries whose ``tool_code`` is in ``tool_codes`` (the agent's grants)

    Each section is an index (``code : description``) — never the full content,
    which is loaded on demand via ``manage_memory(action='recall'|'search')``.
    Returns ``("", 0)`` when nothing applies, or when the table is missing
    (migrations not applied). The int is the user-scope count (for the warning).
    """
    from .db import cli_user_id

    try:
        uid = user_id if user_id is not None else cli_user_id(conn)
        sections: list[str] = []
        sections += _memory_section(
            conn, "World knowledge (long-term memory)", "scope='world'", (), MEMORY_WORLD_CAP
        )
        sections += _memory_section(
            conn,
            "Known facts about the user (long-term memory)",
            "scope='user' AND user_id=?",
            (uid,),
            MEMORY_USER_CAP,
        )
        if project_id is not None:
            sections += _memory_section(
                conn,
                "Project context (long-term memory)",
                "scope='project' AND project_id=?",
                (project_id,),
                MEMORY_PROJECT_CAP,
            )
        if tool_codes:
            placeholders = ",".join("?" * len(tool_codes))
            cap = MEMORY_TOOL_CAP_PER_TOOL * len(tool_codes)
            sections += _memory_section(
                conn,
                "Tool notes (how to use your tools)",
                f"scope='tool' AND tool_code IN ({placeholders})",
                tuple(sorted(tool_codes)),
                cap,
            )
        user_count_row = conn.execute(
            "SELECT COUNT(*) AS c FROM memory WHERE scope='user' AND user_id=?", (uid,)
        ).fetchone()
        user_count = int(user_count_row["c"]) if user_count_row is not None else 0
    except (sqlite3.OperationalError, KeyError):
        # Table or `cli` user missing (migrations not applied yet).
        return "", 0

    if not sections:
        return "", 0

    sections.append("")
    sections.append(
        "Use `manage_memory(action='recall', scope='<scope>', code='<code>')` to "
        "load an entry's full body, `action='search'` to find related memory, "
        "`action='save'` / `note_for_<scope>` to add, `action='update'` to refine."
    )
    if user_count >= warn_at:
        sections.append(
            f"⚠ User memory near capacity ({user_count} entries). "
            "Purge obsolete entries via `action='delete'`."
        )
    return "\n".join(sections) + "\n", user_count


# =============================================================================
# Paradigm rendering
# =============================================================================


def render_directives(paradigms: list[Paradigm]) -> str:
    """Group paradigms by category and render their contents as Markdown.

    Each category becomes a ``## <Category title>`` header, followed by the
    raw ``content`` (already in Markdown bullet form) of each paradigm in
    that category.
    """
    out: list[str] = []
    current_category: tuple[str, str] | None = None
    for p in paradigms:
        cat_key = (p.section_code, p.category_code)
        if cat_key != current_category:
            out.append(f"\n## {p.category_title}")
            current_category = cat_key
        out.append(p.content.strip())
    return "\n".join(out).strip()


# =============================================================================
# Role-specific output contract (v2)
# =============================================================================


def _render_output_contract_v2(role: str) -> str:
    """v2 output contract — role-specific termination rules.

    - ``specialist`` : terminate via ``report_back``. No ``ask_human``.
    - ``router``     : terminate by emitting an assistant turn WITHOUT
                       tool_calls. Has ``ask_human``.
    - ``finalizer``  : terminate by emitting an assistant turn WITHOUT
                       tool_calls. No delegation, no ask_human.
    """
    if role == "specialist":
        return (
            "# OUTPUT CONTRACT\n"
            "- Reflect first in your thought channel ; surface assumptions and traps.\n"
            "- You may use `delegate_to(agent_code, briefing, expected?, support_files?)` "
            "to descend the task tree if a sub-task exceeds your scope.\n"
            "- You do NOT have `ask_human`. If a clarification is missing, "
            "conclude with `report_back(confidence='low', low_confidence_reason='...')`. "
            "The main agent (jean-michel) decides whether to ask the human.\n"
            "- Conclude with `report_back(summary, files_produced, confidence, low_confidence_reason?)`. "
            "This is the ONLY way to exit. `low_confidence_reason` is mandatory when confidence='low'.\n"
            "- Inter-agent briefings: English. Workspace files: English unless explicitly requested otherwise."
        )
    if role == "router":
        return (
            "# OUTPUT CONTRACT\n"
            "- Reflect first in your thought channel.\n"
            "- Delegate via `delegate_to(agent_code, briefing, expected?, support_files?)`. "
            "Multiple parallel delegate_to calls in the same turn are processed sequentially.\n"
            "- Ask the human via `ask_human(question, why)` only when a clarification blocks progress.\n"
            "- Conclude by emitting an assistant turn WITHOUT any tool_calls. "
            "The `content` field of that turn IS the final answer to the user.\n"
            "- Inter-agent briefings: English. Human-facing output: in the detected language."
        )
    # finalizer
    return (
        "# OUTPUT CONTRACT\n"
        "- Reflect first ; produce the deliverable.\n"
        "- Conclude by emitting an assistant turn WITHOUT any tool_calls. "
        "The `content` field of that turn IS the final answer to the user.\n"
        "- You do NOT delegate, you do NOT ask the human. Work with the inputs provided."
    )


# =============================================================================
# v2 system prompt renderer
# =============================================================================


def render_delegation_targets_block(
    targets: list[tuple[str, str, str]],
) -> str:
    """Render the ``## Delegation targets`` block for the system prompt.

    ``targets`` is a list of ``(code, role, mission)`` triples. The mission is
    one-line trimmed at 160 chars so the block stays compact. Returns an empty
    string when the agent has no delegation targets — caller decides whether
    to inject it. The block exists so the LLM literally sees the names it can
    pass to ``delegate_to`` ; otherwise it hallucinates agent codes or skips
    delegation entirely.
    """
    if not targets:
        return ""
    lines: list[str] = [
        "## Delegation targets — the only agent codes you may pass to `delegate_to`",
    ]
    for code, role, mission in targets:
        flat = " ".join((mission or "").split())
        if len(flat) > 160:
            flat = flat[:159].rstrip() + "…"
        lines.append(f"- `{code}` ({role}) — {flat}")
    return "\n".join(lines) + "\n"


def render_system_prompt_v2(
    *,
    agent_code: str,
    agent_name: str,
    agent_role: str,
    agent_mission: str,
    paradigms: list[Paradigm],
    user_profile_text: str = "",
    memory_block: str = "",
    user_language: str = "und",
    mode: str = "analyse",
    delegation_targets_meta: list[tuple[str, str, str]] | None = None,
) -> str:
    """v2 system prompt renderer.

    Composition order :

      # IDENTITY
      # CONTEXT
        ## Human  (profile + user_memory index)
        ## Conversation
        ## Delegation targets  (only when the agent has any)
      # DIRECTIVES
        (paradigms grouped by category)
      # OUTPUT CONTRACT
        (role-specific — see ``_render_output_contract_v2``)
    """
    directives = render_directives(paradigms) if paradigms else "(no paradigms)"
    output_contract = _render_output_contract_v2(agent_role)

    human_block_parts: list[str] = []
    if user_profile_text.strip():
        human_block_parts.append(user_profile_text.strip())
    if memory_block.strip():
        human_block_parts.append(memory_block.rstrip())
    human_block = (
        "\n\n".join(human_block_parts)
        if human_block_parts
        else "No user profile provided."
    )

    targets_block = render_delegation_targets_block(delegation_targets_meta or [])
    targets_section = f"\n{targets_block}" if targets_block else ""

    return (
        f"# IDENTITY\n"
        f"You are {agent_name} ({agent_code}).\n"
        f"Role: {agent_role}.\n"
        f"Mission: {agent_mission}\n\n"
        f"# CONTEXT\n"
        f"## Human\n"
        f"{human_block}\n\n"
        f"Detected language — use for human-facing output: {user_language}\n"
        f"Working language for everything else (internal reasoning, tool queries, "
        f"briefings to other agents): English only.\n\n"
        f"## Conversation\n"
        f"- mode: {mode}\n"
        f"{targets_section}\n"
        f"# DIRECTIVES\n"
        f"{directives}\n\n"
        f"{output_contract}\n"
    )
