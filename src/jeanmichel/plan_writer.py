"""Deterministic, orchestrator-only writer for the live ``plan.md``.

The plan is a hierarchical markdown document, one ``## Sx`` section per
delegated step, with task / summary / files-produced / timestamped action
log. Specialists' tool calls are recorded under their step so the router
(and any peer) can see "already searched X → found Y" and avoid redundant
work.

Only the orchestrator writes the plan. The LLM never edits it.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

__all__ = ["plan_path", "write", "log_action"]

# Tools whose results are worth summarising in the plan's action log.
# Anything not in this set is silently ignored to keep the plan readable.
_LOGGED_TOOLS: frozenset[str] = frozenset({
    "web_search",
    "wikipedia_search",
    "wikipedia_fetch",
    "wikipedia_summary",
    "weather",
    "workspace_create_file",
    "workspace_str_replace",
    "workspace_list",
    "workspace_view",
    "conv_read_file",
    "conv_list",
    "conv_history_scan",
    "self_inspect_agent",
    "self_inspect_paradigm",
})

_STATUS_ICON = {
    "in_progress": "🔄",
    "done": "✅",
    "blocked": "🚫",
    "partial": "⚠️",
}

_MAX_ACTIONS_PER_STEP = 40
_MAX_SUMMARY_CHARS = 160
_MAX_BRIEFING_CHARS = 240


def plan_path(conv_folder: Path) -> Path:
    """Absolute path of the plan.md file for a conversation."""
    return conv_folder / "plan.md"


def _truncate(s: str, n: int) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _summarize_result(tool_name: str, result_str: str) -> str:
    """Return a short, human-readable summary of a tool's result.

    ``result_str`` is the JSON-serialised tool response (or a raw string).
    We parse defensively — any structural mismatch falls back to a generic
    "ok" / truncated raw string.
    """
    raw = result_str or ""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return _truncate(raw, 120)

    if not isinstance(data, dict):
        return _truncate(raw, 120)

    if data.get("error"):
        return f"error: {_truncate(str(data['error']), 100)}"

    if tool_name == "web_search":
        results = data.get("results") or []
        n = len(results)
        titles = [r.get("title", "") for r in results[:3] if isinstance(r, dict)]
        if titles:
            return f"{n} hits: " + " | ".join(_truncate(t, 40) for t in titles)
        return f"{n} hits"

    if tool_name in ("wikipedia_search",):
        results = data.get("results") or []
        n = len(results)
        titles = [r.get("title", "") for r in results[:3] if isinstance(r, dict)]
        if titles:
            return f"{n} pages: " + " | ".join(_truncate(t, 40) for t in titles)
        return f"{n} pages"

    if tool_name in ("wikipedia_fetch", "wikipedia_summary"):
        title = data.get("title") or data.get("page") or "?"
        content = data.get("content") or data.get("summary") or ""
        return f"page '{_truncate(title, 60)}' ({len(content)} chars)"

    if tool_name == "weather":
        loc = data.get("location") or data.get("city") or "?"
        temp = data.get("temperature_c") or data.get("temperature") or "?"
        cond = data.get("condition") or data.get("description") or ""
        return f"{loc}: {temp}°C {_truncate(cond, 40)}".strip()

    if tool_name == "workspace_create_file":
        path = data.get("path") or "?"
        size = data.get("bytes_written") or data.get("size") or 0
        return f"wrote {path} ({size} bytes)"

    if tool_name == "workspace_str_replace":
        path = data.get("path") or "?"
        return f"edited {path}"

    if tool_name in ("workspace_list", "workspace_view", "conv_list"):
        entries = data.get("entries") or data.get("files") or data.get("items") or []
        if isinstance(entries, list):
            return f"{len(entries)} entries"
        return "ok"

    if tool_name == "conv_read_file":
        path = data.get("path") or "?"
        content = data.get("content") or ""
        return f"read {path} ({len(content)} chars)"

    if tool_name == "conv_history_scan":
        hits = data.get("matches") or data.get("hits") or []
        return f"{len(hits)} matches" if isinstance(hits, list) else "ok"

    if tool_name in ("self_inspect_agent", "self_inspect_paradigm"):
        name = data.get("agent") or data.get("paradigm") or data.get("name") or "?"
        return f"inspected '{name}'"

    # Generic fallback: first useful field or truncated JSON.
    for key in ("summary", "message", "status", "ok"):
        if key in data:
            return f"{key}={_truncate(str(data[key]), 100)}"
    return _truncate(raw, 100)


def _format_args(arguments: dict) -> str:
    """Render call arguments as ``k=v`` pairs.

    String values are kept at full length up to 300 chars so the plan
    preserves enough signal for peer agents to recognise — and avoid —
    duplicate queries. Past that, we truncate with an ellipsis.
    """
    if not arguments:
        return ""
    parts = []
    for k, v in arguments.items():
        if isinstance(v, str):
            parts.append(f'{k}="{_truncate(v, 300)}"')
        elif isinstance(v, (int, float, bool)) or v is None:
            parts.append(f"{k}={v}")
        elif isinstance(v, (list, tuple)):
            parts.append(f"{k}=[{len(v)} items]")
        elif isinstance(v, dict):
            parts.append(f"{k}={{{len(v)} keys}}")
        else:
            parts.append(f"{k}=…")
    return ", ".join(parts)


def log_action(
    steps: list[dict],
    step_id: str | None,
    agent: str,
    tool_name: str,
    arguments: dict,
    result: str,
) -> None:
    """Append a tool call to the matching step's action log.

    No-op if ``step_id`` is None (root-level router calls), if the tool is
    not in ``_LOGGED_TOOLS``, or if the step is unknown. Caps the action
    list at ``_MAX_ACTIONS_PER_STEP`` to bound plan size.
    """
    if step_id is None or tool_name not in _LOGGED_TOOLS:
        return
    step = next((s for s in steps if s.get("id") == step_id), None)
    if step is None:
        return
    actions = step.setdefault("actions", [])
    if len(actions) >= _MAX_ACTIONS_PER_STEP:
        return
    actions.append({
        "ts": time.strftime("%H:%M:%S"),
        "agent": agent,
        "tool": tool_name,
        "args": _format_args(arguments or {}),
        "summary": _summarize_result(tool_name, result),
    })


def _render_step(step: dict) -> str:
    sid = step.get("id", "?")
    agent = step.get("agent", "?")
    status = step.get("status", "in_progress")
    icon = _STATUS_ICON.get(status, "•")
    briefing = _truncate(step.get("briefing", ""), _MAX_BRIEFING_CHARS)
    summary = _truncate(step.get("summary", ""), _MAX_SUMMARY_CHARS)
    files = step.get("files_produced") or []
    actions = step.get("actions") or []

    out = [f"## {sid} {icon} {agent} — {status}"]
    if briefing:
        out.append(f"**Task:** {briefing}")
    if summary:
        out.append(f"**Summary:** {summary}")
    if files:
        out.append("**Files produced:** " + ", ".join(files))
    if actions:
        out.append("**Actions:**")
        for a in actions:
            call = f"`{a.get('tool', '?')}({a.get('args', '')})`"
            out.append(
                f"- `{a.get('ts', '')}` {a.get('agent', '?')} → {call} → "
                f"{a.get('summary', '')}"
            )
    return "\n".join(out)


def write(conv_folder: Path, steps: list[dict]) -> None:
    """Serialise the full plan to ``plan.md``."""
    path = plan_path(conv_folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not steps:
        path.write_text("# Plan\n\n_(no delegated steps yet)_\n", encoding="utf-8")
        return
    body = "\n\n".join(_render_step(s) for s in steps)
    path.write_text(f"# Plan\n\n{body}\n", encoding="utf-8")
