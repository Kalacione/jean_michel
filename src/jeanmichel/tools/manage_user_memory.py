"""Tool : manage_user_memory — long-term cross-conversation memory.

A single multi-action tool (cf. §10 doc 06). Operates on the `user_memory`
table created by `migrate_101_user_memory.sql`. Stateless : no conv_folder,
no per-agent context — the table is global to the user.

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
from datetime import UTC, datetime
from typing import Any

from ..db import connect as db_connect
from ._base import ToolSpec
from ._errors import tool_error, tool_ok

_log = logging.getLogger(__name__)


# ---- Constants -----------------------------------------------------------

_VALID_TYPES: frozenset[str] = frozenset({"user", "feedback", "project", "reference"})
_VALID_ACTIONS: frozenset[str] = frozenset({"save", "recall", "list", "update", "delete"})

# Hard caps mentioned in §10 doc 06 — defensive limits the tool enforces.
_MAX_TITLE_CHARS = 60
_MAX_DESCRIPTION_CHARS = 150
_MAX_CONTENT_CHARS = 1_000


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- Action implementations ----------------------------------------------


def _action_save(
    conn: sqlite3.Connection,
    *,
    type_: str,
    code: str,
    title: str,
    description: str,
    content: str,
) -> str:
    err = _validate_save_args(type_, code, title, description, content)
    if err is not None:
        return err

    existing = conn.execute(
        "SELECT id, type FROM user_memory WHERE type=? AND code=?",
        (type_, code),
    ).fetchone()
    if existing is not None:
        return tool_error(
            "already_exists",
            (
                f"An entry with type='{type_}' and code='{code}' already exists. "
                "Use action='update' to modify it."
            ),
            entry_type=type_,
            entry_code=code,
        )

    now = _now()
    conn.execute(
        "INSERT INTO user_memory "
        "(type, code, title, description, content, created_at, modified_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (type_, code, title, description, content, now, now),
    )
    return tool_ok(
        f"Saved {type_}/{code}: {title}",
        action="save",
        entry_type=type_,
        entry_code=code,
    )


def _action_recall(conn: sqlite3.Connection, *, code: str) -> str:
    if not code or not code.strip():
        return tool_error("invalid_args", "code is required for recall.")

    rows = conn.execute(
        "SELECT id, type, code, title, description, content, created_at, modified_at "
        "FROM user_memory WHERE code=? "
        "ORDER BY modified_at DESC",
        (code,),
    ).fetchall()
    if not rows:
        return tool_error("not_found", f"No entry with code='{code}'.", entry_code=code)

    if len(rows) == 1:
        r = rows[0]
        return tool_ok(
            f"Recalled {r['type']}/{r['code']}: {r['title']}",
            entry={
                "id": r["id"],
                "type": r["type"],
                "code": r["code"],
                "title": r["title"],
                "description": r["description"],
                "content": r["content"],
                "created_at": r["created_at"],
                "modified_at": r["modified_at"],
            },
        )

    # Multiple types share this code — return the most recent and report all.
    primary = rows[0]
    others = [
        {"type": r["type"], "modified_at": r["modified_at"]}
        for r in rows[1:]
    ]
    return tool_ok(
        (
            f"Multiple entries share code='{code}' (across types: "
            f"{[r['type'] for r in rows]}). Returning most recent."
        ),
        entry={
            "id": primary["id"],
            "type": primary["type"],
            "code": primary["code"],
            "title": primary["title"],
            "description": primary["description"],
            "content": primary["content"],
            "created_at": primary["created_at"],
            "modified_at": primary["modified_at"],
        },
        other_matches=others,
    )


def _action_list(conn: sqlite3.Connection, *, type_filter: str | None = None) -> str:
    if type_filter is not None and type_filter not in _VALID_TYPES:
        return tool_error(
            "invalid_type",
            f"type must be one of {sorted(_VALID_TYPES)}",
            received=type_filter,
        )

    if type_filter:
        rows = conn.execute(
            "SELECT id, type, code, title, description, modified_at "
            "FROM user_memory WHERE type=? "
            "ORDER BY modified_at DESC",
            (type_filter,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, type, code, title, description, modified_at "
            "FROM user_memory "
            "ORDER BY modified_at DESC",
        ).fetchall()

    entries = [
        {
            "id": r["id"],
            "type": r["type"],
            "code": r["code"],
            "title": r["title"],
            "description": r["description"],
            "modified_at": r["modified_at"],
        }
        for r in rows
    ]
    summary = (
        f"{len(entries)} entries"
        + (f" of type='{type_filter}'" if type_filter else "")
    )
    return tool_ok(summary, count=len(entries), entries=entries)


def _action_update(
    conn: sqlite3.Connection,
    *,
    code: str,
    title: str | None,
    description: str | None,
    content: str | None,
    type_: str | None = None,
) -> str:
    if not code or not code.strip():
        return tool_error("invalid_args", "code is required for update.")
    if title is None and description is None and content is None:
        return tool_error(
            "invalid_args",
            "update requires at least one of: title, description, content.",
        )

    # Validate sizes if provided.
    if title is not None and len(title) > _MAX_TITLE_CHARS:
        return tool_error(
            "title_too_long",
            f"title must be <= {_MAX_TITLE_CHARS} chars.",
            received=len(title),
        )
    if description is not None and len(description) > _MAX_DESCRIPTION_CHARS:
        return tool_error(
            "description_too_long",
            f"description must be <= {_MAX_DESCRIPTION_CHARS} chars.",
            received=len(description),
        )
    if content is not None and len(content) > _MAX_CONTENT_CHARS:
        return tool_error(
            "content_too_long",
            f"content must be <= {_MAX_CONTENT_CHARS} chars.",
            received=len(content),
        )

    # Resolve target row(s) by code (+ optional type disambiguation).
    if type_ is not None:
        existing = conn.execute(
            "SELECT id, type, code FROM user_memory WHERE type=? AND code=?",
            (type_, code),
        ).fetchall()
    else:
        existing = conn.execute(
            "SELECT id, type, code FROM user_memory WHERE code=?",
            (code,),
        ).fetchall()
    if not existing:
        return tool_error("not_found", f"No entry with code='{code}'.", entry_code=code)
    if len(existing) > 1 and type_ is None:
        return tool_error(
            "ambiguous",
            (
                f"Multiple entries share code='{code}'. "
                "Specify type to disambiguate."
            ),
            matches=[{"type": r["type"], "id": r["id"]} for r in existing],
        )

    target_id = existing[0]["id"]
    sets: list[str] = []
    params: list[Any] = []
    if title is not None:
        sets.append("title=?")
        params.append(title)
    if description is not None:
        sets.append("description=?")
        params.append(description)
    if content is not None:
        sets.append("content=?")
        params.append(content)
    sets.append("modified_at=?")
    params.append(_now())
    params.append(target_id)

    conn.execute(
        f"UPDATE user_memory SET {', '.join(sets)} WHERE id=?",
        params,
    )
    return tool_ok(
        f"Updated entry id={target_id} (code='{code}').",
        id=target_id,
        code=code,
    )


def _action_delete(
    conn: sqlite3.Connection,
    *,
    code: str,
    type_: str | None = None,
) -> str:
    if not code or not code.strip():
        return tool_error("invalid_args", "code is required for delete.")

    if type_ is not None:
        existing = conn.execute(
            "SELECT id FROM user_memory WHERE type=? AND code=?",
            (type_, code),
        ).fetchall()
    else:
        existing = conn.execute(
            "SELECT id, type FROM user_memory WHERE code=?",
            (code,),
        ).fetchall()
    if not existing:
        return tool_error("not_found", f"No entry with code='{code}'.", entry_code=code)
    if len(existing) > 1 and type_ is None:
        return tool_error(
            "ambiguous",
            f"Multiple entries share code='{code}'. Specify type to disambiguate.",
            matches=[{"type": r["type"], "id": r["id"]} for r in existing],
        )

    target_id = existing[0]["id"]
    conn.execute("DELETE FROM user_memory WHERE id=?", (target_id,))
    return tool_ok(
        f"Deleted entry id={target_id} (code='{code}').",
        id=target_id,
        code=code,
    )


# ---- Validation helpers --------------------------------------------------


def _validate_save_args(
    type_: str, code: str, title: str, description: str, content: str
) -> str | None:
    if type_ not in _VALID_TYPES:
        return tool_error(
            "invalid_type",
            f"type must be one of {sorted(_VALID_TYPES)}.",
            received=type_,
        )
    if not code or not code.strip():
        return tool_error("invalid_args", "code is required.")
    if " " in code:
        return tool_error(
            "invalid_code",
            "code must not contain spaces. Use kebab-case (e.g. 'unity-montreal').",
        )
    if not title or not title.strip():
        return tool_error("invalid_args", "title is required.")
    if not description or not description.strip():
        return tool_error("invalid_args", "description is required.")
    if not content or not content.strip():
        return tool_error("invalid_args", "content is required.")
    if len(title) > _MAX_TITLE_CHARS:
        return tool_error(
            "title_too_long",
            f"title must be <= {_MAX_TITLE_CHARS} chars.",
            received=len(title),
        )
    if len(description) > _MAX_DESCRIPTION_CHARS:
        return tool_error(
            "description_too_long",
            f"description must be <= {_MAX_DESCRIPTION_CHARS} chars.",
            received=len(description),
        )
    if len(content) > _MAX_CONTENT_CHARS:
        return tool_error(
            "content_too_long",
            f"content must be <= {_MAX_CONTENT_CHARS} chars.",
            received=len(content),
        )
    return None


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
    if action not in _VALID_ACTIONS:
        return tool_error(
            "invalid_action",
            f"action must be one of {sorted(_VALID_ACTIONS)}.",
            received=action,
        )

    try:
        with db_connect() as conn:
            if action == "save":
                return _action_save(
                    conn,
                    type_=type or "",
                    code=code or "",
                    title=title or "",
                    description=description or "",
                    content=content or "",
                )
            if action == "recall":
                return _action_recall(conn, code=code or "")
            if action == "list":
                return _action_list(conn, type_filter=type)
            if action == "update":
                return _action_update(
                    conn,
                    code=code or "",
                    title=title,
                    description=description,
                    content=content,
                    type_=type,
                )
            if action == "delete":
                return _action_delete(conn, code=code or "", type_=type)
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
                "enum": sorted(_VALID_ACTIONS),
                "description": "Which operation to perform.",
            },
            "type": {
                "type": "string",
                "enum": sorted(_VALID_TYPES),
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
                "description": f"Short title (<= {_MAX_TITLE_CHARS} chars). Required for save.",
            },
            "description": {
                "type": "string",
                "description": (
                    f"One-line hook injected into the prompt index "
                    f"(<= {_MAX_DESCRIPTION_CHARS} chars). Required for save."
                ),
            },
            "content": {
                "type": "string",
                "description": (
                    f"Full markdown body (<= {_MAX_CONTENT_CHARS} chars). "
                    "Required for save. Loaded on demand via recall."
                ),
            },
        },
        "required": ["action"],
    },
    handler=_handler,
)
