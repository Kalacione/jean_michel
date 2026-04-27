"""SQLite access layer. Thin helpers over sqlite3."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

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
        "SELECT id, code, name, role, mission, thinking_mode, temperature "
        "FROM agents WHERE active = 1 ORDER BY id",
    ).fetchall()
    return [
        Agent(id=r["id"], code=r["code"], name=r["name"], role=r["role"],
              mission=r["mission"], thinking_mode=bool(r["thinking_mode"]),
              temperature=r["temperature"])
        for r in rows
    ]


def get_agent_by_code(conn: sqlite3.Connection, code: str) -> Agent:
    row = conn.execute(
        "SELECT id, code, name, role, mission, thinking_mode, temperature "
        "FROM agents WHERE code = ? AND active = 1",
        (code,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Unknown agent: {code}")
    return Agent(
        id=row["id"], code=row["code"], name=row["name"], role=row["role"],
        mission=row["mission"], thinking_mode=bool(row["thinking_mode"]),
        temperature=row["temperature"],
    )


# ---- Paradigms ------------------------------------------------------------

def load_paradigms_for_agent(conn: sqlite3.Connection, agent_id: int) -> list[Paradigm]:
    """Globals + paradigms explicitly bound to this agent, ordered."""
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
        ORDER BY s.order_priority, c.order_priority, p.order_priority, p.id
        """,
        (agent_id,),
    ).fetchall()
    return [Paradigm(**dict(r)) for r in rows]


# ---- Conversations --------------------------------------------------------

def create_conversation(conn: sqlite3.Connection, conv_id: str, folder_path: str,
                        user_language: str | None, title: str | None = None) -> Conversation:
    now = _now()
    conn.execute(
        "INSERT INTO conversations (id, title, folder_path, user_language, status, created_at, modified_at) "
        "VALUES (?, ?, ?, ?, 'active', ?, ?)",
        (conv_id, title, folder_path, user_language, now, now),
    )
    return Conversation(id=conv_id, folder_path=folder_path,
                        user_language=user_language, title=title)


# ---- Requests -------------------------------------------------------------

def create_request(conn: sqlite3.Connection, *, req_id: str, conv_id: str,
                   parent_id: str | None, depth: int, agent_id: int,
                   inbound_briefing: str | None, expected_outcome: str | None,
                   dispatch_group_id: str | None = None) -> Request:
    now = _now()
    conn.execute(
        "INSERT INTO requests (id, conversation_id, parent_request_id, dispatch_group_id, "
        "depth, agent_id, inbound_briefing, expected_outcome, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
        (req_id, conv_id, parent_id, dispatch_group_id, depth, agent_id,
         inbound_briefing, expected_outcome, now),
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
