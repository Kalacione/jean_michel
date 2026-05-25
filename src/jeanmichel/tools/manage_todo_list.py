"""Tool: manage_todo_list — persistent TODO list scoped per conversation or request.

Router (jean-michel) writes to ``conv_folder/todo.json`` (conversation-level).
Specialists write to ``conv_folder/todo_<request_id>.json`` (request-level).
Finalizers do not receive this tool (no grant in agent_tools).

The scope is determined automatically from ``agent_role`` — the LLM never
sees a ``scope`` parameter.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from ._base import ToolSpec
from ._errors import tool_error, tool_ok

_VALID_STATUSES = frozenset({"pending", "in_progress", "completed", "skipped", "blocked"})
_VALID_KEYS = frozenset({"id", "title", "status", "depends_on", "assignee_hint", "note"})
_MAX_ITEMS = 20


# ── Path resolution ───────────────────────────────────────────────────────────

def _todo_path(
    conv_folder: Path,
    agent_role: str,
    request_id_provider: Callable[[], str] | None,
) -> Path:
    if agent_role == "router":
        return conv_folder / "todo.json"
    if agent_role == "specialist":
        if request_id_provider is None:
            raise RuntimeError("request_id_provider required for specialist todo scope")
        return conv_folder / f"todo_{request_id_provider()}.json"
    raise RuntimeError(f"manage_todo_list called with unexpected role: {agent_role!r}")


# ── Stats helpers ─────────────────────────────────────────────────────────────

def _compute_stats(todos: list[dict]) -> dict:
    stats: dict[str, int] = {
        "total": len(todos),
        "pending": 0,
        "in_progress": 0,
        "completed": 0,
        "skipped": 0,
        "blocked": 0,
    }
    for item in todos:
        s = item.get("status", "pending")
        if s in stats:
            stats[s] += 1
    return stats


def _summary_line(op: str, stats: dict) -> str:
    n_done = stats["completed"] + stats["skipped"]
    n_total = stats["total"]
    n_progress = stats["in_progress"]
    return f"{op}: {n_done}/{n_total} done, {n_progress} in progress"


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_todos(todos: list[dict]) -> str | None:
    """Return an error message if validation fails, else None."""
    if not isinstance(todos, list):
        return "todos must be a list"
    if len(todos) > _MAX_ITEMS:
        return f"too many todos: {len(todos)} > {_MAX_ITEMS}"

    ids_seen: set[str] = set()
    for i, item in enumerate(todos):
        if not isinstance(item, dict):
            return f"item {i} is not an object"

        unexpected = set(item.keys()) - _VALID_KEYS
        if unexpected:
            return f"item {i} has unexpected keys: {sorted(unexpected)}"

        item_id = item.get("id")
        if not item_id or not isinstance(item_id, str):
            return f"item {i} missing or empty 'id'"
        if item_id in ids_seen:
            return f"duplicate id: {item_id!r}"
        ids_seen.add(item_id)

        if not item.get("title") or not isinstance(item.get("title"), str):
            return f"item {item_id!r} missing or empty 'title'"

        status = item.get("status", "pending")
        if status not in _VALID_STATUSES:
            return f"item {item_id!r} has invalid status: {status!r}"

        depends_on = item.get("depends_on")
        if depends_on is not None:
            if not isinstance(depends_on, list):
                return f"item {item_id!r} 'depends_on' must be a list"
            for dep in depends_on:
                if not isinstance(dep, str):
                    return f"item {item_id!r} 'depends_on' values must be strings"

    # DAG check: no cycles
    all_ids = {item["id"] for item in todos}
    deps_map: dict[str, list[str]] = {
        item["id"]: [d for d in (item.get("depends_on") or []) if d in all_ids]
        for item in todos
    }
    visited: set[str] = set()
    stack: set[str] = set()

    def _has_cycle(node: str) -> bool:
        visited.add(node)
        stack.add(node)
        for neighbour in deps_map.get(node, []):
            if neighbour not in visited:
                if _has_cycle(neighbour):
                    return True
            elif neighbour in stack:
                return True
        stack.discard(node)
        return False

    for node in deps_map:
        if node not in visited:
            if _has_cycle(node):
                return "invalid_dependency_graph: cycle detected in depends_on"

    return None


# ── Atomic write ──────────────────────────────────────────────────────────────

def _write_json(path: Path, todos: list[dict]) -> None:
    payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "todos": todos,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".todo_tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("todos", []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, OSError):
        return []


# ── Handlers ─────────────────────────────────────────────────────────────────

def _make_handler(
    conv_folder: Path,
    agent_role: str,
    request_id_provider: Callable[[], str] | None,
) -> Callable[..., str]:

    def _handler(
        operation: str,
        todos: list[dict] | None = None,
        id: str | None = None,
        status: str | None = None,
        note: str | None = None,
    ) -> str:
        path = _todo_path(conv_folder, agent_role, request_id_provider)

        if operation == "write":
            if todos is None:
                return tool_error("missing_argument", "'todos' is required for write operation")
            err = _validate_todos(todos)
            if err:
                code = "invalid_dependency_graph" if "cycle" in err else (
                    "too_many_todos" if "too many" in err else "validation_error"
                )
                return tool_error(code, err)
            _write_json(path, todos)
            stats = _compute_stats(todos)
            return tool_ok(_summary_line("write", stats), todos=todos, stats=stats)

        elif operation == "read":
            current = _read_json(path)
            stats = _compute_stats(current)
            return tool_ok(_summary_line("read", stats), todos=current, stats=stats)

        elif operation == "update_status":
            if not id:
                return tool_error("missing_argument", "'id' is required for update_status")
            if not status:
                return tool_error("missing_argument", "'status' is required for update_status")
            if status not in _VALID_STATUSES:
                return tool_error("invalid_status", f"Unknown status: {status!r}. Must be one of {sorted(_VALID_STATUSES)}")
            current = _read_json(path)
            item = next((x for x in current if x.get("id") == id), None)
            if item is None:
                return tool_error("todo_not_found", f"No todo with id={id!r}")
            item["status"] = status
            if note is not None:
                item["note"] = note
            _write_json(path, current)
            stats = _compute_stats(current)
            return tool_ok(_summary_line("update_status", stats), todos=current, stats=stats)

        else:
            return tool_error("unknown_operation", f"Unknown operation: {operation!r}. Must be write, read, or update_status")

    return _handler


# ── Public factory ────────────────────────────────────────────────────────────

def make_spec(
    conv_folder: Path,
    agent_role: str,
    request_id_provider: Callable[[], str] | None,
) -> ToolSpec:
    """Return a ToolSpec for manage_todo_list bound to this agent context.

    Args:
        conv_folder: Conversation folder path.
        agent_role: "router" or "specialist". Determines file path scope.
        request_id_provider: Callable returning current request_id (required for specialist).
    """
    return ToolSpec(
        name="manage_todo_list",
        description=(
            "Manage a TODO list to plan and track multi-step work. "
            "Use 'write' to set the full list (replaces existing), "
            "'read' to get the current list, "
            "'update_status' to update a single item's status."
        ),
        parameters={
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["write", "read", "update_status"],
                    "description": "Operation to perform.",
                },
                "todos": {
                    "type": "array",
                    "description": (
                        "Required for 'write'. Full list of todo items. "
                        "Each item: {id (str), title (str), status (pending|in_progress|completed|skipped|blocked), "
                        "depends_on? (list[str]), assignee_hint? (str), note? (str)}. Max 20 items."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed", "skipped", "blocked"],
                            },
                            "depends_on": {"type": "array", "items": {"type": "string"}},
                            "assignee_hint": {"type": "string"},
                            "note": {"type": "string"},
                        },
                        "required": ["id", "title", "status"],
                    },
                },
                "id": {
                    "type": "string",
                    "description": "Required for 'update_status'. The id of the item to update.",
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "skipped", "blocked"],
                    "description": "Required for 'update_status'. New status.",
                },
                "note": {
                    "type": "string",
                    "description": "Optional for 'update_status'. Short note on result or reason.",
                },
            },
            "required": ["operation"],
        },
        handler=_make_handler(conv_folder, agent_role, request_id_provider),
    )
