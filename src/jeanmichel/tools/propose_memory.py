"""Tool : propose_memory — capture a durable FACT or a behavioural RULE for human review.

The agent calls this when it (or the user, via « garde en mémoire » / « note pour plus
tard ») spots something worth keeping long-term. NOTHING is written directly :
  - kind="fact" → a candidate in pending_consolidation (→ semantic memory on approval) ;
  - kind="rule" → a paradigm-promotion candidate (→ a paradigm on approval, DARK until the
    human activates/binds it).
The human approves in the CLI ``/memo`` + web review. ``manage_memory`` is read-only.

Bound to the conversation context (conv_id + memory owner + project) at registry-build time.
"""

from __future__ import annotations

import logging

from ..db import cli_user_id
from ..db import connect as db_connect
from ..service import consolidation, memory
from ._base import ToolSpec
from ._errors import tool_error, tool_ok

_log = logging.getLogger(__name__)

_PARAMETERS = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["fact", "rule"],
            "description": (
                "fact = a durable fact (→ memory) ; rule = a generalizable behavioural lesson "
                "(« when X, do Y / never Z ») → a paradigm, human-approved. Default fact."
            ),
        },
        "title": {"type": "string", "description": f"Short title (<= {memory.MAX_TITLE_CHARS} chars). Required."},
        "content": {
            "type": "string",
            "description": "Full body (markdown). Required. For a rule: English, model-agnostic bullets.",
        },
        "importance": {"type": "integer", "description": "1 (minor) .. 5 (critical). Default 3."},
        "grounding_quote": {
            "type": "string",
            "description": "Optional verbatim quote (from the user or a tool result) supporting it.",
        },
        # fact-only
        "scope": {"type": "string", "enum": sorted(memory.VALID_SCOPES), "description": "[fact] user / project / tool."},
        "code": {"type": "string", "description": "[fact] short kebab-case slug."},
        "description": {
            "type": "string",
            "description": f"[fact] one-line hook injected in the index (<= {memory.MAX_DESCRIPTION_CHARS} chars).",
        },
        "tool_code": {"type": "string", "description": "[fact] target tool name, when scope='tool'."},
        # rule-only
        "section_code": {
            "type": "string",
            "description": "[rule] paradigm SECTION code (e.g. communication, reasoning, process, code, safety, critical_thinking).",
        },
        "category_code": {"type": "string", "description": "[rule] paradigm CATEGORY code within the section."},
    },
    "required": ["title", "content"],
}

_DESCRIPTION = (
    "Propose something worth keeping long-term — kind='fact' (a user preference, a project "
    "decision/constraint, or a tool lesson → memory) or kind='rule' (a generalizable "
    "behavioural lesson → a paradigm). Use it on « garde ça en mémoire » or when you learn "
    "something durable. NOT written directly : it becomes a candidate the human reviews and "
    "approves. Read existing memory with manage_memory first to avoid duplicates."
)


def make_spec(
    conv_id: str = "", user_id: int | None = None, project_id: int | None = None
) -> ToolSpec:
    """Return a ToolSpec bound to the conversation context (conv + memory owner + project)."""

    def handler(
        title: str,
        content: str,
        kind: str = "fact",
        importance: int = 3,
        grounding_quote: str = "",
        scope: str | None = None,
        code: str | None = None,
        description: str | None = None,
        tool_code: str | None = None,
        section_code: str | None = None,
        category_code: str | None = None,
    ) -> str:
        if not conv_id:
            return tool_error("no_conversation", "propose_memory needs a conversation context.")
        try:
            if kind == "rule":
                if not (section_code and category_code):
                    return tool_error("invalid_args", "kind='rule' needs section_code + category_code.")
                cand = consolidation.add_rule_candidate(
                    conv_id, section_code=section_code, category_code=category_code,
                    title=title, content=content, grounding_quote=grounding_quote, importance=importance,
                )
                return tool_ok(
                    f"Proposed rule « {cand['title']} » for review (suggested: {cand['suggested_action']}). "
                    "The human approves it in /memo.",
                    action="propose", kind="rule", suggested_action=cand["suggested_action"],
                )
            # kind == "fact"
            if not (scope and code and description):
                return tool_error("invalid_args", "kind='fact' needs scope + code + description.")
            uid = user_id
            if uid is None:
                with db_connect() as conn:
                    uid = cli_user_id(conn)
            cand = consolidation.add_candidate(
                conv_id, scope=scope, code=code, title=title, description=description,
                content=content, user_id=uid, project_id=project_id, tool_code=tool_code,
                grounding_quote=grounding_quote, importance=importance,
            )
            return tool_ok(
                f"Proposed {cand['scope']}/{cand['code']} for review "
                f"(suggested: {cand['suggested_action']}). The human approves it in /memo.",
                action="propose", kind="fact", scope=cand["scope"], entry_code=cand["code"],
                suggested_action=cand["suggested_action"],
            )
        except memory.MemoryOpError as exc:
            return tool_error(exc.code, exc.message, **exc.extra)
        except Exception as exc:  # noqa: BLE001
            _log.warning("propose_memory unexpected error: %s", exc)
            return tool_error("internal_error", str(exc))

    return ToolSpec(
        name="propose_memory",
        description=_DESCRIPTION,
        parameters=_PARAMETERS,
        handler=handler,
    )


# Module-level default (no conversation/owner) — used by the registry fallback + tests.
SPEC = make_spec()
