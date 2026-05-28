"""v2 prompt rendering helpers.

Three exposed surfaces :

- ``DISPATCH_SYSTEM_PROMPT`` — static prompt for the Tier 0 dispatcher
  (cf. DevNotes/REVOLUCION/06_proposition_v2.md §3). JSON-forced output.
- ``render_user_memory_index(conn, limit, warn_at)`` — Markdown block listing
  the (type / code / description) of the most-recently-modified
  ``user_memory`` entries. Injected into every agent's ``## Human`` section
  by ``render_system_prompt_v2``.
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

from .config import USER_MEMORY_INDEX_LIMIT, USER_MEMORY_WARN_AT

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

intent="alexa" when ONE tool from the list can satisfy the request directly:
  - clock              : current time / date
        args: {"location": "<city>"} when the user names a place
              (e.g. "Paris", "Tokyo, Japan"). Omit args for the user's
              own local time — the orchestrator fills it from the profile.
  - weather            : current weather or forecast at a location
        args: {"location": "<city>"} when the user names a place
              (e.g. "Paris", "Tokyo, Japan"). Omit args for weather
              at the user's own location — the orchestrator fills it
              from the profile.
  - wikipedia_search   : single factual lookup (definition, dates, identity)
        args: {"query": "<entity name>"}

intent="deep" for everything else (comparison, multi-step research,
codebase analysis, document production, debugging, advice).

If you cannot decide, answer "deep". Never invent a tool name not in
the list above."""


# =============================================================================
# user_memory index renderer
# =============================================================================


def render_user_memory_index(
    conn: sqlite3.Connection,
    limit: int = USER_MEMORY_INDEX_LIMIT,
    warn_at: int = USER_MEMORY_WARN_AT,
) -> tuple[str, int]:
    """Render the user_memory index block + return the total entry count.

    The block lists ``[type] code : description`` of the most-recently-
    modified entries (capped at ``limit``). Returns ``("", 0)`` when the
    table is empty or missing — the caller decides whether to inject.
    """
    try:
        rows = conn.execute(
            "SELECT type, code, description, modified_at "
            "FROM user_memory "
            "ORDER BY modified_at DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        # Table missing (migration 101 not applied yet).
        return "", 0

    if not rows:
        return "", 0

    count_row = conn.execute("SELECT COUNT(*) AS c FROM user_memory").fetchone()
    total_count = int(count_row["c"]) if count_row is not None else len(rows)

    lines: list[str] = ["## Known facts about the user (long-term memory)"]
    for r in rows:
        lines.append(f"- [{r['type']}] {r['code']} : {r['description']}")
    lines.append("")
    lines.append(
        "Use `manage_user_memory(action='recall', code='<code>')` to load the "
        "full body of an entry, `action='save'` to add a new fact, "
        "`action='update'` to refine one. "
    )
    if total_count >= warn_at:
        lines.append(
            f"⚠ Memory near capacity ({total_count} / {limit} entries shown). "
            "Purge obsolete entries via `action='delete'`."
        )
    return "\n".join(lines) + "\n", total_count


# =============================================================================
# Paradigm rendering
# =============================================================================


def render_directives(paradigms: list["Paradigm"]) -> str:
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
    paradigms: list["Paradigm"],
    user_profile_text: str = "",
    user_memory_block: str = "",
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
    if user_memory_block.strip():
        human_block_parts.append(user_memory_block.rstrip())
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
