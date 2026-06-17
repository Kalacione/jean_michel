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


# ---- Rich plan document (markdown) ----------------------------------------
# The PLAN turn authors a SUBSTANTIVE plan (Context/analysis, steps WITH detail
# and rationale, verification) via the ``plan_write`` tool, stored at
# ``conv_folder/plan.md`` (conversation root, alongside todo.json). It is the
# durable reasoning the human approves and that is re-injected into every
# execution turn — todo.json stays as the terse progress tracker. (This replaces
# the old auto-rendered checklist mirror, which carried no analysis.)
PLAN_FILENAME = "plan.md"


def plan_file_for(plan_id: str) -> str:
    """Canonical conv-relative path of a plan by id (Phase 2 R2.1). Plans live in the shared
    ``workspace/`` so agents read/edit them with their own workspace tools ; the agent's
    workspace-relative name is just ``plan_<id>.md``. (Legacy convs keep a conv-root ``plan.md``
    referenced by the entry's ``plan_file`` — see the load/save_plan wrappers.)"""
    return f"workspace/plan_{plan_id}.md"


# ---- by-filename plan I/O (conv-relative path ; new plans live under workspace/) ----------


def load_plan_file(conv_folder: Path, filename: str) -> str | None:
    """Return the markdown stored at conv-relative ``filename``, or None when absent/empty/unreadable."""
    try:
        raw = (conv_folder / filename).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return raw or None


def save_plan_file(conv_folder: Path, filename: str, markdown: str) -> None:
    """Atomically write a plan document at conv-relative ``filename`` (tmp + replace ; creates
    parent dirs, e.g. ``workspace/``)."""
    p = conv_folder / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(markdown, encoding="utf-8")
    tmp.replace(p)


def clear_plan_file(conv_folder: Path, filename: str) -> None:
    """Remove a specific plan file."""
    (conv_folder / filename).unlink(missing_ok=True)


def _active_plan_file(state: Any) -> str | None:
    """Conv-relative path of the ACTIVE plan from the referent (None if no active plan)."""
    pid = getattr(state, "active_plan_id", None)
    if not pid:
        return None
    entry = (getattr(state, "plans", None) or {}).get(pid) or {}
    return entry.get("plan_file") or plan_file_for(pid)


def load_active_plan(conv_folder: Path, state: Any) -> str | None:
    """Read the ACTIVE plan's markdown (resolved via state.active_plan_id → its plan_file)."""
    pf = _active_plan_file(state)
    return load_plan_file(conv_folder, pf) if pf else None


def save_active_plan(conv_folder: Path, markdown: str) -> str:
    """Write ``markdown`` to the ACTIVE plan's file (workspace/plan_<id>.md). The id is resolved from
    state.json — the orchestrator assigns active_plan_id BEFORE plan_write runs. Returns the path."""
    from .models import ConversationState
    from .persistence import load_state
    state = ConversationState.from_dict(load_state(conv_folder))
    pf = _active_plan_file(state) or plan_file_for(state.active_plan_id or "p1")
    save_plan_file(conv_folder, pf, markdown)
    return pf


# ---- legacy active-plan wrappers (conv-root plan.md) : back-compat for older convs --------


def load_plan(conv_folder: Path) -> str | None:
    """Legacy : the conv-root plan.md (pre-Phase-2 active plan). Prefer load_active_plan."""
    return load_plan_file(conv_folder, PLAN_FILENAME)


def save_plan(conv_folder: Path, markdown: str) -> None:
    """Legacy : write the conv-root plan.md."""
    save_plan_file(conv_folder, PLAN_FILENAME, markdown)


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
    """Atomically write the whole TODO list (tmp + replace).

    The TODO is the terse EXECUTION tracker — created during execution (after a plan is
    approved) or self-initiated for a multi-step task that needs no formal plan. It is
    DECOUPLED from the plan and carries no acceptance status (that lives on the referent,
    ``state.plans[id].approved``)."""
    conv_folder.mkdir(parents=True, exist_ok=True)
    payload = {"goal": goal, "items": items}
    p = todo_path(conv_folder)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def clear_todo(conv_folder: Path) -> None:
    """Remove the TODO tracker (called when every item is done). The plan is left
    untouched — plan and todo are independent artifacts."""
    todo_path(conv_folder).unlink(missing_ok=True)


# ---- Plan acceptance lifecycle -------------------------------------------
# The acceptance (proposed/accepted) now lives in the REFERENT : `state.plans[id].approved`
# (cf. orchestrator._reconcile_plan_approval). No more `plan_status.json` sidecar.


def clear_plan(conv_folder: Path) -> None:
    """Remove the ACTIVE plan document (plan.md). The todo + the referent entry are cleared
    by the caller."""
    clear_plan_file(conv_folder, PLAN_FILENAME)


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


def set_status(
    conv_folder: Path, item_id: str, status: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Update ONE item's status (granular alternative to whole-list todo_write).

    Returns ``(updated_todo, None)`` or ``(None, error)``. Keeps the AT-MOST-ONE
    ``in_progress`` invariant by rejecting a second in_progress with a clear hint.
    """
    if status not in STATUSES:
        return None, f"status must be one of {STATUSES}. Got: {status!r}."
    todo = load_todo(conv_folder)
    if todo is None:
        return None, "no plan to update — create one first with todo_write."
    items = todo.get("items") or []
    sid = str(item_id)
    target = next((it for it in items if str(it.get("id")) == sid), None)
    if target is None:
        ids = ", ".join(str(it.get("id")) for it in items) or "(none)"
        return None, f"unknown item id {sid!r}. Existing ids: {ids}."
    if status == "in_progress":
        other = next(
            (it for it in items if it is not target and it.get("status") == "in_progress"),
            None,
        )
        if other is not None:
            return None, (
                f"item {other.get('id')} is already in_progress — mark it 'done' or "
                "'pending' before starting another (at most one in_progress)."
            )
    target["status"] = status
    save_todo(conv_folder, todo.get("goal", ""), items)
    return {"goal": todo.get("goal", ""), "items": items}, None


def render_recap(todo: dict[str, Any]) -> str:
    """Render the ``[TODO-RECAP]`` block injected into the orchestrator each turn."""
    items = todo.get("items") or []
    goal = (todo.get("goal") or "").strip()
    done = sum(1 for it in items if it.get("status") == "done")
    lines = [f"{RECAP_MARKER} (orchestrator control — not the human user) Plan ({done}/{len(items)} done)"]
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
