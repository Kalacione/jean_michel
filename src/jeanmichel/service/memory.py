"""Long-term memory CRUD + FTS — pure data layer, single SQL source.

Shared by the ``manage_memory`` tool (LLM-facing), the web API, and the
consolidation engine. Validation + SQL live here once.

A single ``scope`` dimension drives deterministic prompt inclusion :

  user    → one user                           (target: user_id)
  project → one project                        (target: project_id)
  tool    → any agent granted that tool         (target: tool_code)

A row is uniquely addressed by ``(scope, target, code)`` (enforced by the
partial unique indexes on ``memory``), so there is never any ambiguity.

Full-text recall / dedup uses the FTS5 ``memory_fts`` table with BM25 ranking
(``search``). Errors are signalled by raising ``MemoryOpError(code, message,
**extra)`` ; success returns plain data (dicts / ids).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from typing import Any

VALID_SCOPES: frozenset[str] = frozenset({"user", "project", "tool"})
VALID_ACTIONS: frozenset[str] = frozenset(
    {"save", "recall", "search", "list", "update", "delete"}
)

# Hard caps (cf. §10 doc 06).
MAX_TITLE_CHARS = 60
MAX_DESCRIPTION_CHARS = 150
MAX_CONTENT_CHARS = 1_000

# FTS search default fan-out (top-K). MATCH already filters to rows that contain
# the query terms, so this caps how many of those ranked hits we return.
DEFAULT_SEARCH_LIMIT = 10

_COLS = (
    "id", "scope", "user_id", "project_id", "tool_code", "code", "title",
    "description", "content", "importance", "created_at", "modified_at",
)
_SELECT_COLS = ", ".join(_COLS)
_SELECT_COLS_M = ", ".join(f"m.{c}" for c in _COLS)  # for the FTS join (disambiguated)
_INDEX_COLS = (
    "id, scope, user_id, project_id, tool_code, code, title, description, importance, modified_at"
)


class MemoryOpError(Exception):
    """A memory operation failed. Carries a stable error code + extras."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clamp_importance(v: Any) -> int:
    """Coerce to an int in [1, 5] (the injection-ranking weight). Default 3 on garbage."""
    try:
        return max(1, min(5, int(v)))
    except (TypeError, ValueError):
        return 3


# ---------------------------------------------------------------------------
# Scope / target resolution
# ---------------------------------------------------------------------------

def _check_scope(scope: str) -> None:
    if scope not in VALID_SCOPES:
        raise MemoryOpError(
            "invalid_scope", f"scope must be one of {sorted(VALID_SCOPES)}.", received=scope
        )


def _target_clause(
    scope: str,
    *,
    user_id: int | None,
    project_id: int | None,
    tool_code: str | None,
) -> tuple[str, list[Any]]:
    """Return the SQL fragment + params pinning a scoped row to its target.

    Requires the target key that the scope demands ; raises otherwise. The
    result always begins with ``scope=?`` so callers can append it directly.
    """
    _check_scope(scope)
    if scope == "user":
        if user_id is None:
            raise MemoryOpError("invalid_args", "user_id is required for scope='user'.")
        return "scope=? AND user_id=?", [scope, user_id]
    if scope == "project":
        if project_id is None:
            raise MemoryOpError("invalid_args", "project_id is required for scope='project'.")
        return "scope=? AND project_id=?", [scope, project_id]
    # tool
    if not tool_code or not tool_code.strip():
        raise MemoryOpError("invalid_args", "tool_code is required for scope='tool'.")
    return "scope=? AND tool_code=?", [scope, tool_code]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_code(code: str) -> None:
    if not code or not code.strip():
        raise MemoryOpError("invalid_args", "code is required.")
    if " " in code:
        raise MemoryOpError(
            "invalid_code",
            "code must not contain spaces. Use kebab-case (e.g. 'unity-montreal').",
        )


def _validate_field(name: str, value: str, max_chars: int) -> None:
    if not value or not value.strip():
        raise MemoryOpError("invalid_args", f"{name} is required.")
    if len(value) > max_chars:
        raise MemoryOpError(
            f"{name}_too_long", f"{name} must be <= {max_chars} chars.", received=len(value)
        )


