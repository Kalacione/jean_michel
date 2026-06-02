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
        "user_memory",
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


def test_manage_user_memory_granted_to_jean_michel(v2_migrated_db):
    row = v2_migrated_db.execute(
        "SELECT 1 FROM agent_tools at JOIN agents a ON a.id = at.agent_id "
        "WHERE a.code = 'jean-michel' AND at.tool_code = 'manage_user_memory'"
    ).fetchone()
    assert row is not None


def test_user_memory_indices_present(v2_migrated_db):
    indices = {
        r["name"]
        for r in v2_migrated_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='user_memory'"
        ).fetchall()
    }
    assert "idx_user_memory_type" in indices
    assert "idx_user_memory_modified" in indices


def test_new_paradigms_present_and_active(v2_migrated_db):
    rows = v2_migrated_db.execute(
        "SELECT code, active FROM paradigms WHERE code IN "
        "('user_memory_discipline', 'nested_delegation_discipline', "
        "'report_back_format', 'workspace_progressive_write', "
        "'output_contract_no_inline_dump')"
    ).fetchall()
    by_code = {r["code"]: int(r["active"]) for r in rows}
    expected = {
        "user_memory_discipline",
        "nested_delegation_discipline",
        "report_back_format",
        "workspace_progressive_write",
        "output_contract_no_inline_dump",
    }
    assert set(by_code.keys()) == expected
    # All five are active.
    assert all(v == 1 for v in by_code.values())


def test_total_active_paradigms_count(v2_migrated_db):
    """Sanity : the migration chain produces 118 active paradigms.

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
    + 1 from migrate_120_coding_decomposition (pdca_decompose_delegate_revise).
    """
    row = v2_migrated_db.execute(
        "SELECT COUNT(*) AS c FROM paradigms WHERE active = 1"
    ).fetchone()
    assert row["c"] == 118


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
    assert "user_memory" in tables
    # Legacy tables absent.
    assert "requests" not in tables
    assert "artifacts" not in tables
    # Same paradigm count as via the migration chain.
    n = v2_consolidated_db.execute(
        "SELECT COUNT(*) AS c FROM paradigms WHERE active = 1"
    ).fetchone()["c"]
    assert n == 118


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
            "user_memory_columns": sorted(
                r["name"] for r in conn.execute("PRAGMA table_info(user_memory)").fetchall()
            ),
        }

    assert _shape(v2_migrated_db) == _shape(v2_consolidated_db)


# ---- migrate_113 : user_memory isolation -----------------------------------


def test_user_memory_has_user_id(v2_migrated_db):
    cols = {r["name"] for r in v2_migrated_db.execute("PRAGMA table_info(user_memory)")}
    assert "user_id" in cols


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
