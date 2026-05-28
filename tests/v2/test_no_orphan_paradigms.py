"""Verify no orphan FK references or dead-tool mentions after v2 migrations.

These tests run on a freshly-migrated v2 DB (schema + 100 + 101 + 102) and
verify the data is internally consistent for the v2 orchestrator.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent.parent


def _apply_full_v2_chain(conn: sqlite3.Connection) -> None:
    """Apply the v1 baseline + the three v2 migrations to obtain v2 final state.

    This validates the migration chain. To test the consolidated v2 schema
    directly, load `db/schema.sql` instead (see test_migration_idempotence).
    """
    for rel in (
        "db/schema_v1_baseline.sql",
        "db/migrations/migrate_100_paradigm_realignment.sql",
        "db/migrations/migrate_101_user_memory.sql",
        "db/migrations/migrate_102_drop_runtime_tables.sql",
    ):
        conn.executescript((_ROOT / rel).read_text(encoding="utf-8"))


@pytest.fixture()
def v2_db(tmp_path: Path):
    db_path = tmp_path / "v2_orphan_audit.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_full_v2_chain(conn)
    yield conn
    conn.close()


# ---- FK orphans ----------------------------------------------------------


def test_no_orphan_agent_paradigms(v2_db):
    """Every agent_paradigms.paradigm_id must reference an existing paradigm."""
    rows = v2_db.execute(
        "SELECT ap.paradigm_id "
        "FROM agent_paradigms ap "
        "LEFT JOIN paradigms p ON p.id = ap.paradigm_id "
        "WHERE p.id IS NULL"
    ).fetchall()
    orphan_ids = [r["paradigm_id"] for r in rows]
    assert orphan_ids == [], f"orphan paradigm_ids: {orphan_ids}"


def test_no_orphan_agent_paradigms_agent_side(v2_db):
    """Every agent_paradigms.agent_id must reference an existing agent."""
    rows = v2_db.execute(
        "SELECT ap.agent_id "
        "FROM agent_paradigms ap "
        "LEFT JOIN agents a ON a.id = ap.agent_id "
        "WHERE a.id IS NULL"
    ).fetchall()
    assert [r["agent_id"] for r in rows] == []


def test_no_orphan_agent_tools(v2_db):
    """Every agent_tools.agent_id must reference an existing agent."""
    rows = v2_db.execute(
        "SELECT at.agent_id "
        "FROM agent_tools at "
        "LEFT JOIN agents a ON a.id = at.agent_id "
        "WHERE a.id IS NULL"
    ).fetchall()
    assert [r["agent_id"] for r in rows] == []


def test_no_orphan_agent_workspace_grants(v2_db):
    rows = v2_db.execute(
        "SELECT g.agent_id "
        "FROM agent_workspace_grants g "
        "LEFT JOIN agents a ON a.id = g.agent_id "
        "WHERE a.id IS NULL"
    ).fetchall()
    assert [r["agent_id"] for r in rows] == []


def test_no_orphan_agent_sandbox_grants(v2_db):
    rows = v2_db.execute(
        "SELECT g.agent_id "
        "FROM agent_sandbox_grants g "
        "LEFT JOIN agents a ON a.id = g.agent_id "
        "WHERE a.id IS NULL"
    ).fetchall()
    assert [r["agent_id"] for r in rows] == []


def test_no_orphan_paradigm_modes(v2_db):
    rows = v2_db.execute(
        "SELECT pm.paradigm_id "
        "FROM paradigm_modes pm "
        "LEFT JOIN paradigms p ON p.id = pm.paradigm_id "
        "WHERE p.id IS NULL"
    ).fetchall()
    assert [r["paradigm_id"] for r in rows] == []


# ---- Content audit : no active paradigm mentions a dead tool -------------


# The exception : `concise_output` and a few others mention tool names of the
# CALLER (e.g. report_back inside report_back_format paradigm) — those are OK.
# This is testing for *legacy* tool names that don't exist in v2 anymore.
_FORBIDDEN_TOOL_REFERENCES: list[str] = [
    "set_task_class",
    "manage_todo_list",
    "signal_convergence",
    "planner_done",
    "gather_done",
    "critic_done",
    "build_done",
]


def test_no_active_paradigm_mentions_dead_tool(v2_db):
    """No active paradigm content references a dead tool name."""
    where = " OR ".join(
        f"content LIKE '%{name}%'" for name in _FORBIDDEN_TOOL_REFERENCES
    )
    rows = v2_db.execute(
        f"SELECT id, code, content FROM paradigms WHERE active = 1 AND ({where})"
    ).fetchall()
    offenders = [(r["id"], r["code"]) for r in rows]
    assert offenders == [], (
        f"Active paradigms still mention dead tools: {offenders}"
    )


def test_no_active_paradigm_mentions_report_findings(v2_db):
    """`report_findings` is replaced by `report_back` in v2 — no active mentions."""
    rows = v2_db.execute(
        "SELECT id, code FROM paradigms "
        "WHERE active = 1 AND content LIKE '%report_findings%'"
    ).fetchall()
    offenders = [(r["id"], r["code"]) for r in rows]
    assert offenders == [], f"Active paradigms still mention report_findings: {offenders}"


# ---- Agent role coverage : every active agent has at least one paradigm ---


def test_every_active_agent_has_at_least_one_paradigm(v2_db):
    """Sanity : an active agent must have some prompt content. If a migration
    accidentally stripped all bindings of an agent, this test fails."""
    rows = v2_db.execute(
        "SELECT a.code, COUNT(ap.paradigm_id) AS n "
        "FROM agents a "
        "LEFT JOIN agent_paradigms ap ON ap.agent_id = a.id "
        "WHERE a.active = 1 "
        "GROUP BY a.id "
        "HAVING n = 0"
    ).fetchall()
    empty_agents = [r["code"] for r in rows]
    assert empty_agents == [], (
        f"Active agents with no paradigm bindings: {empty_agents}"
    )


# ---- Whitelist sanity ----------------------------------------------------


def test_agent_delegation_targets_codes_resolve(v2_db):
    """Every code in agent_delegation_targets.target_code must match an active agent."""
    rows = v2_db.execute(
        "SELECT adt.target_code, a.code "
        "FROM agent_delegation_targets adt "
        "LEFT JOIN agents a ON a.code = adt.target_code AND a.active = 1 "
        "WHERE a.code IS NULL"
    ).fetchall()
    dangling = [r["target_code"] for r in rows]
    # This may legitimately be empty (the whitelist is not seeded yet) ; the
    # test catches future regressions where someone seeds a target_code that
    # doesn't exist or has been deactivated.
    assert dangling == [], f"Dangling delegation targets: {dangling}"