def _validate_save(code: str, title: str, description: str, content: str) -> None:
    _validate_code(code)
    _validate_field("title", title, MAX_TITLE_CHARS)
    _validate_field("description", description, MAX_DESCRIPTION_CHARS)
    _validate_field("content", content, MAX_CONTENT_CHARS)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def save(
    conn: sqlite3.Connection,
    *,
    scope: str,
    code: str,
    title: str,
    description: str,
    content: str,
    user_id: int | None = None,
    project_id: int | None = None,
    tool_code: str | None = None,
    importance: int = 3,
) -> dict[str, Any]:
    """Insert a new entry in ``scope``. Raises on validation / (scope,target,code) conflict."""
    clause, params = _target_clause(
        scope, user_id=user_id, project_id=project_id, tool_code=tool_code
    )
    _validate_save(code, title, description, content)

    existing = conn.execute(
        f"SELECT id FROM memory WHERE {clause} AND code=?", (*params, code)
    ).fetchone()
    if existing is not None:
        raise MemoryOpError(
            "already_exists",
            (
                f"An entry with scope='{scope}' and code='{code}' already exists. "
                "Update it instead."
            ),
            scope=scope,
            entry_code=code,
        )

    now = _now()
    conn.execute(
        "INSERT INTO memory "
        "(scope, user_id, project_id, tool_code, code, title, description, content, "
        "importance, created_at, modified_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (scope, user_id, project_id, tool_code, code, title, description, content,
         _clamp_importance(importance), now, now),
    )
    return {"scope": scope, "code": code, "title": title}


def recall(
    conn: sqlite3.Connection,
    *,
    scope: str,
    code: str,
    user_id: int | None = None,
    project_id: int | None = None,
    tool_code: str | None = None,
) -> dict[str, Any] | None:
    """Return the full entry uniquely identified by ``(scope, target, code)``, or None."""
    if not code or not code.strip():
        raise MemoryOpError("invalid_args", "code is required for recall.")
    clause, params = _target_clause(
        scope, user_id=user_id, project_id=project_id, tool_code=tool_code
    )
    row = conn.execute(
        f"SELECT {_SELECT_COLS} FROM memory WHERE {clause} AND code=?", (*params, code)
    ).fetchone()
    return dict(row) if row is not None else None


def _filter_clause(
    *,
    scope: str | None,
    user_id: int | None,
    project_id: int | None,
    tool_code: str | None,
) -> tuple[str, list[Any]]:
    """Build a permissive WHERE for list/search : every provided key is ANDed."""
    if scope is not None:
        _check_scope(scope)
    parts: list[str] = []
    params: list[Any] = []
    if scope is not None:
        parts.append("m.scope=?")
        params.append(scope)
    if user_id is not None:
        parts.append("m.user_id=?")
        params.append(user_id)
    if project_id is not None:
        parts.append("m.project_id=?")
        params.append(project_id)
    if tool_code is not None:
        parts.append("m.tool_code=?")
        params.append(tool_code)
    return (" AND ".join(parts) if parts else ""), params


def list_(
    conn: sqlite3.Connection,
    *,
    scope: str | None = None,
    user_id: int | None = None,
    project_id: int | None = None,
    tool_code: str | None = None,
) -> list[dict[str, Any]]:
    """Return index entries (no content), filtered by any combination of keys."""
    where, params = _filter_clause(
        scope=scope, user_id=user_id, project_id=project_id, tool_code=tool_code
    )
    sql = f"SELECT {_INDEX_COLS} FROM memory m"
    if where:
        sql += f" WHERE {where}"
    sql += " ORDER BY modified_at DESC"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


_FTS_TOKEN = re.compile(r"[^\w]+", re.UNICODE)


