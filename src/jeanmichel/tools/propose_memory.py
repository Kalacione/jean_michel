"""Tool : propose_memory — capture a durable memory candidate for human review.

The agent calls this when it (or the user, via « garde en mémoire » / « note pour plus
tard ») spots something worth keeping long-term. NOTHING is written directly : the
candidate lands in the ``pending_consolidation`` queue and the human approves it (CLI
``/memo``, web review). This is the *write-proposal* channel ; ``manage_memory`` is read-only.

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
        "scope": {
            "type": "string",
            "enum": sorted(memory.VALID_SCOPES),
            "description": (
                "user (a durable fact/preference about the human), project (a decision/"
                "constraint of the current project), tool (a reusable lesson on a tool — set tool_code)."
            ),
        },
        "code": {
            "type": "string",
            "description": "Short kebab-case slug (e.g. 'prefers-terse-answers'). No spaces.",
        },
        "title": {"type": "string", "description": f"Short title (<= {memory.MAX_TITLE_CHARS} chars)."},
        "description": {
            "type": "string",
            "description": f"One-line hook surfaced in the prompt index (<= {memory.MAX_DESCRIPTION_CHARS} chars).",
        },
        "content": {
            "type": "string",
            "description": f"Full markdown body (<= {memory.MAX_CONTENT_CHARS} chars).",
        },
        "tool_code": {"type": "string", "description": "Target tool name. Required when scope='tool'."},
        "grounding_quote": {
            "type": "string",
            "description": "Optional verbatim quote from the user or a tool result supporting the fact (helps the human review).",
        },
        "importance": {
            "type": "integer",
            "description": "1 (minor) .. 5 (critical). Default 3. Ranks how prominently the entry is later surfaced.",
        },
    },
    "required": ["scope", "code", "title", "description", "content"],
}

_DESCRIPTION = (
    "Propose a durable fact worth remembering across future conversations — a user "
    "preference, a project decision/constraint, or a reusable tool lesson. Use it when the "
    "user says « garde ça en mémoire » / « note pour plus tard », or when you learn "
    "something durable. It is NOT written directly : it becomes a candidate the human "
    "reviews and approves. Use manage_memory to READ existing memory first (avoid duplicates)."
)


def make_spec(
    conv_id: str = "", user_id: int | None = None, project_id: int | None = None
) -> ToolSpec:
    """Return a ToolSpec bound to the conversation context (conv + memory owner + project)."""

    def handler(
        scope: str,
        code: str,
        title: str,
        description: str,
        content: str,
        tool_code: str | None = None,
        grounding_quote: str = "",
        importance: int = 3,
    ) -> str:
        if not conv_id:
            return tool_error("no_conversation", "propose_memory needs a conversation context.")
        try:
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
                action="propose",
                scope=cand["scope"],
                entry_code=cand["code"],
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
