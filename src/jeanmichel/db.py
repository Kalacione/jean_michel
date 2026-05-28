"""SQLite access layer. Thin helpers over sqlite3."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from . import config
from .models import Agent, Conversation, Paradigm, Request


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---- Agents ---------------------------------------------------------------

def list_active_agents(conn: sqlite3.Connection) -> list[Agent]:
    rows = conn.execute(
        "SELECT id, code, name, role, mission, thinking_mode, temperature, sandbox_image "
        "FROM agents WHERE active = 1 ORDER BY id",
    ).fetchall()
    return [
        Agent(id=r["id"], code=r["code"], name=r["name"], role=r["role"],
              mission=r["mission"], thinking_mode=bool(r["thinking_mode"]),
              temperature=r["temperature"], sandbox_image=r["sandbox_image"])
        for r in rows
    ]


def get_agent_by_code(conn: sqlite3.Connection, code: str) -> Agent:
    row = conn.execute(
        "SELECT id, code, name, role, mission, thinking_mode, temperature, sandbox_image "
        "FROM agents WHERE code = ? AND active = 1",
        (code,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Unknown agent: {code}")
    return Agent(
        id=row["id"], code=row["code"], name=row["name"], role=row["role"],
        mission=row["mission"], thinking_mode=bool(row["thinking_mode"]),
        temperature=row["temperature"], sandbox_image=row["sandbox_image"],
    )


# ---- Paradigms ------------------------------------------------------------

def load_paradigms_for_agent(conn: sqlite3.Connection, agent_id: int, mode: str) -> list[Paradigm]:
    """Globals + paradigms explicitly bound to this agent, filtered by mode, ordered."""
    rows = conn.execute(
        """
        SELECT s.code AS section_code, c.code AS category_code, c.title AS category_title,
               p.code, p.title, p.content
        FROM paradigms p
        JOIN categories c ON c.id = p.category_id
        JOIN sections   s ON s.id = c.section_id
        WHERE p.active = 1 AND c.active = 1 AND s.active = 1
          AND ( p.is_global = 1
                OR p.id IN (SELECT paradigm_id FROM agent_paradigms WHERE agent_id = ?) )
          AND ( NOT EXISTS (SELECT 1 FROM paradigm_modes pm WHERE pm.paradigm_id = p.id)
                OR EXISTS  (SELECT 1 FROM paradigm_modes pm WHERE pm.paradigm_id = p.id AND pm.mode = ?) )
        ORDER BY s.order_priority, c.order_priority, p.order_priority, p.id
        """,
        (agent_id, mode),
    ).fetchall()
    return [Paradigm(**dict(r)) for r in rows]


# ---- Tool grants ----------------------------------------------------------

def load_tool_grants(conn: sqlite3.Connection, agent_id: int) -> list[str]:
    """Return the list of tool_code strings granted to this agent."""
    rows = conn.execute(
        "SELECT tool_code FROM agent_tools WHERE agent_id = ? ORDER BY tool_code",
        (agent_id,),
    ).fetchall()
    return [r[0] for r in rows]


def has_workspace_grant(conn: sqlite3.Connection, agent_id: int) -> bool:
    """Return True if the agent has write access to the workspace."""
    row = conn.execute(
        "SELECT 1 FROM agent_workspace_grants WHERE agent_id = ?",
        (agent_id,),
    ).fetchone()
    return row is not None


def load_sandbox_grants(conn: sqlite3.Connection, agent_id: int) -> list[str]:
    """Return the list of sandbox commands granted to this agent."""
    rows = conn.execute(
        "SELECT command FROM agent_sandbox_grants WHERE agent_id = ? ORDER BY command",
        (agent_id,),
    ).fetchall()
    return [r[0] for r in rows]


def load_delegation_targets(conn: sqlite3.Connection, agent_id: int) -> set[str]:
    """Return the set of agent codes this agent is allowed to delegate to."""
    rows = conn.execute(
        "SELECT target_code FROM agent_delegation_targets WHERE agent_id = ?",
        (agent_id,),
    ).fetchall()
    return {r[0] for r in rows}


# `record_sandbox_execution` was removed when migration 102 dropped the
# `sandbox_executions` table. Audit now goes to
# ``~/.jean-michel/sandbox_audit.jsonl`` via ``persistence.append_sandbox_audit``.


# ---- Conversations --------------------------------------------------------

def create_conversation(conn: sqlite3.Connection, conv_id: str, folder_path: str,
                        user_language: str | None, mode: str = "analyse",
                        title: str | None = None) -> Conversation:
    now = _now()
    conn.execute(
        "INSERT INTO conversations (id, title, folder_path, user_language, status, mode, created_at, modified_at) "
        "VALUES (?, ?, ?, ?, 'active', ?, ?, ?)",
        (conv_id, title, folder_path, user_language, mode, now, now),
    )
    return Conversation(id=conv_id, folder_path=folder_path,
                        user_language=user_language, title=title, mode=mode)


def update_conversation_language(conn: sqlite3.Connection, conv_id: str, language: str) -> None:
    """Update the detected language of an existing conversation."""
    conn.execute(
        "UPDATE conversations SET user_language=?, modified_at=datetime('now') WHERE id=?",
        (language, conv_id),
    )


def close_conversation(conn: sqlite3.Connection, conv_id: str) -> None:
    """Mark a conversation as ``closed`` (idempotent, no error if already closed).

    Called by the CLI at every exit path (Ctrl-D, ``exit``/``quit``, end of
    ``--once``). ``--resume`` will refuse to re-open a closed conversation,
    which is the intended UX : starting a new turn requires a new conv.
    """
    conn.execute(
        "UPDATE conversations SET status='closed', modified_at=datetime('now') "
        "WHERE id=? AND status<>'closed'",
        (conv_id,),
    )


def list_active_conversations(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    """Return recent active or awaiting_human conversations, newest first."""
    return conn.execute(
        "SELECT id, mode, status, user_language, created_at, modified_at "
        "FROM conversations "
        "WHERE status IN ('active', 'awaiting_human') "
        "ORDER BY modified_at DESC LIMIT ?",
        (limit,),
    ).fetchall()


def get_conversation(conn: sqlite3.Connection, conv_id_or_prefix: str) -> sqlite3.Row | None:
    """Look up a conversation by exact id or by id prefix."""
    row = conn.execute(
        "SELECT id, folder_path, mode, user_language, status FROM conversations WHERE id=?",
        (conv_id_or_prefix,),
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT id, folder_path, mode, user_language, status FROM conversations WHERE id LIKE ?",
            (conv_id_or_prefix + "%",),
        ).fetchone()
    return row


# ---- Requests -------------------------------------------------------------

def create_request(conn: sqlite3.Connection, *, req_id: str, conv_id: str,
                   parent_id: str | None, depth: int, agent_id: int,
                   inbound_briefing: str | None, expected_outcome: str | None,
                   dispatch_group_id: str | None = None,
                   turn_index: int = 0) -> Request:
    now = _now()
    conn.execute(
        "INSERT INTO requests (id, conversation_id, parent_request_id, dispatch_group_id, "
        "depth, agent_id, inbound_briefing, expected_outcome, turn_index, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
        (req_id, conv_id, parent_id, dispatch_group_id, depth, agent_id,
         inbound_briefing, expected_outcome, turn_index, now),
    )
    return Request(id=req_id, conversation_id=conv_id, parent_request_id=parent_id,
                   dispatch_group_id=dispatch_group_id, depth=depth, agent_id=agent_id,
                   inbound_briefing=inbound_briefing, expected_outcome=expected_outcome,
                   status="pending")


def update_request_status(conn: sqlite3.Connection, req_id: str, status: str,
                          completed: bool = False) -> None:
    if completed:
        conn.execute(
            "UPDATE requests SET status = ?, completed_at = ? WHERE id = ?",
            (status, _now(), req_id),
        )
    else:
        conn.execute("UPDATE requests SET status = ? WHERE id = ?", (status, req_id))


# ---- Artifacts ------------------------------------------------------------

def record_artifact(conn: sqlite3.Connection, request_id: str,
                    relative_path: str, kind: str) -> None:
    conn.execute(
        "INSERT INTO artifacts (request_id, relative_path, kind, created_at) "
        "VALUES (?, ?, ?, ?)",
        (request_id, relative_path, kind, _now()),
    )


def record_phase_completion(conn: sqlite3.Connection, conversation_id: str,
                            phase: str, agent_code: str, summary: str) -> None:
    conn.execute(
        "INSERT INTO conversation_phases "
        "(conversation_id, phase, agent_code, summary, recorded_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (conversation_id, phase, agent_code, summary, _now()),
    )


def get_pipeline_state(conn: sqlite3.Connection, conv_id: str) -> tuple[str | None, str | None]:
    """Return (task_class, current_phase) for a conversation."""
    row = conn.execute(
        "SELECT task_class, current_phase FROM conversations WHERE id=?",
        (conv_id,),
    ).fetchone()
    if row is None:
        return None, None
    return row["task_class"], row["current_phase"]


def set_task_class(conn: sqlite3.Connection, conv_id: str, task_class: str) -> None:
    conn.execute(
        "UPDATE conversations SET task_class=?, modified_at=datetime('now') WHERE id=?",
        (task_class, conv_id),
    )


def update_conversation_phase(conn: sqlite3.Connection, conv_id: str, phase: str) -> None:
    conn.execute(
        "UPDATE conversations SET current_phase=?, modified_at=datetime('now') WHERE id=?",
        (phase, conv_id),
    )


# ---- Admin write helpers --------------------------------------------------

def grant_tool(conn: sqlite3.Connection, agent_code: str, tool_code: str) -> None:
    """Grant tool_code to agent identified by agent_code. No-op if already granted."""
    agent = get_agent_by_code(conn, agent_code)
    conn.execute(
        "INSERT OR IGNORE INTO agent_tools (agent_id, tool_code) VALUES (?, ?)",
        (agent.id, tool_code),
    )


def revoke_tool(conn: sqlite3.Connection, agent_code: str, tool_code: str) -> None:
    """Revoke tool_code from agent identified by agent_code."""
    agent = get_agent_by_code(conn, agent_code)
    conn.execute(
        "DELETE FROM agent_tools WHERE agent_id = ? AND tool_code = ?",
        (agent.id, tool_code),
    )


def bind_paradigm(conn: sqlite3.Connection, agent_code: str, paradigm_code: str) -> None:
    """Bind paradigm to agent. No-op if already bound."""
    agent = get_agent_by_code(conn, agent_code)
    row = conn.execute("SELECT id FROM paradigms WHERE code = ?", (paradigm_code,)).fetchone()
    if row is None:
        raise KeyError(f"Unknown paradigm: {paradigm_code}")
    conn.execute(
        "INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id) VALUES (?, ?)",
        (agent.id, row["id"]),
    )


def unbind_paradigm(conn: sqlite3.Connection, agent_code: str, paradigm_code: str) -> None:
    """Remove an explicit paradigm binding from an agent."""
    agent = get_agent_by_code(conn, agent_code)
    row = conn.execute("SELECT id FROM paradigms WHERE code = ?", (paradigm_code,)).fetchone()
    if row is None:
        raise KeyError(f"Unknown paradigm: {paradigm_code}")
    conn.execute(
        "DELETE FROM agent_paradigms WHERE agent_id = ? AND paradigm_id = ?",
        (agent.id, row["id"]),
    )


def create_paradigm(
    conn: sqlite3.Connection,
    *,
    section_code: str,
    category_code: str,
    code: str,
    title: str,
    content: str,
    rationale: str | None = None,
    is_global: bool = False,
    order_priority: int = 100,
) -> int:
    """Insert a new paradigm. Returns the new paradigm id."""
    row = conn.execute(
        "SELECT c.id FROM categories c JOIN sections s ON s.id = c.section_id "
        "WHERE s.code = ? AND c.code = ?",
        (section_code, category_code),
    ).fetchone()
    if row is None:
        raise KeyError(f"Unknown category: {section_code}.{category_code}")
    now = _now()
    cursor = conn.execute(
        "INSERT INTO paradigms (category_id, code, title, content, rationale, "
        "is_global, order_priority, active, created_at, modified_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
        (row["id"], code, title, content, rationale, int(is_global), order_priority, now, now),
    )
    return cursor.lastrowid  # type: ignore[return-value]


def set_paradigm_active(conn: sqlite3.Connection, paradigm_code: str, active: bool) -> None:
    """Enable or disable a paradigm."""
    result = conn.execute(
        "UPDATE paradigms SET active = ?, modified_at = ? WHERE code = ?",
        (int(active), _now(), paradigm_code),
    )
    if result.rowcount == 0:
        raise KeyError(f"Unknown paradigm: {paradigm_code}")
