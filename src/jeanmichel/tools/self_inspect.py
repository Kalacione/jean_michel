"""Tool: self_inspect — query Jean-Michel's own internal state.

Returns a structured snapshot of the running system: agents, their tool grants,
paradigm counts, sandbox configuration, and recent activity statistics.

This is the foundation for meta-cognition: any agent with this tool can
observe how the system is currently configured and reason about improvements.

Stateless tool: connects to DB_PATH at call time.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .. import config
from ._base import ToolSpec


def _handler(scope: str = "full") -> str:
    """Return a JSON snapshot of the system state.

    Args:
        scope: What to return.
            "agents"           — agents + their tools + paradigm counts + sandbox config.
            "paradigms"        — all active paradigms grouped by section/category.
            "conversations"    — recent activity stats including failure counts.
            "sandbox"          — sandbox execution audit (last 50 rows).
            "recent_summaries" — content of the last N conversation summary.md files.
            "full"             — agents + conversations (default).
    """
    valid_scopes = ("agents", "paradigms", "conversations", "sandbox", "recent_summaries", "full")
    if scope not in valid_scopes:
        return json.dumps({"error": f"Invalid scope '{scope}'. Valid: {valid_scopes}"})

    try:
        conn = sqlite3.connect(config.DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        result: dict = {}

        if scope in ("agents", "full"):
            result["agents"] = _agents_snapshot(conn)

        if scope == "paradigms":
            result["paradigms"] = _paradigms_snapshot(conn)

        if scope in ("conversations", "full"):
            result["activity"] = _activity_snapshot(conn)

        if scope == "sandbox":
            result["sandbox_executions"] = _sandbox_snapshot(conn)

        if scope == "recent_summaries":
            result["recent_summaries"] = _recent_summaries_snapshot(conn)

        conn.close()
        return json.dumps(result, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


def _agents_snapshot(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, code, name, role, mission, thinking_mode, temperature, "
        "       sandbox_image, active "
        "FROM agents ORDER BY id"
    ).fetchall()

    agents = []
    for r in rows:
        agent_id = r["id"]

        tools = [
            t["tool_code"]
            for t in conn.execute(
                "SELECT tool_code FROM agent_tools WHERE agent_id=? ORDER BY tool_code",
                (agent_id,),
            ).fetchall()
        ]

        paradigm_count = conn.execute(
            """SELECT COUNT(*) AS n FROM (
                SELECT p.id FROM paradigms p
                WHERE p.active=1
                  AND (p.is_global=1
                       OR p.id IN (SELECT paradigm_id FROM agent_paradigms WHERE agent_id=?))
            )""",
            (agent_id,),
        ).fetchone()["n"]

        has_workspace_write = conn.execute(
            "SELECT 1 FROM agent_workspace_grants WHERE agent_id=?",
            (agent_id,),
        ).fetchone() is not None

        sandbox_grants = [
            g["command"]
            for g in conn.execute(
                "SELECT command FROM agent_sandbox_grants WHERE agent_id=? ORDER BY command",
                (agent_id,),
            ).fetchall()
        ]

        agents.append({
            "id": agent_id,
            "code": r["code"],
            "name": r["name"],
            "role": r["role"],
            "active": bool(r["active"]),
            "thinking_mode": bool(r["thinking_mode"]),
            "temperature": r["temperature"],
            "mission_excerpt": (r["mission"] or "")[:120],
            "tools": tools,
            "paradigm_count": paradigm_count,
            "workspace_write": has_workspace_write,
            "sandbox_grants": sandbox_grants,
            "sandbox_image": r["sandbox_image"],
        })

    return agents


def _paradigms_snapshot(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT s.code AS section, c.code AS category, c.title AS category_title,
                  p.code, p.title, p.is_global, p.active,
                  (SELECT COUNT(*) FROM agent_paradigms ap WHERE ap.paradigm_id=p.id) AS agent_bindings
           FROM paradigms p
           JOIN categories c ON c.id=p.category_id
           JOIN sections   s ON s.id=c.section_id
           WHERE p.active=1 AND c.active=1 AND s.active=1
           ORDER BY s.order_priority, c.order_priority, p.order_priority"""
    ).fetchall()
    return [dict(r) for r in rows]


