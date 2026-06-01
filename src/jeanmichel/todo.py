"""Persistent per-conversation TODO — the orchestrator's living plan (PDCA).

One flat list per conversation, stored at ``conv_folder/todo.json`` (the
conversation root, NOT the workspace). The orchestrator (router) is the SOLE
writer, via the ``todo_write`` tool; it rewrites the whole list on every
update (mark done / append / modify / reorder). Workers never write it — they
propose changes through ``report_back.suggested_todo_updates`` and the
orchestrator disposes.

The list is re-surfaced to the orchestrator each turn as a ``[TODO-RECAP]``
message injected by the ``PreLLMCall`` hook (main agent only).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TODO_FILENAME = "todo.json"
RECAP_MARKER = "[TODO-RECAP]"
STATUSES = ("pending", "in_progress", "done")
_GLYPH = {"done": "[x]", "in_progress": "[>]", "pending": "[ ]"}


def todo_path(conv_folder: Path) -> Path:
    return conv_folder / TODO_FILENAME


def load_todo(conv_folder: Path) -> dict[str, Any] | None:
    """Return the stored TODO dict, or None when absent/empty/unreadable."""
    try:
        raw = todo_path(conv_folder).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("items"):
        return None
    return data


def save_todo(conv_folder: Path, goal: str, items: list[dict[str, Any]]) -> None:
    """Atomically write the whole TODO list (tmp + replace)."""
    conv_folder.mkdir(parents=True, exist_ok=True)
    payload = {"goal": goal, "items": items}
    p = todo_path(conv_folder)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def clear_todo(conv_folder: Path) -> None:
    """Remove the TODO file (called when every item is done)."""
    todo_path(conv_folder).unlink(missing_ok=True)


def normalize_items(items: Any) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Validate + normalize a raw items payload.

    Returns ``(items, None)`` on success or ``(None, error_message)`` on
    rejection. Rules: a non-empty list; each item has a non-empty ``text`` and
    a ``status`` in {pending, in_progress, done}; AT MOST ONE ``in_progress``.
    A stable ``id`` is taken from the item or assigned by position.
    """
    if not isinstance(items, list) or not items:
        return None, "todo_write requires a non-empty 'items' list."
    out: list[dict[str, Any]] = []
    in_progress = 0
    for i, raw in enumerate(items, start=1):
        if not isinstance(raw, dict):
            return None, f"item #{i} must be an object with 'text' and 'status'."
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            return None, f"item #{i} requires a non-empty 'text' string."
        status = raw.get("status", "pending")
        if status not in STATUSES:
            return None, (
                f"item #{i} 'status' must be one of {STATUSES}. Got: {status!r}."
            )
        if status == "in_progress":
            in_progress += 1
        out.append({
            "id": str(raw.get("id") or i),
            "text": text.strip(),
            "status": status,
        })
    if in_progress > 1:
        return None, (
            f"At most ONE item may be 'in_progress' at a time (got {in_progress}). "
            "Mark the others 'pending' or 'done'."
        )
    return out, None


def render_recap(todo: dict[str, Any]) -> str:
    """Render the ``[TODO-RECAP]`` block injected into the orchestrator each turn."""
    items = todo.get("items") or []
    goal = (todo.get("goal") or "").strip()
    done = sum(1 for it in items if it.get("status") == "done")
    lines = [f"{RECAP_MARKER} Plan ({done}/{len(items)} done)"]
    if goal:
        lines.append(f"Goal: {goal}")
    for it in items:
        glyph = _GLYPH.get(it.get("status", "pending"), "[ ]")
        lines.append(f"  {glyph} {it.get('id')}. {it.get('text')}")
    nxt = next((it for it in items if it.get("status") == "in_progress"), None)
    if nxt is None:
        nxt = next((it for it in items if it.get("status") == "pending"), None)
    if nxt is not None:
        lines.append(f"Next action: {nxt.get('text')}")
    else:
        lines.append("All items done — synthesize the final answer.")
    return "\n".join(lines)