def _fts_match(query: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression : quoted tokens OR-ed.

    Quoting each token sidesteps FTS5 query-syntax errors on punctuation and
    treats the input as a bag of words ranked by BM25 (no boolean surprises).
    """
    tokens = [t for t in _FTS_TOKEN.split(query.strip()) if t]
    if not tokens:
        raise MemoryOpError("invalid_args", "search query is empty.")
    return " OR ".join(f'"{t}"' for t in tokens)


def search(
    conn: sqlite3.Connection,
    *,
    query: str,
    scope: str | None = None,
    user_id: int | None = None,
    project_id: int | None = None,
    tool_code: str | None = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> list[dict[str, Any]]:
    """Full-text search (FTS5 + BM25), best match first, capped at ``limit``.

    Deterministic : only rows whose text matches the query terms are returned,
    ordered by BM25 relevance. Each result carries a ``score`` (BM25 ; lower is
    a better match). Filters by scope/target like ``list_``.
    """
    match = _fts_match(query)
    where, params = _filter_clause(
        scope=scope, user_id=user_id, project_id=project_id, tool_code=tool_code
    )
    sql = (
        f"SELECT {_SELECT_COLS_M}, bm25(memory_fts) AS score "
        "FROM memory_fts f JOIN memory m ON m.id = f.rowid "
        "WHERE memory_fts MATCH ?"
    )
    qparams: list[Any] = [match]
    if where:
        sql += f" AND {where}"
        qparams.extend(params)
    sql += " ORDER BY bm25(memory_fts) LIMIT ?"
    qparams.append(int(limit))
    rows = conn.execute(sql, qparams).fetchall()
    return [dict(r) for r in rows]


def update(
    conn: sqlite3.Connection,
    *,
    scope: str,
    code: str,
    user_id: int | None = None,
    project_id: int | None = None,
    tool_code: str | None = None,
    title: str | None = None,
    description: str | None = None,
    content: str | None = None,
    importance: int | None = None,
) -> int:
    """Update the entry identified by ``(scope, target, code)``. Returns its id."""
    if not code or not code.strip():
        raise MemoryOpError("invalid_args", "code is required for update.")
    if title is None and description is None and content is None and importance is None:
        raise MemoryOpError(
            "invalid_args",
            "update requires at least one of: title, description, content, importance.",
        )
    if title is not None:
        _validate_field("title", title, MAX_TITLE_CHARS)
    if description is not None:
        _validate_field("description", description, MAX_DESCRIPTION_CHARS)
    if content is not None:
        _validate_field("content", content, MAX_CONTENT_CHARS)

    target_id = _resolve_id(
        conn, scope=scope, code=code, user_id=user_id, project_id=project_id, tool_code=tool_code
    )
    sets: list[str] = []
    params: list[Any] = []
    for col, val in (("title", title), ("description", description), ("content", content)):
        if val is not None:
            sets.append(f"{col}=?")
            params.append(val)
    if importance is not None:
        sets.append("importance=?")
        params.append(_clamp_importance(importance))
    sets.append("modified_at=?")
    params.append(_now())
    params.append(target_id)
    conn.execute(f"UPDATE memory SET {', '.join(sets)} WHERE id=?", params)
    return target_id


def delete(
    conn: sqlite3.Connection,
    *,
    scope: str,
    code: str,
    user_id: int | None = None,
    project_id: int | None = None,
    tool_code: str | None = None,
) -> int:
    """Delete the entry identified by ``(scope, target, code)``. Returns its id."""
    if not code or not code.strip():
        raise MemoryOpError("invalid_args", "code is required for delete.")
    target_id = _resolve_id(
        conn, scope=scope, code=code, user_id=user_id, project_id=project_id, tool_code=tool_code
    )
    conn.execute("DELETE FROM memory WHERE id=?", (target_id,))
    return target_id


def _resolve_id(
    conn: sqlite3.Connection,
    *,
    scope: str,
    code: str,
    user_id: int | None,
    project_id: int | None,
    tool_code: str | None,
) -> int:
    """Resolve the unique row id for ``(scope, target, code)`` or raise not_found."""
    clause, params = _target_clause(
        scope, user_id=user_id, project_id=project_id, tool_code=tool_code
    )
    row = conn.execute(
        f"SELECT id FROM memory WHERE {clause} AND code=?", (*params, code)
    ).fetchone()
    if row is None:
        raise MemoryOpError(
            "not_found", f"No {scope} entry with code='{code}'.", scope=scope, entry_code=code
        )
    return row["id"]
