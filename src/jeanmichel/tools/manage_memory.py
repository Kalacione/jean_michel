"""Tool : manage_memory — READ long-term, scoped, cross-conversation memory.

Read-only : recall / search / list. CRUD + validation live in
``jeanmichel.service.memory`` (shared with the web API + the consolidation engine).
The agent does NOT write memory directly — to remember something durable it calls
``propose_memory`` (the write-proposal channel : the candidate is human-reviewed before
anything is written). This module binds the conversation context (``user_id`` = memory
owner, ``project_id`` = the conversation's project) and turns data / errors into
tool_ok/tool_error strings.

A memory has a ``scope`` that decides where it is later injected (deterministic) :

  user    → the current user           (target bound from the conversation)
  project → the conversation's project  (target bound from the conversation)
  tool    → any agent granted a tool    (target: tool_code, given by the LLM)
"""

from __future__ import annotations

import logging
from typing import Any

from ..db import cli_user_id
from ..db import connect as db_connect
from ..service import memory
from ._base import ToolSpec
from ._errors import tool_error, tool_ok

_log = logging.getLogger(__name__)

# Read-only tool surface. Writes go through propose_memory (human-reviewed).
_READ_ACTIONS: tuple[str, ...] = ("recall", "search", "list")


def _resolve_target(
    scope: str, *, uid: int, project_id: int | None, tool_code: str | None
) -> dict[str, Any]:
    """Map a scope to the concrete target kwargs for the service layer."""
    if scope == "user":
        return {"user_id": uid}
    if scope == "project":
        if project_id is None:
            raise memory.MemoryOpError(
                "no_project",
                "scope='project' requires the conversation to be attached to a project.",
            )
        return {"project_id": project_id}
    if scope == "tool":
        return {"tool_code": tool_code}
    raise memory.MemoryOpError("invalid_scope", f"unknown scope '{scope}'.")


def _visible_scopes(uid: int, project_id: int | None) -> list[tuple[str, dict[str, Any]]]:
    """The scopes a browse (list/search without an explicit scope) spans."""
    out: list[tuple[str, dict[str, Any]]] = [("user", {"user_id": uid})]
    if project_id is not None:
        out.append(("project", {"project_id": project_id}))
    return out


def _handler(
    action: str,
    scope: str | None = None,
    code: str | None = None,
    tool_code: str | None = None,
    query: str | None = None,
    limit: int | None = None,
    *,
    user_id: int | None = None,
    project_id: int | None = None,
) -> str:
    """Dispatch a READ action, bound to the conversation's user/project context."""
    if action not in _READ_ACTIONS:
        return tool_error(
            "invalid_action",
            f"manage_memory is read-only ; action must be one of {list(_READ_ACTIONS)}. "
            "To remember something, use propose_memory.",
            received=action,
        )

    try:
        with db_connect() as conn:
            uid = user_id if user_id is not None else cli_user_id(conn)

            if action == "recall":
                if scope is None:
                    return tool_error("invalid_args", "scope is required for recall.")
                target = _resolve_target(scope, uid=uid, project_id=project_id, tool_code=tool_code)
                row = memory.recall(conn, scope=scope, code=code or "", **target)
                if row is None:
                    return tool_error(
                        "not_found",
                        f"No {scope} entry with code='{code or ''}'.",
                        scope=scope,
                        entry_code=code or "",
                    )
                return tool_ok(f"Recalled {row['scope']}/{row['code']}: {row['title']}", entry=row)

            if action == "search":
                if not query:
                    return tool_error("invalid_args", "query is required for search.")
                cap = int(limit) if limit else memory.DEFAULT_SEARCH_LIMIT
                if scope is not None:
                    target = _resolve_target(
                        scope, uid=uid, project_id=project_id, tool_code=tool_code
                    )
                    hits = memory.search(conn, query=query, scope=scope, limit=cap, **target)
                else:
                    merged: list[dict[str, Any]] = []
                    for sc, target in _visible_scopes(uid, project_id):
                        merged.extend(
                            memory.search(conn, query=query, scope=sc, limit=cap, **target)
                        )
                    merged.sort(key=lambda r: r["score"])
                    hits = merged[:cap]
                return tool_ok(f"{len(hits)} match(es) for '{query}'.", count=len(hits), results=hits)

            if action == "list":
                if scope is not None:
                    target = _resolve_target(
                        scope, uid=uid, project_id=project_id, tool_code=tool_code
                    )
                    entries = memory.list_(conn, scope=scope, **target)
                else:
                    entries = []
                    for sc, target in _visible_scopes(uid, project_id):
                        entries.extend(memory.list_(conn, scope=sc, **target))
                return tool_ok(f"{len(entries)} entries", count=len(entries), entries=entries)
    except memory.MemoryOpError as exc:
        return tool_error(exc.code, exc.message, **exc.extra)
    except Exception as exc:  # noqa: BLE001
        _log.warning("manage_memory unexpected error: %s", exc)
        return tool_error("internal_error", str(exc))

    # Unreachable
    return tool_error("internal_error", "no branch matched")


_PARAMETERS = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(_READ_ACTIONS),
            "description": "recall (full body by code), search (full-text), list (index). Read-only.",
        },
        "scope": {
            "type": "string",
            "enum": sorted(memory.VALID_SCOPES),
            "description": (
                "user (about the human), project (the current project), tool "
                "(operational note for a tool). Required for recall ; optional filter for list/search."
            ),
        },
        "code": {
            "type": "string",
            "description": "Short kebab-case slug. Required for recall.",
        },
        "tool_code": {
            "type": "string",
            "description": "Target tool name. Required when scope='tool'.",
        },
        "query": {
            "type": "string",
            "description": "Free-text query for search (full-text, BM25-ranked).",
        },
        "limit": {
            "type": "integer",
            "description": f"Max search results (default {memory.DEFAULT_SEARCH_LIMIT}).",
        },
    },
    "required": ["action"],
}

_DESCRIPTION = (
    "READ long-term, scoped, cross-conversation memory. Scopes: user (durable facts about "
    "the human), project (the current project), tool (operational notes). Actions: recall "
    "(load full body by code), search (full-text BM25 ranking — use it before concluding "
    "you don't know something), list (index). To ADD or refine memory, use propose_memory "
    "(it proposes a candidate the human reviews — manage_memory never writes)."
)


def make_spec(user_id: int | None = None, project_id: int | None = None) -> ToolSpec:
    """Return a ToolSpec bound to the conversation context (memory owner + project)."""

    def handler(
        action: str,
        scope: str | None = None,
        code: str | None = None,
        tool_code: str | None = None,
        query: str | None = None,
        limit: int | None = None,
    ) -> str:
        return _handler(
            action,
            scope=scope,
            code=code,
            tool_code=tool_code,
            query=query,
            limit=limit,
            user_id=user_id,
            project_id=project_id,
        )

    return ToolSpec(
        name="manage_memory",
        description=_DESCRIPTION,
        parameters=_PARAMETERS,
        handler=handler,
    )


# Module-level default (cli-scoped, no project) — used by the registry fallback + tests.
SPEC = make_spec()
