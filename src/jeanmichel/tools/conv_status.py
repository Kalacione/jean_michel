"""Tool: conv_status — live metacognitive dashboard for the current conversation.

Returns real-time metrics from the DB for the ongoing conversation:
- Delegation tree (depth, agents, status)
- Tool call counts per agent (detect over-use)
- Repeated calls (detect loops)
- Budget signals (who is over the soft limits)

Designed to be called by jean-michel (router) when it suspects a loop or needs
to decide whether to force synthesis on a running specialist.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

from .. import config
from ._base import ToolSpec

# Soft limits — above these thresholds a budget_signal is emitted.
_TOOL_CALL_SOFT_LIMIT = 5    # per agent per conversation
_DEPTH_SOFT_LIMIT = 3         # delegation depth
_TOTAL_CALLS_SOFT_LIMIT = 20  # conversation-wide


def _handler(conversation_id: str) -> str:
    db_path = config.DB_PATH

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        conv_row = conn.execute(
            "SELECT folder_path FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()

        requests = conn.execute(
            """
            SELECT r.id, r.depth, r.status, r.turn_index,
                   a.name AS agent_name
            FROM requests r
            JOIN agents a ON r.agent_id = a.id
            WHERE r.conversation_id = ?
            ORDER BY r.created_at
            """,
            (conversation_id,),
        ).fetchall()

        if not requests:
            return json.dumps(
                {"error": f"No requests found for conversation {conversation_id!r}"}
            )

        tool_call_artifacts = conn.execute(
            """
            SELECT a.relative_path, ag.name AS agent_name
            FROM artifacts a
            JOIN requests r ON a.request_id = r.id
            JOIN agents ag ON r.agent_id = ag.id
            WHERE r.conversation_id = ? AND a.kind = 'tool_call'
            ORDER BY a.created_at
            """,
            (conversation_id,),
        ).fetchall()

    # ── Parse tool names from artifact files ─────────────────────────────────
    conv_folder = Path(conv_row["folder_path"]) if conv_row else None

    tool_calls_parsed: list[dict] = []
    for row in tool_call_artifacts:
        tool_name: str | None = None
        if conv_folder:
            tc_path = conv_folder / row["relative_path"]
            if tc_path.exists():
                try:
                    for line in tc_path.read_text(encoding="utf-8").splitlines():
                        stripped = line.strip()
                        if stripped.startswith("tool:"):
                            tool_name = stripped.split(":", 1)[1].strip().strip("\"'")
                            break
                except OSError:
                    pass
        tool_calls_parsed.append({"agent": row["agent_name"], "tool": tool_name})

    # ── Metrics ───────────────────────────────────────────────────────────────
    req_list = [dict(r) for r in requests]

    depth_max = max((r["depth"] for r in req_list), default=0)
    depth_current = max(
        (r["depth"] for r in req_list if r["status"] == "running"),
        default=0,
    )

    delegations_by_agent: Counter = Counter(r["agent_name"] for r in req_list)

    active = [
        {
            "request_id": r["id"][:8],
            "agent": r["agent_name"],
            "depth": r["depth"],
            "status": r["status"],
        }
        for r in req_list
        if r["status"] in ("running", "pending")
    ]

    tc_by_agent: Counter = Counter(tc["agent"] for tc in tool_calls_parsed)
    total_tool_calls = len(tool_calls_parsed)

    fingerprints: Counter = Counter(
        (tc["agent"], tc["tool"]) for tc in tool_calls_parsed if tc["tool"]
    )
    repeated_calls = [
        {"agent": agent, "tool": tool, "count": count}
        for (agent, tool), count in fingerprints.items()
        if count > 1
    ]

    # ── Budget signals ────────────────────────────────────────────────────────
    budget_signals: list[str] = []
    for agent, count in tc_by_agent.items():
        if count >= _TOOL_CALL_SOFT_LIMIT:
            budget_signals.append(
                f"WARNING: {agent} has {count} tool calls (soft limit {_TOOL_CALL_SOFT_LIMIT})"
            )
    if depth_max >= _DEPTH_SOFT_LIMIT:
        budget_signals.append(
            f"WARNING: delegation depth reached {depth_max} (soft limit {_DEPTH_SOFT_LIMIT})"
        )
    if total_tool_calls >= _TOTAL_CALLS_SOFT_LIMIT:
        budget_signals.append(
            f"WARNING: {total_tool_calls} total tool calls "
            f"(soft limit {_TOTAL_CALLS_SOFT_LIMIT})"
        )
    for rc in repeated_calls:
        budget_signals.append(
            f"LOOP RISK: {rc['agent']} called {rc['tool']!r} {rc['count']}x — "
            "possible loop, consider forcing synthesis"
        )

    return json.dumps(
        {
            "conversation_id": conversation_id[:12],
            "depth_max": depth_max,
            "depth_current": depth_current,
            "active_requests": active,
            "delegations_by_agent": dict(delegations_by_agent),
            "tool_calls_by_agent": dict(tc_by_agent),
            "total_tool_calls": total_tool_calls,
            "repeated_calls": repeated_calls,
            "budget_signals": budget_signals,
        },
        ensure_ascii=False,
        indent=2,
    )


def make_spec(conversation_id: str) -> ToolSpec:
    """Return a ToolSpec bound to `conversation_id`."""
    return ToolSpec(
        name="conv_status",
        description=(
            "Live metacognitive dashboard for the current conversation. "
            "Returns: delegation depth, active agents, tool call counts per agent, "
            "repeated calls (loop detection), and budget signals. "
            "Call this when you suspect a specialist is looping, before launching "
            "a new delegation if total_tool_calls > 15, or when a delegation has "
            "been running longer than expected. "
            "Use budget_signals to decide: force synthesis, add a targeted sub-delegation, "
            "or cancel a runaway agent."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=lambda: _handler(conversation_id),
    )
