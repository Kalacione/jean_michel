"""SQLite access layer. Thin helpers over sqlite3."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from . import config
from .models import Agent, Conversation, Paradigm


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

_PARADIGM_SELECT = """
        SELECT s.code AS section_code, c.code AS category_code, c.title AS category_title,
               p.code, p.title, p.content{requires_tool}
        FROM paradigms p
        JOIN categories c ON c.id = p.category_id
        JOIN sections   s ON s.id = c.section_id
        {join}
        WHERE p.active = 1 AND c.active = 1 AND s.active = 1
          AND ( p.is_global = 1
                OR p.id IN (SELECT paradigm_id FROM agent_paradigms WHERE agent_id = ?) )
          AND ( NOT EXISTS (SELECT 1 FROM paradigm_modes pm WHERE pm.paradigm_id = p.id)
                OR EXISTS  (SELECT 1 FROM paradigm_modes pm WHERE pm.paradigm_id = p.id AND pm.mode = ?) )
        ORDER BY s.order_priority, c.order_priority, p.order_priority, p.id
        """


def load_paradigms_for_agent(conn: sqlite3.Connection, agent_id: int, mode: str) -> list[Paradigm]:
    """Globals + paradigms explicitly bound to this agent, filtered by mode, ordered.

    Carries ``requires_tool`` (from the optional ``paradigm_requires_tool`` table) so
    the caller can gate a paradigm on the agent actually having that tool. Falls back
    to the un-gated query when the table is absent (migrate_127 not yet applied)."""
    try:
        sql = _PARADIGM_SELECT.format(
            requires_tool=", prt.tool_prefix AS requires_tool",
            join="LEFT JOIN paradigm_requires_tool prt ON prt.paradigm_id = p.id",
        )
        rows = conn.execute(sql, (agent_id, mode)).fetchall()
    except sqlite3.OperationalError:
        sql = _PARADIGM_SELECT.format(requires_tool="", join="")
        rows = conn.execute(sql, (agent_id, mode)).fetchall()
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
                        title: str | None = None,
                        project_id: int | None = None) -> Conversation:
    now = _now()
    conn.execute(
        "INSERT INTO conversations (id, title, folder_path, user_language, status, mode, "
        "project_id, created_at, modified_at) "
        "VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)",
        (conv_id, title, folder_path, user_language, mode, project_id, now, now),
    )
    return Conversation(id=conv_id, folder_path=folder_path,
                        user_language=user_language, title=title, mode=mode,
                        project_id=project_id)


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
    cols = "id, folder_path, mode, user_language, status, title, project_id, created_at, modified_at"
    row = conn.execute(
        f"SELECT {cols} FROM conversations WHERE id=?",
        (conv_id_or_prefix,),
    ).fetchone()
    if row is None:
        row = conn.execute(
            f"SELECT {cols} FROM conversations WHERE id LIKE ?",
            (conv_id_or_prefix + "%",),
        ).fetchone()
    return row


def rename_conversation(conn: sqlite3.Connection, conv_id: str, title: str) -> None:
    """Set a user-facing title. Metadata only — does NOT bump modified_at
    (renaming is not an interaction ; it must not reorder the list)."""
    conn.execute("UPDATE conversations SET title=? WHERE id=?", (title, conv_id))


def set_title_if_empty(conn: sqlite3.Connection, conv_id: str, title: str) -> None:
    """Seed a default title only when none exists yet (preserves user edits)."""
    conn.execute(
        "UPDATE conversations SET title=? WHERE id=? AND (title IS NULL OR title='')",
        (title, conv_id),
    )


def touch_conversation(conn: sqlite3.Connection, conv_id: str) -> None:
    """Bump modified_at to now — marks the last interaction (drives list order).

    Uses the same ISO format as created_at (_now) so string ordering stays
    correct despite other writers using datetime('now')'s space format.
    """
    conn.execute("UPDATE conversations SET modified_at=? WHERE id=?", (_now(), conv_id))


def delete_conversation(conn: sqlite3.Connection, conv_id: str) -> None:
    """Delete a conversation row. ON DELETE CASCADE (migrate_114) removes its
    ownership links — and any future cascade-declared child rows. Requires
    PRAGMA foreign_keys=ON (set by ``connect``)."""
    conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))


# ---- Web users + profile + conversation ownership (web frontend) ----------

# Structured profile fields (the cli_profile.toml structure, reprise en BDD ;
# migrate_113). Filled at creation for web users.
WEB_PROFILE_FIELDS = ("name", "birthdate", "city", "country", "language", "interests", "notes")


def create_web_user(
    conn: sqlite3.Connection, username: str, password_hash: str, **profile: str
) -> int:
    """Insert a web user (+ optional profile fields). Returns the new id.

    Profile kwargs (name/birthdate/city/country/language/interests/notes) default
    to '' in the schema ; only the ones provided are written. Raises
    IntegrityError on a duplicate username.
    """
    cols = ["username", "password_hash", "created_at"]
    vals: list[str] = [username, password_hash, _now()]
    for field in WEB_PROFILE_FIELDS:
        if field in profile:
            cols.append(field)
            vals.append(profile[field])
    placeholders = ", ".join("?" for _ in cols)
    cur = conn.execute(
        f"INSERT INTO web_users ({', '.join(cols)}) VALUES ({placeholders})", vals
    )
    return cur.lastrowid  # type: ignore[return-value]


def get_web_user_by_username(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM web_users WHERE username=?", (username,)).fetchone()


def get_web_user_by_id(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM web_users WHERE id=?", (user_id,)).fetchone()


def update_web_user_profile(conn: sqlite3.Connection, user_id: int, **fields: str) -> None:
    """Update the profile columns of a web user. Only known fields are written."""
    sets: list[str] = []
    vals: list[str] = []
    for field in WEB_PROFILE_FIELDS:
        if field in fields and fields[field] is not None:
            sets.append(f"{field}=?")
            vals.append(fields[field])
    if not sets:
        return
    vals.append(user_id)  # type: ignore[arg-type]
    conn.execute(f"UPDATE web_users SET {', '.join(sets)} WHERE id=?", vals)


def cli_user_id(conn: sqlite3.Connection) -> int:
    """id of the reserved `cli` user — the CLI's identity + the default memory scope.

    Raises KeyError if migrate_113 hasn't been applied (no `cli` user yet).
    """
    row = conn.execute("SELECT id FROM web_users WHERE username='cli'").fetchone()
    if row is None:
        raise KeyError("reserved 'cli' user missing (migrate_113 not applied)")
    return row["id"]


def associate_conversation_user(
    conn: sqlite3.Connection, user_id: int, conversation_id: str
) -> None:
    """Link a conversation to its owner. Idempotent. The CLI never calls this."""
    conn.execute(
        "INSERT OR IGNORE INTO conversation_users (user_id, conversation_id, created_at) "
        "VALUES (?, ?, ?)",
        (user_id, conversation_id, _now()),
    )


def list_conversations_for_user(
    conn: sqlite3.Connection, user_id: int, limit: int = 50
) -> list[sqlite3.Row]:
    """Conversations owned by ``user_id`` (all statuses), newest first.

    Web-scoped : only conversations associated to this user are returned, so
    Alice never sees Bob's — and CLI conversations (no association) are absent.
    """
    return conn.execute(
        "SELECT c.id, c.title, c.mode, c.status, c.user_language, c.created_at, c.modified_at "
        "FROM conversations c "
        "JOIN conversation_users cu ON cu.conversation_id = c.id "
        "WHERE cu.user_id = ? "
        "ORDER BY c.modified_at DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()


def user_owns_conversation(
    conn: sqlite3.Connection, user_id: int, conversation_id: str
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM conversation_users WHERE user_id=? AND conversation_id=?",
        (user_id, conversation_id),
    ).fetchone()
    return row is not None


# ---- Projects (migrate_124) -----------------------------------------------

_PROJECT_COLS = "id, user_id, code, name, description, status, created_at, modified_at"


def create_project(
    conn: sqlite3.Connection, *, user_id: int, code: str, name: str, description: str = ""
) -> int:
    """Insert a project owned by ``user_id``. Returns the new id.

    Raises IntegrityError on a duplicate (user_id, code)."""
    now = _now()
    cur = conn.execute(
        "INSERT INTO projects (user_id, code, name, description, status, created_at, modified_at) "
        "VALUES (?, ?, ?, ?, 'active', ?, ?)",
        (user_id, code, name, description, now, now),
    )
    return cur.lastrowid  # type: ignore[return-value]


def list_projects_for_user(
    conn: sqlite3.Connection, user_id: int, *, include_archived: bool = True
) -> list[sqlite3.Row]:
    """Projects owned by ``user_id``, newest first. Active-only when asked."""
    sql = f"SELECT {_PROJECT_COLS} FROM projects WHERE user_id=?"
    if not include_archived:
        sql += " AND status='active'"
    sql += " ORDER BY modified_at DESC"
    return conn.execute(sql, (user_id,)).fetchall()


def get_project(conn: sqlite3.Connection, project_id: int) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT {_PROJECT_COLS} FROM projects WHERE id=?", (project_id,)
    ).fetchone()


def get_project_by_code(
    conn: sqlite3.Connection, user_id: int, code: str
) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT {_PROJECT_COLS} FROM projects WHERE user_id=? AND code=?", (user_id, code)
    ).fetchone()


def update_project(
    conn: sqlite3.Connection, project_id: int, *,
    name: str | None = None, description: str | None = None, status: str | None = None,
) -> None:
    """Update a project's mutable fields. Only provided fields are written."""
    sets: list[str] = []
    vals: list[object] = []
    for col, val in (("name", name), ("description", description), ("status", status)):
        if val is not None:
            sets.append(f"{col}=?")
            vals.append(val)
    if not sets:
        return
    sets.append("modified_at=?")
    vals.append(_now())
    vals.append(project_id)
    conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id=?", vals)


def delete_project(conn: sqlite3.Connection, project_id: int) -> None:
    """Delete a project. Memory scope='project' cascades ; conversations.project_id
    is set NULL (migrate_124). Requires PRAGMA foreign_keys=ON (set by ``connect``)."""
    conn.execute("DELETE FROM projects WHERE id=?", (project_id,))


def set_conversation_project(
    conn: sqlite3.Connection, conv_id: str, project_id: int | None
) -> None:
    """Attach (or detach, with None) a conversation to a project."""
    conn.execute(
        "UPDATE conversations SET project_id=? WHERE id=?", (project_id, conv_id)
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