def _activity_snapshot(conn: sqlite3.Connection) -> dict:
    total_convs = conn.execute("SELECT COUNT(*) AS n FROM conversations").fetchone()["n"]
    active_convs = conn.execute(
        "SELECT COUNT(*) AS n FROM conversations WHERE status='active'"
    ).fetchone()["n"]
    convs_7d = conn.execute(
        "SELECT COUNT(*) AS n FROM conversations "
        "WHERE created_at >= datetime('now', '-7 days')"
    ).fetchone()["n"]
    convs_30d = conn.execute(
        "SELECT COUNT(*) AS n FROM conversations "
        "WHERE created_at >= datetime('now', '-30 days')"
    ).fetchone()["n"]

    total_requests = conn.execute("SELECT COUNT(*) AS n FROM requests").fetchone()["n"]
    requests_7d = conn.execute(
        "SELECT COUNT(*) AS n FROM requests "
        "WHERE created_at >= datetime('now', '-7 days')"
    ).fetchone()["n"]

    failed_total = conn.execute(
        "SELECT COUNT(*) AS n FROM requests WHERE status='failed'"
    ).fetchone()["n"]
    failed_7d = conn.execute(
        "SELECT COUNT(*) AS n FROM requests "
        "WHERE status='failed' AND created_at >= datetime('now', '-7 days')"
    ).fetchone()["n"]

    ask_human_total = conn.execute(
        "SELECT COUNT(*) AS n FROM artifacts WHERE kind='ask_human'"
    ).fetchone()["n"]
    ask_human_7d = conn.execute(
        "SELECT COUNT(*) AS n FROM artifacts "
        "WHERE kind='ask_human' AND created_at >= datetime('now', '-7 days')"
    ).fetchone()["n"]

    # Most active agents in last 30 days
    top_agents = conn.execute(
        """SELECT a.code, COUNT(r.id) AS request_count
           FROM requests r JOIN agents a ON a.id=r.agent_id
           WHERE r.created_at >= datetime('now', '-30 days')
           GROUP BY a.code ORDER BY request_count DESC LIMIT 5"""
    ).fetchall()

    return {
        "conversations": {
            "total": total_convs,
            "active": active_convs,
            "last_7_days": convs_7d,
            "last_30_days": convs_30d,
        },
        "requests": {
            "total": total_requests,
            "last_7_days": requests_7d,
            "failed_total": failed_total,
            "failed_7d": failed_7d,
        },
        "ask_human": {
            "total": ask_human_total,
            "last_7_days": ask_human_7d,
        },
        "top_agents_30d": [
            {"agent": r["code"], "requests": r["request_count"]} for r in top_agents
        ],
    }


def _sandbox_snapshot(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """SELECT se.command, se.exit_code, se.duration_ms, se.created_at,
                  a.code AS agent_code
           FROM sandbox_executions se
           JOIN requests r ON r.id=se.request_id
           JOIN agents   a ON a.id=r.agent_id
           ORDER BY se.id DESC LIMIT 50"""
    ).fetchall()

    total = conn.execute("SELECT COUNT(*) AS n FROM sandbox_executions").fetchone()["n"]
    refused = conn.execute(
        "SELECT COUNT(*) AS n FROM sandbox_executions WHERE exit_code IS NULL"
    ).fetchone()["n"]
    avg_duration = conn.execute(
        "SELECT AVG(duration_ms) AS avg FROM sandbox_executions WHERE exit_code IS NOT NULL"
    ).fetchone()["avg"]

    return {
        "summary": {
            "total_executions": total,
            "refused": refused,
            "avg_duration_ms": round(avg_duration, 1) if avg_duration else None,
        },
        "recent": [dict(r) for r in rows],
    }


def _recent_summaries_snapshot(conn: sqlite3.Connection, limit: int = 5) -> list[dict]:
    """Return the content of summary.md for the N most recent conversations."""
    rows = conn.execute(
        "SELECT id, folder_path, title, mode, created_at, status "
        "FROM conversations ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()

    results = []
    for r in rows:
        folder = Path(r["folder_path"])
        summary_path = folder / "summary.md"
        journal_path = folder / "conversation.md"

        content_text: str | None = None
        content_source: str | None = None
        if summary_path.exists():
            content_text = summary_path.read_text(encoding="utf-8")[:2000]
            content_source = "summary.md"
        elif journal_path.exists():
            content_text = journal_path.read_text(encoding="utf-8")[:1000]
            content_source = "conversation.md"

        results.append({
            "conversation_id": r["id"][:12],
            "folder": folder.name,
            "created_at": r["created_at"],
            "mode": r["mode"],
            "status": r["status"],
            "title": r["title"],
            "content_source": content_source,
            "content": content_text,
        })

    return results


SPEC = ToolSpec(
    name="self_inspect",
    description=(
        "Query Jean-Michel's own internal configuration and activity. "
        "Returns a structured JSON snapshot. "
        "scope='agents': agent config (tools, paradigm counts, sandbox grants). "
        "scope='paradigms': full paradigm list. "
        "scope='conversations': activity stats including failure counts and ask_human frequency. "
        "scope='sandbox': execution audit (last 50 rows). "
        "scope='recent_summaries': content of the last 5 conversation summary.md files — "
        "use this to observe actual conversation quality and user needs. "
        "scope='full': agents + activity (default)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["agents", "paradigms", "conversations", "sandbox", "recent_summaries", "full"],
                "description": (
                    "What data to return: "
                    "'agents', 'paradigms', 'conversations', 'sandbox', "
                    "'recent_summaries' (summary.md of last 5 conversations), "
                    "or 'full' (agents + activity)."
                ),
            },
        },
        "required": [],
    },
    handler=_handler,
)
