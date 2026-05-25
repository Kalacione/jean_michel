"""DB-level consistency invariants.

These tests load db/schema.sql into a temp DB and assert structural rules
that should hold for any fresh install. They catch drift between
``agent_tools`` and ``agent_workspace_grants`` (the failure mode behind the
``no_write_grant`` runtime error seen on 2026-05-25).
"""

from __future__ import annotations

from jeanmichel import db
from jeanmichel.tools import WORKSPACE_WRITE_TOOLS


def test_every_write_tool_grant_has_workspace_grant(tmp_env):
    """If an agent has a workspace write tool in agent_tools, it must also
    have a row in agent_workspace_grants — otherwise every call to that tool
    fails at runtime with no_write_grant."""
    with db.connect() as conn:
        placeholders = ",".join("?" * len(WORKSPACE_WRITE_TOOLS))
        rows = conn.execute(
            f"""
            SELECT a.code, at.tool_code
            FROM agent_tools at
            JOIN agents a ON a.id = at.agent_id
            WHERE at.tool_code IN ({placeholders})
              AND a.id NOT IN (SELECT agent_id FROM agent_workspace_grants)
            ORDER BY a.code, at.tool_code
            """,
            tuple(sorted(WORKSPACE_WRITE_TOOLS)),
        ).fetchall()

    offenders = [(r["code"], r["tool_code"]) for r in rows]
    assert not offenders, (
        "Drift detected — these agents have a workspace write tool in "
        "agent_tools but no agent_workspace_grants row. They will hit "
        "no_write_grant at runtime. Either add the grant or remove the tool. "
        f"Offenders: {offenders}"
    )


def test_workspace_grants_reference_existing_agents(tmp_env):
    """Every agent_workspace_grants row should point to a real agent."""
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT g.agent_id
            FROM agent_workspace_grants g
            LEFT JOIN agents a ON a.id = g.agent_id
            WHERE a.id IS NULL
            """,
        ).fetchall()
    assert not rows, f"agent_workspace_grants references missing agent_id(s): {[r[0] for r in rows]}"


def test_delegation_targets_reference_existing_agents(tmp_env):
    """Every agent_delegation_targets row should point to an active agent code."""
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT adt.agent_id, adt.target_code
            FROM agent_delegation_targets adt
            LEFT JOIN agents a ON a.code = adt.target_code AND a.active = 1
            WHERE a.id IS NULL
            """,
        ).fetchall()
    assert not rows, (
        "agent_delegation_targets references missing/inactive agent codes: "
        f"{[(r['agent_id'], r['target_code']) for r in rows]}"
    )


def test_comparator_specialist_has_write_grant(tmp_env):
    """Regression test for the 2026-05-25 incident: comparator-specialist
    must be able to write workspace files (it produces comparison tables)."""
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM agent_workspace_grants g
            JOIN agents a ON a.id = g.agent_id
            WHERE a.code = 'comparator-specialist'
            """,
        ).fetchone()
    assert row is not None, (
        "comparator-specialist is missing from agent_workspace_grants. "
        "It needs write access to materialise its comparison table."
    )
