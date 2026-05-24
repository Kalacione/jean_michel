"""Deterministic plan.md writer — invoked by the orchestrator only.

The LLM never calls these functions. They are side-effects of orchestrator
control flow:
  - delegate_to(agent, briefing)  → add_step()   (status: in_progress)
  - child converges               → complete_step()
  - child fails                   → fail_step()

Plan.md lives at workspace/plan.md and is rendered as a simple markdown table
readable by LLMs via workspace_view.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

_PLAN_FILE = Path("plan.md")  # directly in conv_folder (not workspace — no quota impact)

_ICONS: dict[str, str] = {
    "in_progress": "🔄",
    "done":        "✅",
    "blocked":     "🚫",
}


def plan_path(conv_folder: Path) -> Path:
    return conv_folder / _PLAN_FILE


def write(conv_folder: Path, steps: list[dict]) -> None:
    """Render and write plan.md from the current in-memory step list.

    Each step dict must have: id, agent, briefing, status.
    Optional: summary (set when done).
    """
    p = plan_path(conv_folder)
    p.write_text(_render(steps), encoding="utf-8")


def _render(steps: list[dict]) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "# Research Plan",
        "",
        f"*Updated: {now}*",
        "",
        "| # | Agent | Task | Status | Summary |",
        "|---|-------|------|--------|---------|",
    ]
    for s in steps:
        icon = _ICONS.get(s["status"], "❓")
        briefing = s.get("briefing", "")[:80].replace("|", "∣")
        summary = (s.get("summary") or "").replace("|", "∣")
        lines.append(
            f"| {s['id']} | {s['agent']} | {briefing} "
            f"| {icon} {s['status']} | {summary} |"
        )
    return "\n".join(lines) + "\n"
