"""user_memory CRUD — pure data layer, single SQL source.

Shared by the ``manage_user_memory`` tool (LLM-facing, formats results as
tool_ok/tool_error strings) and the web API (S2/S5, returns JSON). Validation
and SQL live here once ; transports only translate the result.

Errors are signalled by raising ``MemoryOpError(code, message, **extra)``.
Success returns plain data (dicts / ids).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

VALID_TYPES: frozenset[str] = frozenset({"user", "feedback", "project", "reference"})
VALID_ACTIONS: frozenset[str] = frozenset({"save", "recall", "list", "update", "delete"})

# Hard caps (cf. §10 doc 06).
MAX_TITLE_CHARS = 60
MAX_DESCRIPTION_CHARS = 150
MAX_CONTENT_CHARS = 1_000


class MemoryOpError(Exception):
    """A user_memory operation failed. Carries a stable error code + extras."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_save(
    type_: str, code: str, title: str, description: str, content: str
) -> None:
    if type_ not in VALID_TYPES:
        raise MemoryOpError(
            "invalid_type", f"type must be one of {sorted(VALID_TYPES)}.", received=type_
        )
    if not code or not code.strip():
        raise MemoryOpError("invalid_args", "code is required.")
    if " " in code:
        raise MemoryOpError(
            "invalid_code",
            "code must not contain spaces. Use kebab-case (e.g. 'unity-montreal').",
        )
    if not title or not title.strip():
        raise MemoryOpError("invalid_args", "title is required.")
    if not description or not description.strip():
        raise MemoryOpError("invalid_args", "description is required.")
    if not content or not content.strip():
        raise MemoryOpError("invalid_args", "content is required.")
    if len(title) > MAX_TITLE_CHARS:
        raise MemoryOpError(
            "title_too_long", f"title must be <= {MAX_TITLE_CHARS} chars.", received=len(title)
        )
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise MemoryOpError(
            "description_too_long",
            f"description must be <= {MAX_DESCRIPTION_CHARS} chars.",
            received=len(description),
        )
    if len(content) > MAX_CONTENT_CHARS:
        raise MemoryOpError(
            "content_too_long",
            f"content must be <= {MAX_CONTENT_CHARS} chars.",
            received=len(content),
        )


def save(
    conn: sqlite3.Connection,
    *,
    type_: str,
    code: str,
    title: str,
    description: str,
    content: str,
) -> dict[str, Any]:
    """Insert a new entry. Raises on validation error or (type, code) conflict."""
    _validate_save(type_, code, title, description, content)
    existing = conn.execute(
        "SELECT id, type FROM user_memory WHERE type=? AND code=?", (type_, code)
    ).fetchone()
    if existing is not None:
        raise MemoryOpError(
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
    return {"type": type_, "code": code, "title": title}


def recall(conn: sqlite3.Connection, *, code: str) -> list[dict[str, Any]]:
    """Return all full entries matching ``code`` (most recent first). May be []."""
    if not code or not code.strip():
        raise MemoryOpError("invalid_args", "code is required for recall.")
    rows = conn.execute(
        "SELECT id, type, code, title, description, content, created_at, modified_at "
        "FROM user_memory WHERE code=? "
        "ORDER BY modified_at DESC",
        (code,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_(
    conn: sqlite3.Connection, *, type_filter: str | None = None
) -> list[dict[str, Any]]:
    """Return index entries (no content), optionally filtered by type."""
    if type_filter is not None and type_filter not in VALID_TYPES:
        raise MemoryOpError(
            "invalid_type",
            f"type must be one of {sorted(VALID_TYPES)}",
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
    return [dict(r) for r in rows]


def update(
    conn: sqlite3.Connection,
    *,
    code: str,
    title: str | None = None,
    description: str | None = None,
    content: str | None = None,
    type_: str | None = None,
) -> int:
    """Update one entry. Returns the affected id. Raises on not_found/ambiguous."""
    if not code or not code.strip():
        raise MemoryOpError("invalid_args", "code is required for update.")
    if title is None and description is None and content is None:
        raise MemoryOpError(
            "invalid_args",
            "update requires at least one of: title, description, content.",
        )
    if title is not None and len(title) > MAX_TITLE_CHARS:
        raise MemoryOpError(
            "title_too_long", f"title must be <= {MAX_TITLE_CHARS} chars.", received=len(title)
        )
    if description is not None and len(description) > MAX_DESCRIPTION_CHARS:
        raise MemoryOpError(
            "description_too_long",
            f"description must be <= {MAX_DESCRIPTION_CHARS} chars.",
            received=len(description),
        )
    if content is not None and len(content) > MAX_CONTENT_CHARS:
        raise MemoryOpError(
            "content_too_long",
            f"content must be <= {MAX_CONTENT_CHARS} chars.",
            received=len(content),
        )

    target_id = _resolve_single(conn, code=code, type_=type_)
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
    conn.execute(f"UPDATE user_memory SET {', '.join(sets)} WHERE id=?", params)
    return target_id


def delete(conn: sqlite3.Connection, *, code: str, type_: str | None = None) -> int:
    """Delete one entry. Returns the deleted id. Raises on not_found/ambiguous."""
    if not code or not code.strip():
        raise MemoryOpError("invalid_args", "code is required for delete.")
    target_id = _resolve_single(conn, code=code, type_=type_)
    conn.execute("DELETE FROM user_memory WHERE id=?", (target_id,))
    return target_id


def _resolve_single(
    conn: sqlite3.Connection, *, code: str, type_: str | None
) -> int:
    """Resolve a unique row id by code (+ optional type). Raises if 0 or ambiguous."""
    if type_ is not None:
        existing = conn.execute(
            "SELECT id, type FROM user_memory WHERE type=? AND code=?", (type_, code)
        ).fetchall()
    else:
        existing = conn.execute(
            "SELECT id, type FROM user_memory WHERE code=?", (code,)
        ).fetchall()
    if not existing:
        raise MemoryOpError("not_found", f"No entry with code='{code}'.", entry_code=code)
    if len(existing) > 1 and type_ is None:
        raise MemoryOpError(
            "ambiguous",
            f"Multiple entries share code='{code}'. Specify type to disambiguate.",
            matches=[{"type": r["type"], "id": r["id"]} for r in existing],
        )
    return existing[0]["id"]
