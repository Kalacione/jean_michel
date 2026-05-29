"""Tool : manage_user_memory — long-term cross-conversation memory.

A single multi-action tool (cf. §10 doc 06). The actual CRUD + validation
lives in ``jeanmichel.service.memory`` (shared with the web API) ; this module
is the LLM-facing wrapper that turns data / errors into tool_ok/tool_error
strings.

Actions :

- ``save(type, code, title, description, content)`` : INSERT a new entry.
  Returns ``error: already_exists`` (with a hint to use ``update``) if the
  ``(type, code)`` pair is taken.
- ``recall(code)`` : SELECT one entry by code (across all types). Returns
  the full content. ``code`` is unique enough in practice ; if multiple
  types share a code, the most recently modified wins.
- ``list(type?)`` : SELECT all entries (or filtered by type), returning only
  the index fields (id, type, code, title, description, modified_at). The
  full ``content`` is never returned by ``list`` — use ``recall`` for that.
- ``update(code, title?, description?, content?)`` : UPDATE one entry.
  At least one of the optional fields must be provided.
- ``delete(code)`` : DELETE one entry.

Grants : only ``jean-michel`` receives this tool in v2 (cf. §11 bis doc 06).
"""

from __future__ import annotations

import logging
import sqlite3

from ..db import connect as db_connect
from ..service import memory
from ._base import ToolSpec
from ._errors import tool_error, tool_ok

_log = logging.getLogger(__name__)


# ---- Handler -------------------------------------------------------------


def _handler(
    action: str,
    type: str | None = None,
    code: str | None = None,
    title: str | None = None,
    description: str | None = None,
    content: str | None = None,
) -> str:
    """Dispatch on ``action`` and run the SQL within a single transaction."""
    if action not in memory.VALID_ACTIONS:
        return tool_error(
            "invalid_action",
            f"action must be one of {sorted(memory.VALID_ACTIONS)}.",
            received=action,
        )

    try:
        with db_connect() as conn:
            if action == "save":
                saved = memory.save(
                    conn,
                    type_=type or "",
                    code=code or "",
                    title=title or "",
                    description=description or "",
                    content=content or "",
                )
                return tool_ok(
                    f"Saved {saved['type']}/{saved['code']}: {saved['title']}",
                    action="save",
                    entry_type=saved["type"],
                    entry_code=saved["code"],
                )
            if action == "recall":
                rows = memory.recall(conn, code=code or "")
                if not rows:
                    return tool_error(
                        "not_found",
                        f"No entry with code='{code or ''}'.",
                        entry_code=code or "",
                    )
                if len(rows) == 1:
                    r = rows[0]
                    return tool_ok(
                        f"Recalled {r['type']}/{r['code']}: {r['title']}",
                        entry=r,
                    )
                primary = rows[0]
                others = [
                    {"type": r["type"], "modified_at": r["modified_at"]} for r in rows[1:]
                ]
                return tool_ok(
                    (
                        f"Multiple entries share code='{code}' (across types: "
                        f"{[r['type'] for r in rows]}). Returning most recent."
                    ),
                    entry=primary,
                    other_matches=others,
                )
            if action == "list":
                entries = memory.list_(conn, type_filter=type)
                summary = (
                    f"{len(entries)} entries" + (f" of type='{type}'" if type else "")
                )
                return tool_ok(summary, count=len(entries), entries=entries)
            if action == "update":
                target_id = memory.update(
                    conn,
                    code=code or "",
                    title=title,
                    description=description,
                    content=content,
                    type_=type,
                )
                return tool_ok(
                    f"Updated entry id={target_id} (code='{code}').",
                    id=target_id,
                    code=code,
                )
            if action == "delete":
                target_id = memory.delete(conn, code=code or "", type_=type)
                return tool_ok(
                    f"Deleted entry id={target_id} (code='{code}').",
                    id=target_id,
                    code=code,
                )
    except memory.MemoryOpError as exc:
        return tool_error(exc.code, exc.message, **exc.extra)
    except sqlite3.IntegrityError as exc:
        return tool_error("integrity_error", str(exc))
    except Exception as exc:  # noqa: BLE001
        _log.warning("manage_user_memory unexpected error: %s", exc)
        return tool_error("internal_error", str(exc))

    # Unreachable
    return tool_error("internal_error", "no branch matched")


# ---- ToolSpec ------------------------------------------------------------


SPEC = ToolSpec(
    name="manage_user_memory",
    description=(
        "Manage the long-term cross-conversation user memory. One tool, "
        "five actions : save (insert), recall (load full content), list "
        "(get index of all entries), update (modify), delete. "
        "Use save when the human reveals a durable fact about themselves. "
        "Use recall to load the full body of an entry whose code you saw "
        "in the ## Known facts section. Use update when an entry needs "
        "refinement. Use delete when it becomes obsolete. Entries are "
        "scoped by 'type' (user|feedback|project|reference) and unique "
        "per (type, code) pair."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": sorted(memory.VALID_ACTIONS),
                "description": "Which operation to perform.",
            },
            "type": {
                "type": "string",
                "enum": sorted(memory.VALID_TYPES),
                "description": (
                    "Entry category. Required for save. Optional for list "
                    "(filter), update/delete (disambiguation)."
                ),
            },
            "code": {
                "type": "string",
                "description": (
                    "Short kebab-case slug (e.g. 'unity-montreal'). Required "
                    "for save/recall/update/delete."
                ),
            },
            "title": {
                "type": "string",
                "description": (
                    f"Short title (<= {memory.MAX_TITLE_CHARS} chars). Required for save."
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    f"One-line hook injected into the prompt index "
                    f"(<= {memory.MAX_DESCRIPTION_CHARS} chars). Required for save."
                ),
            },
            "content": {
                "type": "string",
                "description": (
                    f"Full markdown body (<= {memory.MAX_CONTENT_CHARS} chars). "
                    "Required for save. Loaded on demand via recall."
                ),
            },
        },
        "required": ["action"],
    },
    handler=_handler,
)
