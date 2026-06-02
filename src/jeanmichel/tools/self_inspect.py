"""Tool: self_inspect — query Jean-Michel's own internal state.

Returns a structured snapshot of the running system: agents, their tool grants,
paradigm counts, sandbox configuration, and recent activity statistics.

This is the foundation for meta-cognition: any agent with this tool can
observe how the system is currently configured and reason about improvements.

Stateless tool: connects to DB_PATH at call time.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .. import config
from ._base import ToolSpec
from ._errors import tool_error, tool_ok


def _handler(scope: str = "full") -> str:
    """Return a JSON snapshot of the system state.

    Args:
        scope: What to return.
            "agents"           — agents + their tools + paradigm counts + sandbox config.
            "paradigms"        — all active paradigms grouped by section/category.
            "conversations"    — recent activity stats derived from event logs.
            "sandbox"          — sandbox execution audit (last 50 rows).
            "recent_summaries" — content of the last N conversation summary.md files.
            "full"             — agents + conversations (default).
    """
    valid_scopes = ("agents", "paradigms", "conversations", "sandbox", "recent_summaries", "architecture", "full")
    if scope not in valid_scopes:
        return tool_error("invalid_scope", f"Invalid scope '{scope}'. Valid: {valid_scopes}")

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

        if scope == "architecture":
            result["architecture"] = _architecture_snapshot()

        conn.close()

        # Build a short summary of what scope returned.
        bits: list[str] = []
        if "agents" in result:
            bits.append(f"{len(result['agents'])} agents")
        if "paradigms" in result:
            bits.append(f"{len(result['paradigms'])} paradigms")
        if "activity" in result:
            bits.append("activity")
        if "sandbox_executions" in result:
            bits.append(f"{len(result['sandbox_executions'])} sandbox rows")
        if "recent_summaries" in result:
            bits.append(f"{len(result['recent_summaries'])} summaries")
        if "architecture" in result:
            bits.append("architecture")
        summary = f"scope={scope}: " + (", ".join(bits) if bits else "empty")

        return tool_ok(summary, **result)

    except Exception as e:
        return tool_error("self_inspect_failed", str(e))


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


def _parse_event_utc(value: str | None) -> datetime | None:
    """Parse an event `utc` field (events.py format) into an aware datetime."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _events_activity() -> dict:
    """Aggregate runtime activity from events.jsonl across all conversations.

    Runtime orchestration state moved out of SQL in migration 102 (the
    `requests`/`artifacts` tables were dropped). The equivalent stats —
    request volume, delegation health, busiest agents — are now derived from
    the per-conversation event logs. `RequestStarted` is emitted for each
    human turn AND each delegation, so it is the v2 analogue of an old
    `requests` row.
    """
    from ..persistence import load_events

    now = datetime.now(UTC)
    since_7d = now - timedelta(days=7)
    since_30d = now - timedelta(days=30)

    requests_total = requests_7d = 0
    delegations_total = low_confidence = 0
    hook_denials = 0
    agent_counts_30d: dict[str, int] = {}

    if config.CONVERSATIONS_DIR.exists():
        for folder in config.CONVERSATIONS_DIR.iterdir():
            if not folder.is_dir():
                continue
            for ev in load_events(folder):
                etype = ev.get("type")
                ts = _parse_event_utc(ev.get("utc"))
                if etype == "RequestStarted":
                    requests_total += 1
                    if ts and ts >= since_7d:
                        requests_7d += 1
                    if ts and ts >= since_30d:
                        agent = ev.get("agent") or "?"
                        agent_counts_30d[agent] = agent_counts_30d.get(agent, 0) + 1
                elif etype == "DelegationCompleted":
                    delegations_total += 1
                    if ev.get("confidence") == "low":
                        low_confidence += 1
                elif etype == "HookFired" and "deny" in (ev.get("action") or "").lower():
                    hook_denials += 1

    top_agents = sorted(agent_counts_30d.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return {
        "requests": {
            "total": requests_total,
            "last_7_days": requests_7d,
        },
        "delegations": {
            "total": delegations_total,
            "low_confidence": low_confidence,
        },
        "hook_denials_total": hook_denials,
        "top_agents_30d": [{"agent": a, "requests": n} for a, n in top_agents],
    }


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

    return {
        "conversations": {
            "total": total_convs,
            "active": active_convs,
            "last_7_days": convs_7d,
            "last_30_days": convs_30d,
        },
        **_events_activity(),
    }


def _sandbox_snapshot(conn: sqlite3.Connection) -> dict:
    """Read from the cross-conversation sandbox audit JSONL.

    `conn` is kept in the signature for symmetry with the other snapshot
    helpers but is not used here — audit data has moved out of SQL
    (migration 102) into ``~/.jean-michel/sandbox_audit.jsonl``.
    """
    from ..persistence import load_sandbox_audit

    entries = load_sandbox_audit()
    total = len(entries)
    refused = sum(1 for e in entries if e.get("exit_code") is None)
    completed = [e for e in entries if e.get("exit_code") is not None]
    avg_duration = (
        sum(e.get("duration_ms", 0) for e in completed) / len(completed)
        if completed else None
    )
    recent = entries[-50:]

    return {
        "summary": {
            "total_executions": total,
            "refused": refused,
            "avg_duration_ms": round(avg_duration, 1) if avg_duration else None,
        },
        "recent": recent,
    }


def _architecture_snapshot() -> dict:
    """Return README.md and db/schema.sql to give agents awareness of their own architecture."""
    root = config.REPO_ROOT
    result: dict = {}

    readme = root / "README.md"
    if readme.exists():
        result["readme"] = readme.read_text(encoding="utf-8")
    else:
        result["readme"] = None

    schema = root / "db" / "schema.sql"
    if schema.exists():
        result["schema_sql"] = schema.read_text(encoding="utf-8")
    else:
        result["schema_sql"] = None

    return result


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
        "scope='conversations': activity stats from the event logs (request volume, "
        "low-confidence delegations, hook denials, busiest agents). "
        "scope='sandbox': execution audit (last 50 rows). "
        "scope='recent_summaries': content of the last 5 conversation summary.md files — "
        "use this to observe actual conversation quality and user needs. "
        "scope='architecture': README.md + db/schema.sql — use this to understand the "
        "system's real structure, table names, and column names before proposing changes. "
        "scope='full': agents + activity (default)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["agents", "paradigms", "conversations", "sandbox", "recent_summaries", "architecture", "full"],
                "description": (
                    "What data to return: "
                    "'agents', 'paradigms', 'conversations', 'sandbox', "
                    "'recent_summaries' (summary.md of last 5 conversations), "
                    "'architecture' (README + DB schema — read this before proposing SQL changes), "
                    "or 'full' (agents + activity)."
                ),
            },
        },
        "required": [],
    },
    handler=_handler,
)
