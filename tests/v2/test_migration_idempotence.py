"""Verify the v2 migration chain : schema + 100 + 101 + 102 → final state.

Tests :
- Apply the full chain on a fresh DB and check the resulting schema is the
  expected v2 final state (dropped legacy tables, new user_memory table,
  model_override column, dead grants cleaned up).
- Idempotence of the IF-EXISTS portions of each migration : apply 100 and 101
  twice without error. Migration 102 includes an `ALTER TABLE ADD COLUMN`
  which is one-shot (SQLite limitation) — we don't test double-apply for it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent.parent


def _apply_sql(conn: sqlite3.Connection, sql_path: Path) -> None:
    conn.executescript(sql_path.read_text(encoding="utf-8"))


@pytest.fixture()
def v2_migrated_db(tmp_path: Path):
    """Fresh DB obtained by applying the migration chain on the v1 baseline.

    `db/schema_v1_baseline.sql` is the pre-v2 schema (preserved for migration
    regression testing) ; the three migration files turn it into v2. This
    end-state should be equivalent to loading `db/schema.sql` (v2 final)
    directly.
    """
    db_path = tmp_path / "v2.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_sql(conn, _ROOT / "db" / "schema_v1_baseline.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_100_paradigm_realignment.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_101_user_memory.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_102_drop_runtime_tables.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_103_search_quality.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_104_drop_conv_read_file.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_105_strategist_agent.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_106_news_specialist.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_107_news_routing_and_web_fetch.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_108_code_fetcher_agent.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_109_code_runner_routing_and_sandbox.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_110_syntax_check_before_run.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_111_code_runner_to_reasoner.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_112_web_users.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_113_user_memory_isolation.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_114_conversation_cascade.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_115_image_search.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_116_vision_tools.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_117_image_display_routing.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_118_paradigms_english.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_119_image_results_cap.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_120_coding_decomposition.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_121_code_mode.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_122_workspace_file_ops.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_123_code_runner_node.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_124_projects.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_125_memory_scopes.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_126_memory_paradigms.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_127_graphify_paradigm.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_128_repo_tools.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_129_repo_test.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_130_code_paradigms.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_131_deliberation.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_132_comparator_delegation.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_133_project_repo.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_134_code_router.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_135_code_space_doctrine.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_136_repo_exec.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_137_git_checkpoint.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_138_project_dockerfile.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_139_remove_graphify.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_140_doctrine_mounts.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_141_ground_facts.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_142_code_analyst.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_143_todo_update.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_144_critics_are_validators.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_145_runner_bounces_readonly.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_146_apply_dont_describe.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_147_router_delegates_web_search.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_148_router_planning_sobriety.sql")
    yield conn
    conn.close()


@pytest.fixture()
def v2_consolidated_db(tmp_path: Path):
    """Fresh DB loaded directly from the consolidated `db/schema.sql` (v2 final)."""
    db_path = tmp_path / "v2_consolidated.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_sql(conn, _ROOT / "db" / "schema.sql")
    yield conn
    conn.close()


# ---- Schema shape after full migration chain ------------------------------


def test_legacy_tables_are_dropped(v2_migrated_db):
    """The 4 runtime tables that Phase 6 drops must be gone."""
    tables = {
        r["name"]
        for r in v2_migrated_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for legacy in ("requests", "artifacts", "conversation_phases", "sandbox_executions"):
        assert legacy not in tables, f"legacy table {legacy!r} not dropped"


def test_v2_tables_are_present(v2_migrated_db):
    """The expected v2 tables survive."""
    tables = {
        r["name"]
        for r in v2_migrated_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    expected = {
        "conversations",
        "agents",
        "agent_paradigms",
        "agent_tools",
        "agent_workspace_grants",
        "agent_sandbox_grants",
        "agent_delegation_targets",
        "sections",
        "categories",
        "paradigms",
        "paradigm_modes",
        "memory",
        "projects",
    }
    missing = expected - tables
    assert missing == set(), f"missing v2 tables: {missing}"


def test_model_override_column_present(v2_migrated_db):
    cols = {
        r["name"]
        for r in v2_migrated_db.execute("PRAGMA table_info(agents)").fetchall()
    }
    assert "model_override" in cols


def test_archivist_agent_deleted(v2_migrated_db):
    row = v2_migrated_db.execute(
        "SELECT COUNT(*) AS c FROM agents WHERE code='archivist'"
    ).fetchone()
    assert row["c"] == 0


def test_dead_tool_grants_removed(v2_migrated_db):
    """agent_tools must not reference tools we removed in the v2 codebase."""
    rows = v2_migrated_db.execute(
        "SELECT tool_code FROM agent_tools WHERE tool_code IN "
        "('set_task_class', 'manage_todo_list', 'signal_convergence', 'report_findings')"
    ).fetchall()
    assert rows == [], f"dead tool grants leaked: {[r['tool_code'] for r in rows]}"


def test_manage_memory_granted_to_jean_michel(v2_migrated_db):
    row = v2_migrated_db.execute(
        "SELECT 1 FROM agent_tools at JOIN agents a ON a.id = at.agent_id "
        "WHERE a.code = 'jean-michel' AND at.tool_code = 'manage_memory'"
    ).fetchone()
    assert row is not None


def test_memory_indices_present(v2_migrated_db):
    indices = {
        r["name"]
        for r in v2_migrated_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='memory'"
        ).fetchall()
    }
    # Partial unique indexes per scope + the recency index.
    assert {"ux_memory_user", "ux_memory_world", "idx_memory_modified"} <= indices


def test_new_paradigms_present_and_active(v2_migrated_db):
    rows = v2_migrated_db.execute(
        "SELECT code, active FROM paradigms WHERE code IN "
        "('memory_discipline', 'nested_delegation_discipline', "
        "'report_back_format', 'workspace_progressive_write', "
        "'output_contract_no_inline_dump')"
    ).fetchall()
    by_code = {r["code"]: int(r["active"]) for r in rows}
    expected = {
        "memory_discipline",
        "nested_delegation_discipline",
        "report_back_format",
        "workspace_progressive_write",
        "output_contract_no_inline_dump",
    }
    assert set(by_code.keys()) == expected
    # All five are active.
    assert all(v == 1 for v in by_code.values())


def test_total_active_paradigms_count(v2_migrated_db):
    """Sanity : the migration chain produces 120 active paradigms.

    104 from migrations 100-102 (cf. DevNotes/REVOLUCION/08_paradigm_audit_table.md)
    + 4 from migrate_103_search_quality (P1 breadth, P2 wiki lateral, P3 coverage
    check, P4 strategist_decomposition_discipline aka parallel_specialists)
    + 1 from migrate_105_strategist_agent (strategist_first, router-side)
    + 1 from migrate_106_news_specialist (news_freshness_discipline)
    + 1 from migrate_107_news_routing_and_web_fetch (news_first_for_news_briefs)
    + 3 from migrate_108_code_fetcher_agent (code_fetcher_multi_source,
                                              delegate_to_code_fetcher_on_doubt,
                                              cite_sources_in_user_facing_output)
    + 2 from migrate_109_code_runner_routing_and_sandbox
        (code_runner_for_code_production_briefs, test_in_sandbox_when_runnable)
    + 1 from migrate_117_image_display_routing (show_images_inline)
    + 1 from migrate_120_coding_decomposition (pdca_decompose_delegate_revise)
    + 1 from migrate_126_memory_paradigms (tool_note_discipline ;
        user_memory_discipline is renamed to memory_discipline, not added)
    + 1 from migrate_127_graphify_paradigm (graphify_codebase_navigation)
    + 2 from migrate_130_code_paradigms (repo_intervention_discipline,
                                         prefer_repo_tools_over_bash)
    + 2 from migrate_131_deliberation (critical_coder_method, sergent_kiss_gate)
    + 1 from migrate_135_code_space_doctrine (code_space_doctrine)
    + 1 from migrate_137_git_checkpoint (git_checkpoint_discipline)
    - 1 from migrate_139_remove_graphify (graphify_codebase_navigation removed)
    + 1 from migrate_141_ground_facts (ground_every_fact)
    + 1 from migrate_142_code_analyst (route_analysis_to_code_analyst)
    + 1 from migrate_145_runner_bounces_readonly (bounce_readonly_to_code_analyst)
    + 1 from migrate_146_apply_dont_describe (apply_dont_describe)
    + 1 from migrate_147_router_delegates_web_search (delegate_web_search).
    """
    row = v2_migrated_db.execute(
        "SELECT COUNT(*) AS c FROM paradigms WHERE active = 1"
    ).fetchone()
    assert row["c"] == 130


def test_runner_bounces_readonly_paradigm(v2_migrated_db, v2_consolidated_db):
    """P4 (migrate_145): code-runner + code-runner-node carry the read-only bounce
    paradigm so a mis-cast analysis is sent back to code-analyst, not fumbled into a
    production spiral. Present + active + bound identically in chain AND schema.sql."""
    for db in (v2_migrated_db, v2_consolidated_db):
        p = db.execute(
            "SELECT id, active FROM paradigms WHERE code='bounce_readonly_to_code_analyst'"
        ).fetchone()
        assert p is not None and int(p["active"]) == 1
        bound = {
            r["code"] for r in db.execute(
                "SELECT a.code FROM agent_paradigms ap JOIN agents a ON a.id=ap.agent_id "
                "WHERE ap.paradigm_id=?", (p["id"],),
            )
        }
        assert bound == {"code-runner", "code-runner-node"}


def test_apply_dont_describe_paradigm(v2_migrated_db, v2_consolidated_db):
    """migrate_146: code-runner + code-runner-node must APPLY edits (repo_edit/write),
    not describe them — anti hallucinated-completion (conv 825fb5b3). Present + active +
    bound identically in chain AND schema.sql."""
    for db in (v2_migrated_db, v2_consolidated_db):
        p = db.execute(
            "SELECT id, active FROM paradigms WHERE code='apply_dont_describe'"
        ).fetchone()
        assert p is not None and int(p["active"]) == 1
        bound = {
            r["code"] for r in db.execute(
                "SELECT a.code FROM agent_paradigms ap JOIN agents a ON a.id=ap.agent_id "
                "WHERE ap.paradigm_id=?", (p["id"],),
            )
        }
        assert bound == {"code-runner", "code-runner-node"}


def test_router_planning_sobriety(v2_migrated_db, v2_consolidated_db):
    """migrate_148: pdca is OFF jean-michel (it over-planned simple queries) but STAYS on
    code-router ; and no paradigm pins the rigid '3-7' step count anymore ('free the todo').
    Identical in chain AND schema.sql."""
    for db in (v2_migrated_db, v2_consolidated_db):
        bound = {r["code"] for r in db.execute(
            "SELECT a.code FROM agent_paradigms ap JOIN agents a ON a.id=ap.agent_id "
            "JOIN paradigms p ON p.id=ap.paradigm_id WHERE p.code='pdca_decompose_delegate_revise'")}
        assert "jean-michel" not in bound
        assert "code-router" in bound
        assert db.execute("SELECT COUNT(*) AS c FROM paradigms WHERE content LIKE '%3-7%'").fetchone()["c"] == 0


def test_router_delegates_web_search(v2_migrated_db, v2_consolidated_db):
    """migrate_147: jean-michel no longer holds web_search directly (it delegates to
    web-search-specialist, which writes findings to a workspace file), and carries the
    delegate_web_search doctrine. Identical in chain AND schema.sql."""
    for db in (v2_migrated_db, v2_consolidated_db):
        jm = db.execute("SELECT id FROM agents WHERE code='jean-michel'").fetchone()["id"]
        has = db.execute(
            "SELECT 1 FROM agent_tools WHERE agent_id=? AND tool_code='web_search'", (jm,)
        ).fetchone()
        assert has is None  # no direct web_search
        p = db.execute("SELECT id, active FROM paradigms WHERE code='delegate_web_search'").fetchone()
        assert p is not None and int(p["active"]) == 1
        bound = [r["agent_id"] for r in db.execute(
            "SELECT agent_id FROM agent_paradigms WHERE paradigm_id=?", (p["id"],))]
        assert bound == [jm]


def test_ground_every_fact_paradigm(v2_migrated_db, v2_consolidated_db):
    """migrate_141: global anti-hallucination paradigm present, and paradigm 79 no
    longer licenses parametric memory for 'stable facts'."""
    for db in (v2_migrated_db, v2_consolidated_db):
        r = db.execute(
            "SELECT is_global, active FROM paradigms WHERE code = 'ground_every_fact'"
        ).fetchone()
        assert r is not None, "ground_every_fact missing"
        assert int(r["is_global"]) == 1 and int(r["active"]) == 1
        c79 = db.execute("SELECT content FROM paradigms WHERE id = 79").fetchone()["content"]
        assert "parametric memory is fine" not in c79
        assert "ground_every_fact" in c79


# ---- Idempotence ---------------------------------------------------------


def test_migrate_100_idempotent(tmp_path):
    """migrate_100 can be applied twice without error or duplicate rows."""
    db_path = tmp_path / "idem100.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_sql(conn, _ROOT / "db" / "schema_v1_baseline.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_100_paradigm_realignment.sql")
    n_after_first = conn.execute(
        "SELECT COUNT(*) AS c FROM paradigms WHERE active = 1"
    ).fetchone()["c"]
    # Second apply.
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_100_paradigm_realignment.sql")
    n_after_second = conn.execute(
        "SELECT COUNT(*) AS c FROM paradigms WHERE active = 1"
    ).fetchone()["c"]
    assert n_after_first == n_after_second
    conn.close()


def test_migrate_101_idempotent(tmp_path):
    """migrate_101 (CREATE TABLE IF NOT EXISTS) can be applied multiple times."""
    db_path = tmp_path / "idem101.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_sql(conn, _ROOT / "db" / "schema_v1_baseline.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_101_user_memory.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_101_user_memory.sql")
    # Table still exists and is empty.
    row = conn.execute("SELECT COUNT(*) AS c FROM user_memory").fetchone()
    assert row["c"] == 0
    conn.close()


def test_migrate_102_drops_are_idempotent(tmp_path):
    """The DROP TABLE IF EXISTS statements in migrate_102 are idempotent.

    The ALTER TABLE ADD COLUMN is NOT — that's a documented SQLite limitation
    (the migration runner applies a file at most once per DB). We split the
    statements here to test just the idempotent drops.
    """
    db_path = tmp_path / "idem102_drops.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_sql(conn, _ROOT / "db" / "schema_v1_baseline.sql")

    # Re-applying drop statements alone is safe.
    drops = """
    DROP TABLE IF EXISTS sandbox_executions;
    DROP TABLE IF EXISTS artifacts;
    DROP TABLE IF EXISTS conversation_phases;
    DROP TABLE IF EXISTS requests;
    """
    conn.executescript(drops)
    conn.executescript(drops)  # second apply, must not raise
    tables = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "requests" not in tables
    assert "artifacts" not in tables
    conn.close()


# ---- migrate_112 : web users + conversation ownership --------------------


def test_web_users_tables_present(v2_migrated_db):
    """migrate_112 adds the multi-user web tables."""
    tables = {
        r["name"]
        for r in v2_migrated_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "web_users" in tables
    assert "conversation_users" in tables


def test_migrate_112_idempotent(tmp_path):
    """migrate_112 (CREATE ... IF NOT EXISTS) can be applied twice."""
    db_path = tmp_path / "idem112.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_sql(conn, _ROOT / "db" / "schema_v1_baseline.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_112_web_users.sql")
    _apply_sql(conn, _ROOT / "db" / "migrations" / "migrate_112_web_users.sql")
    row = conn.execute("SELECT COUNT(*) AS c FROM web_users").fetchone()
    assert row["c"] == 0
    conn.close()


# ---- jm.sh --install simulation : schema.sql alone == v2 final -----------


def test_schema_alone_is_v2_final(v2_consolidated_db):
    """Phase 8 consolidation : `db/schema.sql` IS the v2 final state.

    Fresh `./jm.sh --install` loads schema.sql directly — no migrations
    needed on top. Verified by comparing the consolidated schema's shape
    to the migrated schema's shape.
    """
    tables = {
        r["name"]
        for r in v2_consolidated_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    # v2 tables present.
    assert "memory" in tables
    assert "projects" in tables
    # Legacy tables absent.
    assert "requests" not in tables
    assert "artifacts" not in tables
    assert "user_memory" not in tables
    # Same paradigm count as via the migration chain.
    n = v2_consolidated_db.execute(
        "SELECT COUNT(*) AS c FROM paradigms WHERE active = 1"
    ).fetchone()["c"]
    assert n == 130


def test_consolidated_and_migrated_schemas_agree(v2_migrated_db, v2_consolidated_db):
    """The schema.sql consolidated state must equal the v1 + migrations state."""
    def _shape(conn):
        return {
            "tables": sorted(
                r["name"] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ),
            "active_paradigms": conn.execute(
                "SELECT COUNT(*) AS c FROM paradigms WHERE active = 1"
            ).fetchone()["c"],
            "active_agents": conn.execute(
                "SELECT COUNT(*) AS c FROM agents WHERE active = 1"
            ).fetchone()["c"],
            "agents_columns": sorted(
                r["name"] for r in conn.execute("PRAGMA table_info(agents)").fetchall()
            ),
            "web_users_columns": sorted(
                r["name"] for r in conn.execute("PRAGMA table_info(web_users)").fetchall()
            ),
            "memory_columns": sorted(
                r["name"] for r in conn.execute("PRAGMA table_info(memory)").fetchall()
            ),
            "projects_columns": sorted(
                r["name"] for r in conn.execute("PRAGMA table_info(projects)").fetchall()
            ),
        }

    assert _shape(v2_migrated_db) == _shape(v2_consolidated_db)


# ---- migrate_113 : user_memory isolation -----------------------------------


def test_memory_has_scope_and_targets(v2_migrated_db):
    cols = {r["name"] for r in v2_migrated_db.execute("PRAGMA table_info(memory)")}
    assert {"scope", "user_id", "project_id", "tool_code"} <= cols


def test_web_users_has_profile_columns(v2_migrated_db):
    cols = {r["name"] for r in v2_migrated_db.execute("PRAGMA table_info(web_users)")}
    assert {"name", "birthdate", "city", "country", "language", "interests", "notes"} <= cols


def test_cli_user_created(v2_migrated_db):
    row = v2_migrated_db.execute("SELECT id FROM web_users WHERE username='cli'").fetchone()
    assert row is not None


# ---- migrate_114 : conversation deletion cascade ---------------------------


def test_conversation_users_cascade_in_migration(v2_migrated_db):
    on_delete = {
        r["table"]: r["on_delete"]
        for r in v2_migrated_db.execute("PRAGMA foreign_key_list(conversation_users)")
    }
    assert on_delete.get("conversations") == "CASCADE"
    assert on_delete.get("web_users") == "CASCADE"


def test_conversation_users_cascade_in_consolidated_schema(v2_consolidated_db):
    """Fresh installs load schema.sql directly — it must carry the cascade too."""
    on_delete = {
        r["table"]: r["on_delete"]
        for r in v2_consolidated_db.execute("PRAGMA foreign_key_list(conversation_users)")
    }
    assert on_delete.get("conversations") == "CASCADE"
    assert on_delete.get("web_users") == "CASCADE"


# ---- migrate_115 : image_search grant --------------------------------------


def test_image_search_granted(v2_migrated_db, v2_consolidated_db):
    """Both the migration chain and schema.sql grant image_search to the router
    and the web-search-specialist."""
    for db in (v2_migrated_db, v2_consolidated_db):
        codes = {
            r["code"]
            for r in db.execute(
                "SELECT a.code FROM agent_tools at JOIN agents a ON a.id = at.agent_id "
                "WHERE at.tool_code = 'image_search'"
            )
        }
        assert {"jean-michel", "web-search-specialist"} <= codes


def test_vision_tools_granted(v2_migrated_db, v2_consolidated_db):
    """migrate_116 + schema.sql grant analyze_image + image_fetch to the same agents."""
    for db in (v2_migrated_db, v2_consolidated_db):
        for tool in ("analyze_image", "image_fetch"):
            codes = {
                r["code"]
                for r in db.execute(
                    "SELECT a.code FROM agent_tools at JOIN agents a ON a.id = at.agent_id "
                    "WHERE at.tool_code = ?",
                    (tool,),
                )
            }
            assert {"jean-michel", "web-search-specialist"} <= codes, tool


def test_show_images_inline_paradigm(v2_migrated_db, v2_consolidated_db):
    """migrate_117 : the router gets an active 'show_images_inline' routing paradigm."""
    for db in (v2_migrated_db, v2_consolidated_db):
        row = db.execute(
            "SELECT 1 FROM agent_paradigms ap "
            "JOIN agents a ON a.id = ap.agent_id "
            "JOIN paradigms p ON p.id = ap.paradigm_id "
            "WHERE a.code = 'jean-michel' AND p.code = 'show_images_inline' AND p.active = 1"
        ).fetchone()
        assert row is not None


def test_paradigm_content_is_english(v2_migrated_db, v2_consolidated_db):
    """migrate_118 : prompt-facing paradigm text (content + rendered category
    titles) is English-only — the internal/inter-LLM language is English."""
    import re

    accent = re.compile(r"[àâäéèêëïîôöùûüçœ]", re.IGNORECASE)
    fr_words = {"une", "des", "vous", "avec", "cette", "montre", "voici", "dans"}
    for db in (v2_migrated_db, v2_consolidated_db):
        for code, content in db.execute("SELECT code, content FROM paradigms WHERE active = 1"):
            text = content or ""
            assert not accent.search(text), f"{code}: accented French in content"
            words = set(re.findall(r"[a-zà-ÿ]+", text.lower()))
            assert not (words & fr_words), f"{code}: French words {words & fr_words}"
        for (title,) in db.execute("SELECT title FROM categories WHERE active = 1"):
            assert not accent.search(title or ""), f"non-English category title: {title!r}"


# ---- migrate_128 : repo_* tool grants --------------------------------------


def test_repo_tools_granted(v2_migrated_db, v2_consolidated_db):
    """migrate_128 + schema.sql grant the repo_* tools to the coding agents."""
    for db in (v2_migrated_db, v2_consolidated_db):
        def codes(tool, conn=db):
            return {
                r["code"]
                for r in conn.execute(
                    "SELECT a.code FROM agent_tools at JOIN agents a ON a.id = at.agent_id "
                    "WHERE at.tool_code = ?", (tool,)
                )
            }
        for tool in ("repo_read", "repo_grep", "repo_glob", "repo_edit", "repo_write"):
            assert {"code-runner", "code-runner-node"} <= codes(tool), tool
        # code-fetcher is EXTERNAL-only since migrate_142 : no repo tools at all.
        assert "code-fetcher" not in codes("repo_read")
        assert "code-fetcher" not in codes("repo_edit")
        assert "code-fetcher" not in codes("repo_write")
        # code-analyst is the read-only analyst : reads but never edits/runs.
        assert "code-analyst" in codes("repo_read")
        assert "code-analyst" in codes("repo_grep")
        assert "code-analyst" in codes("repo_git")
        assert "code-analyst" not in codes("repo_edit")
        assert "code-analyst" not in codes("repo_write")
        assert "code-analyst" not in codes("repo_exec")
        assert "code-analyst" not in codes("repo_test")


def test_critics_are_validators(v2_migrated_db, v2_consolidated_db):
    """migrate_144 : critical-coder/sergent-kiss missions reframed as validators
    (grounded in sources), not creatives."""
    for db in (v2_migrated_db, v2_consolidated_db):
        cc = db.execute("SELECT mission FROM agents WHERE code='critical-coder'").fetchone()["mission"]
        sk = db.execute("SELECT mission FROM agents WHERE code='sergent-kiss'").fetchone()["mission"]
        assert "Validator" in cc and "do NOT propose, design, or invent" in cc
        assert "validation gate" in sk and "never redesign" in sk


def test_todo_update_granted_to_routers(v2_migrated_db, v2_consolidated_db):
    """migrate_143 : the granular todo_update tool is granted to the plan owners
    (the agents that have todo_write) — jean-michel + code-router."""
    for db in (v2_migrated_db, v2_consolidated_db):
        granted = {
            r["code"] for r in db.execute(
                "SELECT a.code FROM agent_tools t JOIN agents a ON a.id = t.agent_id "
                "WHERE t.tool_code = 'todo_update'"
            )
        }
        assert granted == {"jean-michel", "code-router"}


def test_code_analyst_cast(v2_migrated_db, v2_consolidated_db):
    """migrate_142 : code-analyst is the read-only analyst cast — a code-router
    delegation target, and NOT a CODE_WORKER (so analysis never triggers the
    code-production deliberation that exploded the 129-call run)."""
    from jeanmichel.deliberation import CODE_WORKERS
    assert "code-analyst" not in CODE_WORKERS
    for db in (v2_migrated_db, v2_consolidated_db):
        targets = {
            r["target_code"] for r in db.execute(
                "SELECT target_code FROM agent_delegation_targets adt "
                "JOIN agents a ON a.id = adt.agent_id WHERE a.code = 'code-router'"
            )
        }
        assert "code-analyst" in targets
        row = db.execute("SELECT role, active FROM agents WHERE code = 'code-analyst'").fetchone()
        assert row is not None and row["role"] == "specialist" and int(row["active"]) == 1


_P1_REPO_TOOLS = "('repo_read','repo_grep','repo_glob','repo_edit','repo_write')"


def test_migrate_128_idempotent(v2_migrated_db):
    """migrate_128 (INSERT OR IGNORE) can be re-applied without duplicate grants."""
    # Scope to the WORKERS only : migrate_142 strips code-fetcher's repo_* grants
    # (external-only), so re-applying migrate_128 would resurrect them — including
    # code-fetcher here would muddy the pure no-duplicate check.
    q = (
        f"SELECT COUNT(*) AS c FROM agent_tools at JOIN agents a ON a.id = at.agent_id "
        f"WHERE at.tool_code IN {_P1_REPO_TOOLS} "
        f"AND a.code IN ('code-runner','code-runner-node')"
    )
    n1 = v2_migrated_db.execute(q).fetchone()["c"]
    _apply_sql(v2_migrated_db, _ROOT / "db" / "migrations" / "migrate_128_repo_tools.sql")
    n2 = v2_migrated_db.execute(q).fetchone()["c"]
    assert n1 == n2 == 10  # 5 (code-runner) + 5 (code-runner-node)


def test_migrate_129_repo_test_granted(v2_migrated_db, v2_consolidated_db):
    """migrate_129 grants repo_test to the workers only. (repo_graph_refresh was also
    granted by 129 but removed by migrate_139 — graphify retiré.)"""
    for db in (v2_migrated_db, v2_consolidated_db):
        def codes(tool, conn=db):
            return {
                r["code"]
                for r in conn.execute(
                    "SELECT a.code FROM agent_tools at JOIN agents a ON a.id = at.agent_id "
                    "WHERE at.tool_code = ?", (tool,)
                )
            }
        assert codes("repo_test") >= {"code-runner", "code-runner-node"}
        assert "code-fetcher" not in codes("repo_test")  # lookup agent doesn't run code
        assert codes("repo_graph_refresh") == set()      # removed with graphify (migrate_139)


# ---- migrate_130 : code-mode discipline paradigms --------------------------

_P4_CODES = {"repo_intervention_discipline", "prefer_repo_tools_over_bash"}


def test_code_paradigms_present_gated_and_bound(v2_migrated_db, v2_consolidated_db):
    """migrate_130 + schema.sql: the two code paradigms exist, are gated to mode
    'code' ONLY (anti-leak), and are bound to BOTH coding workers."""
    for db in (v2_migrated_db, v2_consolidated_db):
        present = {
            r["code"] for r in db.execute(
                "SELECT code FROM paradigms WHERE active = 1 AND code IN "
                "('repo_intervention_discipline','prefer_repo_tools_over_bash')"
            )
        }
        assert present == _P4_CODES
        for code in _P4_CODES:
            modes = {
                r["mode"] for r in db.execute(
                    "SELECT mode FROM paradigm_modes pm JOIN paradigms p ON p.id = pm.paradigm_id "
                    "WHERE p.code = ?", (code,)
                )
            }
            assert modes == {"code"}, (code, modes)  # gated to code only
            agents = {
                r["code"] for r in db.execute(
                    "SELECT a.code FROM agent_paradigms ap JOIN agents a ON a.id = ap.agent_id "
                    "JOIN paradigms p ON p.id = ap.paradigm_id WHERE p.code = ?", (code,)
                )
            }
            assert {"code-runner", "code-runner-node"} <= agents, (code, agents)


def test_code_paradigms_only_render_in_code_mode(v2_consolidated_db):
    """End-to-end gating via the real loader: code-runner sees the discipline in
    'code' mode, NOT in 'chat' (the anti-regression invariant)."""
    from jeanmichel import db as jdb

    cr = v2_consolidated_db.execute("SELECT id FROM agents WHERE code='code-runner'").fetchone()["id"]
    in_code = {p.code for p in jdb.load_paradigms_for_agent(v2_consolidated_db, cr, "code")}
    in_chat = {p.code for p in jdb.load_paradigms_for_agent(v2_consolidated_db, cr, "chat")}
    assert "repo_intervention_discipline" in in_code
    assert "repo_intervention_discipline" not in in_chat
    assert "prefer_repo_tools_over_bash" in in_code
    assert "prefer_repo_tools_over_bash" not in in_chat


# ---- migrate_135 : code space doctrine + workspace gating + repo_git -------


def test_code_space_doctrine_gated_and_bound(v2_migrated_db, v2_consolidated_db):
    """migrate_135 + schema.sql: code_space_doctrine exists, leads (low priority),
    is gated to 'code' only, and is bound to BOTH coding workers."""
    for db in (v2_migrated_db, v2_consolidated_db):
        row = db.execute(
            "SELECT order_priority FROM paradigms WHERE code='code_space_doctrine' AND active=1"
        ).fetchone()
        assert row is not None and row["order_priority"] == 8  # leads behavioural paradigms
        modes = {
            r["mode"] for r in db.execute(
                "SELECT mode FROM paradigm_modes pm JOIN paradigms p ON p.id=pm.paradigm_id "
                "WHERE p.code='code_space_doctrine'"
            )
        }
        assert modes == {"code"}
        agents = {
            r["code"] for r in db.execute(
                "SELECT a.code FROM agent_paradigms ap JOIN agents a ON a.id=ap.agent_id "
                "JOIN paradigms p ON p.id=ap.paradigm_id WHERE p.code='code_space_doctrine'"
            )
        }
        assert {"code-runner", "code-runner-node"} <= agents


def test_workspace_tools_only_gated_out_of_code(v2_migrated_db, v2_consolidated_db):
    """migrate_135: workspace_tools_only no longer applies in code mode (the scratch
    is not 'the source of truth' when a repo is attached), but still in the others.
    Verified through the real loader for code-runner."""
    from jeanmichel import db as jdb
    for db in (v2_migrated_db, v2_consolidated_db):
        modes = {
            r["mode"] for r in db.execute(
                "SELECT mode FROM paradigm_modes pm JOIN paradigms p ON p.id=pm.paradigm_id "
                "WHERE p.code='workspace_tools_only'"
            )
        }
        assert modes == {"analyse", "chat", "vocal"}  # gated OUT of code
        cr = db.execute("SELECT id FROM agents WHERE code='code-runner'").fetchone()["id"]
        in_code = {p.code for p in jdb.load_paradigms_for_agent(db, cr, "code")}
        in_chat = {p.code for p in jdb.load_paradigms_for_agent(db, cr, "chat")}
        assert "code_space_doctrine" in in_code and "code_space_doctrine" not in in_chat
        assert "workspace_tools_only" not in in_code  # the fix
        assert "workspace_tools_only" in in_chat       # non-regression


def test_repo_git_granted_to_coding_workers(v2_migrated_db, v2_consolidated_db):
    """migrate_135: the read-only repo_git tool is granted to both coding workers."""
    for db in (v2_migrated_db, v2_consolidated_db):
        agents = {
            r["code"] for r in db.execute(
                "SELECT a.code FROM agent_tools t JOIN agents a ON a.id=t.agent_id "
                "WHERE t.tool_code='repo_git'"
            )
        }
        assert {"code-runner", "code-runner-node"} <= agents


# ---- migrate_136 : repo_exec (project sandbox) -----------------------------


def test_repo_exec_granted_and_doctrine_names_it(v2_migrated_db, v2_consolidated_db):
    """migrate_136: repo_exec granted to both coding workers, and the doctrine
    paradigm now names the project sandbox (repo_exec)."""
    for db in (v2_migrated_db, v2_consolidated_db):
        agents = {
            r["code"] for r in db.execute(
                "SELECT a.code FROM agent_tools t JOIN agents a ON a.id=t.agent_id "
                "WHERE t.tool_code='repo_exec'"
            )
        }
        assert {"code-runner", "code-runner-node"} <= agents
        content = db.execute(
            "SELECT content FROM paradigms WHERE code='code_space_doctrine'"
        ).fetchone()["content"]
        assert "repo_exec" in content and "PROJECT SANDBOX" in content
        # migrate_140: the two mount points are spelled out (repo /app + scratch /workspace).
        assert "/app" in content and "/workspace" in content


# ---- migrate_137 : git checkpoint discipline -------------------------------


def test_git_checkpoint_discipline_gated_and_bound(v2_migrated_db, v2_consolidated_db):
    """migrate_137: git_checkpoint_discipline exists, gated to 'code' only, bound
    to both coding workers."""
    for db in (v2_migrated_db, v2_consolidated_db):
        modes = {
            r["mode"] for r in db.execute(
                "SELECT mode FROM paradigm_modes pm JOIN paradigms p ON p.id=pm.paradigm_id "
                "WHERE p.code='git_checkpoint_discipline'"
            )
        }
        assert modes == {"code"}
        agents = {
            r["code"] for r in db.execute(
                "SELECT a.code FROM agent_paradigms ap JOIN agents a ON a.id=ap.agent_id "
                "JOIN paradigms p ON p.id=ap.paradigm_id WHERE p.code='git_checkpoint_discipline'"
            )
        }
        assert {"code-runner", "code-runner-node"} <= agents


# ---- migrate_138 : projects.dockerfile -------------------------------------


def test_projects_has_dockerfile_column(v2_migrated_db, v2_consolidated_db):
    """migrate_138 + schema.sql: projects.dockerfile exists (default '')."""
    for conn in (v2_migrated_db, v2_consolidated_db):
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
        assert "dockerfile" in cols


# ---- migrate_132 : comparator delegation whitelist -------------------------


def test_comparator_delegation_whitelist(v2_migrated_db, v2_consolidated_db):
    """migrate_132 + schema.sql give comparator-specialist an explicit whitelist."""
    for db in (v2_migrated_db, v2_consolidated_db):
        targets = {
            r["target_code"] for r in db.execute(
                "SELECT target_code FROM agent_delegation_targets ad "
                "JOIN agents a ON a.id = ad.agent_id WHERE a.code = 'comparator-specialist'"
            )
        }
        assert targets == {
            "web-search-specialist", "wikipedia-specialist",
            "weather-specialist", "news-specialist",
        }


# ---- migrate_133 : project repo attach -------------------------------------


def test_projects_repo_columns(v2_migrated_db, v2_consolidated_db):
    """migrate_133 + schema.sql add code_repo + repo_kind to projects."""
    for db in (v2_migrated_db, v2_consolidated_db):
        cols = {r["name"] for r in db.execute("PRAGMA table_info(projects)")}
        assert {"code_repo", "repo_kind"} <= cols


# ---- migrate_134 : dedicated code-router -----------------------------------


def test_code_router_agent(v2_migrated_db, v2_consolidated_db):
    """migrate_134 + schema.sql: code-router is a router on qwen3:14b, lean
    paradigm set, delegating to the code workers."""
    for db in (v2_migrated_db, v2_consolidated_db):
        row = db.execute(
            "SELECT role, model_override FROM agents WHERE code='code-router' AND active=1"
        ).fetchone()
        assert row is not None and row["role"] == "router"
        assert row["model_override"] == "qwen3:14b"
        n_para = db.execute(
            "SELECT COUNT(*) AS c FROM agent_paradigms ap JOIN agents a ON a.id=ap.agent_id "
            "WHERE a.code='code-router'"
        ).fetchone()["c"]
        assert n_para == 15  # 15 à l'origine ; -1 graphify (migrate_139) ; +1 route_analysis_to_code_analyst (migrate_142)
        targets = {
            r["target_code"] for r in db.execute(
                "SELECT target_code FROM agent_delegation_targets ad JOIN agents a ON a.id=ad.agent_id "
                "WHERE a.code='code-router'"
            )
        }
        assert targets == {"code-runner", "code-runner-node", "code-fetcher", "code-analyst"}


def test_code_router_is_leaner_than_jean_michel(v2_consolidated_db):
    """The whole point: code-router carries far fewer bound paradigms than the
    generalist jean-michel (focus → reliable delegation for small models)."""
    from jeanmichel.orchestrator_v2 import load_agent_spec_v2

    cr = load_agent_spec_v2(v2_consolidated_db, "code-router", mode="code")
    assert cr.role == "router" and cr.model == "qwen3:14b"

    def n_bound(code):
        return v2_consolidated_db.execute(
            "SELECT COUNT(*) AS c FROM agent_paradigms ap JOIN agents a ON a.id=ap.agent_id "
            "WHERE a.code=?", (code,)
        ).fetchone()["c"]
    assert n_bound("code-router") < n_bound("jean-michel")  # 15 vs 46
