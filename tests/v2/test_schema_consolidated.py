"""Pin the invariants of `db/schema.sql` — the consolidated install baseline.

`db/schema.sql` is the single source of truth loaded verbatim by `./jm.sh --install`
(at `user_version 0` : the migration baseline). The historical migration chain that
built it is gone ; these tests are the anti-drift guard rail in its place. They load
schema.sql into a fresh DB and assert the shape a fresh install must have : legacy
tables gone, v2 tables/columns present, the pinned active paradigm/agent counts, key
tool grants + paradigm bindings + mode gating, FK cascades, and a working memory FTS.

When you change schema.sql, these counts/bindings keep you honest — update them in the
same breath as the schema, or the test tells you what you forgot.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture()
def schema_db(tmp_path: Path):
    """Fresh DB loaded directly from the consolidated db/schema.sql (what --install does)."""
    db_path = tmp_path / "schema.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript((_ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
    yield conn
    conn.close()


# ---- Migration baseline ---------------------------------------------------


def test_schema_is_migration_baseline_version_zero(schema_db):
    """schema.sql IS the baseline : a fresh install sits at user_version 0, so the
    `--migrate` runner only ever applies future migrate_NNN files (NNN >= 1)."""
    assert schema_db.execute("PRAGMA user_version").fetchone()[0] == 0


# ---- Schema shape ---------------------------------------------------------


def test_legacy_tables_are_absent(schema_db):
    """The pre-v2 runtime tables must not exist in the consolidated schema."""
    tables = {
        r["name"]
        for r in schema_db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for legacy in (
        "requests",
        "artifacts",
        "conversation_phases",
        "sandbox_executions",
        "user_memory",
    ):
        assert legacy not in tables, f"legacy table {legacy!r} leaked into schema.sql"


def test_v2_tables_are_present(schema_db):
    """The expected v2 tables are all present."""
    tables = {
        r["name"]
        for r in schema_db.execute("SELECT name FROM sqlite_master WHERE type='table'")
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
        "pending_consolidation",
        "projects",
        "web_users",
        "conversation_users",
    }
    missing = expected - tables
    assert missing == set(), f"missing v2 tables: {missing}"


def test_model_override_column_present(schema_db):
    cols = {r["name"] for r in schema_db.execute("PRAGMA table_info(agents)")}
    assert "model_override" in cols


def test_archivist_agent_absent(schema_db):
    row = schema_db.execute(
        "SELECT COUNT(*) AS c FROM agents WHERE code='archivist'"
    ).fetchone()
    assert row["c"] == 0


def test_dead_tool_grants_absent(schema_db):
    """agent_tools must not reference tools removed from the v2 codebase."""
    rows = schema_db.execute(
        "SELECT tool_code FROM agent_tools WHERE tool_code IN "
        "('set_task_class', 'manage_todo_list', 'signal_convergence', 'report_findings')"
    ).fetchall()
    assert rows == [], f"dead tool grants leaked: {[r['tool_code'] for r in rows]}"


def test_manage_memory_granted_to_jean_michel(schema_db):
    row = schema_db.execute(
        "SELECT 1 FROM agent_tools at JOIN agents a ON a.id = at.agent_id "
        "WHERE a.code = 'jean-michel' AND at.tool_code = 'manage_memory'"
    ).fetchone()
    assert row is not None


def test_memory_indices_present(schema_db):
    indices = {
        r["name"]
        for r in schema_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='memory'"
        )
    }
    # Partial unique index per user scope + the recency index (no `world` scope anymore).
    assert {"ux_memory_user", "idx_memory_modified"} <= indices


# ---- Counts (the anti-drift pins) -----------------------------------------


def test_total_active_paradigms_count(schema_db):
    """Pinned : 130 active paradigms. Bump this deliberately when you add/retire one
    in schema.sql — it's the guard that a seed edit did exactly what you intended."""
    row = schema_db.execute(
        "SELECT COUNT(*) AS c FROM paradigms WHERE active = 1"
    ).fetchone()
    assert row["c"] == 130


def test_total_active_agents_count(schema_db):
    """Pinned : 20 active agents (the full v2 roster — routers, specialists, workers)."""
    row = schema_db.execute(
        "SELECT COUNT(*) AS c FROM agents WHERE active = 1"
    ).fetchone()
    assert row["c"] == 20


def test_new_paradigms_present_and_active(schema_db):
    rows = schema_db.execute(
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
    assert all(v == 1 for v in by_code.values())


# ---- Key paradigm bindings + routing --------------------------------------


def test_runner_bounces_readonly_paradigm(schema_db):
    """code-runner + code-runner-node carry the read-only bounce paradigm so a
    mis-cast analysis is sent back to code-analyst, not fumbled into a production spiral."""
    p = schema_db.execute(
        "SELECT id, active FROM paradigms WHERE code='bounce_readonly_to_code_analyst'"
    ).fetchone()
    assert p is not None and int(p["active"]) == 1
    bound = {
        r["code"]
        for r in schema_db.execute(
            "SELECT a.code FROM agent_paradigms ap JOIN agents a ON a.id=ap.agent_id "
            "WHERE ap.paradigm_id=?",
            (p["id"],),
        )
    }
    assert bound == {"code-runner", "code-runner-node"}


def test_apply_dont_describe_paradigm(schema_db):
    """code-runner + code-runner-node must APPLY edits (repo_edit/write), not describe
    them — anti hallucinated-completion."""
    p = schema_db.execute(
        "SELECT id, active FROM paradigms WHERE code='apply_dont_describe'"
    ).fetchone()
    assert p is not None and int(p["active"]) == 1
    bound = {
        r["code"]
        for r in schema_db.execute(
            "SELECT a.code FROM agent_paradigms ap JOIN agents a ON a.id=ap.agent_id "
            "WHERE ap.paradigm_id=?",
            (p["id"],),
        )
    }
    assert bound == {"code-runner", "code-runner-node"}


def test_router_planning_sobriety(schema_db):
    """pdca is OFF jean-michel (it over-planned simple queries) but STAYS on code-router,
    and no paradigm pins the rigid '3-7' step count anymore (the todo is free)."""
    bound = {
        r["code"]
        for r in schema_db.execute(
            "SELECT a.code FROM agent_paradigms ap JOIN agents a ON a.id=ap.agent_id "
            "JOIN paradigms p ON p.id=ap.paradigm_id WHERE p.code='pdca_decompose_delegate_revise'"
        )
    }
    assert "jean-michel" not in bound
    assert "code-router" in bound
    assert (
        schema_db.execute(
            "SELECT COUNT(*) AS c FROM paradigms WHERE content LIKE '%3-7%'"
        ).fetchone()["c"]
        == 0
    )


def test_router_delegates_web_search(schema_db):
    """jean-michel no longer holds web_search directly (it delegates to web-search-specialist,
    which writes findings to a workspace file), and carries the delegate_web_search doctrine."""
    jm = schema_db.execute("SELECT id FROM agents WHERE code='jean-michel'").fetchone()["id"]
    has = schema_db.execute(
        "SELECT 1 FROM agent_tools WHERE agent_id=? AND tool_code='web_search'", (jm,)
    ).fetchone()
    assert has is None  # no direct web_search
    p = schema_db.execute(
        "SELECT id, active FROM paradigms WHERE code='delegate_web_search'"
    ).fetchone()
    assert p is not None and int(p["active"]) == 1
    bound = [
        r["agent_id"]
        for r in schema_db.execute(
            "SELECT agent_id FROM agent_paradigms WHERE paradigm_id=?", (p["id"],)
        )
    ]
    assert bound == [jm]


def test_ground_every_fact_paradigm(schema_db):
    """Global anti-hallucination paradigm present, and paradigm 79 no longer licenses
    parametric memory for 'stable facts'."""
    r = schema_db.execute(
        "SELECT is_global, active FROM paradigms WHERE code = 'ground_every_fact'"
    ).fetchone()
    assert r is not None, "ground_every_fact missing"
    assert int(r["is_global"]) == 1 and int(r["active"]) == 1
    c79 = schema_db.execute("SELECT content FROM paradigms WHERE id = 79").fetchone()["content"]
    assert "parametric memory is fine" not in c79
    assert "ground_every_fact" in c79


# ---- Multi-user web tables ------------------------------------------------


def test_memory_has_scope_and_targets(schema_db):
    cols = {r["name"] for r in schema_db.execute("PRAGMA table_info(memory)")}
    assert {"scope", "user_id", "project_id", "tool_code", "importance"} <= cols


def test_web_users_has_profile_columns(schema_db):
    cols = {r["name"] for r in schema_db.execute("PRAGMA table_info(web_users)")}
    assert {
        "name",
        "birthdate",
        "city",
        "country",
        "language",
        "interests",
        "notes",
    } <= cols


def test_cli_user_seeded(schema_db):
    row = schema_db.execute("SELECT id FROM web_users WHERE username='cli'").fetchone()
    assert row is not None


def test_conversation_users_cascade(schema_db):
    """Fresh installs load schema.sql directly — it must carry the deletion cascade."""
    on_delete = {
        r["table"]: r["on_delete"]
        for r in schema_db.execute("PRAGMA foreign_key_list(conversation_users)")
    }
    assert on_delete.get("conversations") == "CASCADE"
    assert on_delete.get("web_users") == "CASCADE"


# ---- Memory full-text search ----------------------------------------------


def test_memory_fts_round_trips(schema_db):
    """The memory_fts virtual table + sync triggers are seeded : an inserted memory is
    immediately findable via MATCH (the recall path the harness depends on)."""
    has_fts = schema_db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='memory_fts'"
    ).fetchone()
    assert has_fts is not None
    cli = schema_db.execute("SELECT id FROM web_users WHERE username='cli'").fetchone()["id"]
    schema_db.execute(
        "INSERT INTO memory (scope,user_id,code,title,description,content,importance,created_at,modified_at) "
        "VALUES ('user',?,'fts-probe','Zebra','a zebra fact','the zebra runs fast',3,'2026-01-01','2026-01-01')",
        (cli,),
    )
    hit = schema_db.execute(
        "SELECT m.code FROM memory_fts f JOIN memory m ON m.id=f.rowid WHERE memory_fts MATCH 'zebra'"
    ).fetchone()
    assert hit is not None and hit["code"] == "fts-probe"


# ---- Image / vision tool grants -------------------------------------------


def test_image_search_granted(schema_db):
    """image_search is granted to the router and the web-search-specialist."""
    codes = {
        r["code"]
        for r in schema_db.execute(
            "SELECT a.code FROM agent_tools at JOIN agents a ON a.id = at.agent_id "
            "WHERE at.tool_code = 'image_search'"
        )
    }
    assert {"jean-michel", "web-search-specialist"} <= codes


def test_vision_tools_granted(schema_db):
    """analyze_image + image_fetch are granted to the router and the web-search-specialist."""
    for tool in ("analyze_image", "image_fetch"):
        codes = {
            r["code"]
            for r in schema_db.execute(
                "SELECT a.code FROM agent_tools at JOIN agents a ON a.id = at.agent_id "
                "WHERE at.tool_code = ?",
                (tool,),
            )
        }
        assert {"jean-michel", "web-search-specialist"} <= codes, tool


def test_show_images_inline_paradigm(schema_db):
    """The router gets an active 'show_images_inline' routing paradigm."""
    row = schema_db.execute(
        "SELECT 1 FROM agent_paradigms ap "
        "JOIN agents a ON a.id = ap.agent_id "
        "JOIN paradigms p ON p.id = ap.paradigm_id "
        "WHERE a.code = 'jean-michel' AND p.code = 'show_images_inline' AND p.active = 1"
    ).fetchone()
    assert row is not None


def test_paradigm_content_is_english(schema_db):
    """Prompt-facing paradigm text (content + rendered category titles) is English-only —
    the internal/inter-LLM language is English."""
    import re

    accent = re.compile(r"[àâäéèêëïîôöùûüçœ]", re.IGNORECASE)
    fr_words = {"une", "des", "vous", "avec", "cette", "montre", "voici", "dans"}
    for code, content in schema_db.execute(
        "SELECT code, content FROM paradigms WHERE active = 1"
    ):
        text = content or ""
        assert not accent.search(text), f"{code}: accented French in content"
        words = set(re.findall(r"[a-zà-ÿ]+", text.lower()))
        assert not (words & fr_words), f"{code}: French words {words & fr_words}"
    for (title,) in schema_db.execute("SELECT title FROM categories WHERE active = 1"):
        assert not accent.search(title or ""), f"non-English category title: {title!r}"


# ---- repo_* tool grants (coding agents) -----------------------------------


def test_repo_tools_granted(schema_db):
    """The repo_* tools are granted to the coding workers ; code-fetcher is external-only
    (no repo tools) ; code-analyst is the read-only analyst (reads but never edits/runs)."""

    def codes(tool):
        return {
            r["code"]
            for r in schema_db.execute(
                "SELECT a.code FROM agent_tools at JOIN agents a ON a.id = at.agent_id "
                "WHERE at.tool_code = ?",
                (tool,),
            )
        }

    for tool in ("repo_read", "repo_grep", "repo_glob", "repo_edit", "repo_write"):
        assert {"code-runner", "code-runner-node"} <= codes(tool), tool
    # code-fetcher is EXTERNAL-only : no repo tools at all.
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


def test_repo_test_granted(schema_db):
    """repo_test is granted to the workers only (the lookup agent doesn't run code), and
    the retired graphify tool grant is gone."""

    def codes(tool):
        return {
            r["code"]
            for r in schema_db.execute(
                "SELECT a.code FROM agent_tools at JOIN agents a ON a.id = at.agent_id "
                "WHERE at.tool_code = ?",
                (tool,),
            )
        }

    assert codes("repo_test") >= {"code-runner", "code-runner-node"}
    assert "code-fetcher" not in codes("repo_test")
    assert codes("repo_graph_refresh") == set()  # graphify retiré


def test_repo_git_granted_to_coding_workers(schema_db):
    """The read-only repo_git tool is granted to both coding workers."""
    agents = {
        r["code"]
        for r in schema_db.execute(
            "SELECT a.code FROM agent_tools t JOIN agents a ON a.id=t.agent_id "
            "WHERE t.tool_code='repo_git'"
        )
    }
    assert {"code-runner", "code-runner-node"} <= agents


def test_repo_exec_granted_and_doctrine_names_it(schema_db):
    """repo_exec granted to both coding workers, and the doctrine paradigm names the
    project sandbox (repo_exec) and spells out the two mount points (/app + /workspace)."""
    agents = {
        r["code"]
        for r in schema_db.execute(
            "SELECT a.code FROM agent_tools t JOIN agents a ON a.id=t.agent_id "
            "WHERE t.tool_code='repo_exec'"
        )
    }
    assert {"code-runner", "code-runner-node"} <= agents
    content = schema_db.execute(
        "SELECT content FROM paradigms WHERE code='code_space_doctrine'"
    ).fetchone()["content"]
    assert "repo_exec" in content and "PROJECT SANDBOX" in content
    assert "/app" in content and "/workspace" in content


# ---- Coding agents : missions, casts, doctrines ---------------------------


def test_critics_are_validators(schema_db):
    """critical-coder/sergent-kiss missions are framed as validators (grounded in
    sources), not creatives."""
    cc = schema_db.execute(
        "SELECT mission FROM agents WHERE code='critical-coder'"
    ).fetchone()["mission"]
    sk = schema_db.execute(
        "SELECT mission FROM agents WHERE code='sergent-kiss'"
    ).fetchone()["mission"]
    assert "Validator" in cc and "do NOT propose, design, or invent" in cc
    assert "validation gate" in sk and "never redesign" in sk


def test_todo_update_granted_to_routers(schema_db):
    """The granular todo_update tool is granted to the plan owners (the agents that
    have todo_write) — jean-michel + code-router."""
    granted = {
        r["code"]
        for r in schema_db.execute(
            "SELECT a.code FROM agent_tools t JOIN agents a ON a.id = t.agent_id "
            "WHERE t.tool_code = 'todo_update'"
        )
    }
    assert granted == {"jean-michel", "code-router"}


def test_code_analyst_cast(schema_db):
    """code-analyst is the read-only analyst cast — a code-router delegation target, and
    NOT a CODE_WORKER (so analysis never triggers the code-production deliberation)."""
    from jeanmichel.deliberation import CODE_WORKERS

    assert "code-analyst" not in CODE_WORKERS
    targets = {
        r["target_code"]
        for r in schema_db.execute(
            "SELECT target_code FROM agent_delegation_targets adt "
            "JOIN agents a ON a.id = adt.agent_id WHERE a.code = 'code-router'"
        )
    }
    assert "code-analyst" in targets
    row = schema_db.execute(
        "SELECT role, active FROM agents WHERE code = 'code-analyst'"
    ).fetchone()
    assert row is not None and row["role"] == "specialist" and int(row["active"]) == 1


_P4_CODES = {"repo_intervention_discipline", "prefer_repo_tools_over_bash"}


def test_code_paradigms_present_gated_and_bound(schema_db):
    """The two code paradigms exist, are gated to mode 'code' ONLY (anti-leak), and are
    bound to BOTH coding workers."""
    present = {
        r["code"]
        for r in schema_db.execute(
            "SELECT code FROM paradigms WHERE active = 1 AND code IN "
            "('repo_intervention_discipline','prefer_repo_tools_over_bash')"
        )
    }
    assert present == _P4_CODES
    for code in _P4_CODES:
        modes = {
            r["mode"]
            for r in schema_db.execute(
                "SELECT mode FROM paradigm_modes pm JOIN paradigms p ON p.id = pm.paradigm_id "
                "WHERE p.code = ?",
                (code,),
            )
        }
        assert modes == {"code"}, (code, modes)  # gated to code only
        agents = {
            r["code"]
            for r in schema_db.execute(
                "SELECT a.code FROM agent_paradigms ap JOIN agents a ON a.id = ap.agent_id "
                "JOIN paradigms p ON p.id = ap.paradigm_id WHERE p.code = ?",
                (code,),
            )
        }
        assert {"code-runner", "code-runner-node"} <= agents, (code, agents)


def test_code_paradigms_only_render_in_code_mode(schema_db):
    """End-to-end gating via the real loader: code-runner sees the discipline in 'code'
    mode, NOT in 'chat' (the anti-regression invariant)."""
    from jeanmichel import db as jdb

    cr = schema_db.execute("SELECT id FROM agents WHERE code='code-runner'").fetchone()["id"]
    in_code = {p.code for p in jdb.load_paradigms_for_agent(schema_db, cr, "code")}
    in_chat = {p.code for p in jdb.load_paradigms_for_agent(schema_db, cr, "chat")}
    assert "repo_intervention_discipline" in in_code
    assert "repo_intervention_discipline" not in in_chat
    assert "prefer_repo_tools_over_bash" in in_code
    assert "prefer_repo_tools_over_bash" not in in_chat


def test_code_space_doctrine_gated_and_bound(schema_db):
    """code_space_doctrine exists, leads (low order_priority), is gated to 'code' only,
    and is bound to BOTH coding workers."""
    row = schema_db.execute(
        "SELECT order_priority FROM paradigms WHERE code='code_space_doctrine' AND active=1"
    ).fetchone()
    assert row is not None and row["order_priority"] == 8  # leads behavioural paradigms
    modes = {
        r["mode"]
        for r in schema_db.execute(
            "SELECT mode FROM paradigm_modes pm JOIN paradigms p ON p.id=pm.paradigm_id "
            "WHERE p.code='code_space_doctrine'"
        )
    }
    assert modes == {"code"}
    agents = {
        r["code"]
        for r in schema_db.execute(
            "SELECT a.code FROM agent_paradigms ap JOIN agents a ON a.id=ap.agent_id "
            "JOIN paradigms p ON p.id=ap.paradigm_id WHERE p.code='code_space_doctrine'"
        )
    }
    assert {"code-runner", "code-runner-node"} <= agents


def test_workspace_tools_only_gated_out_of_code(schema_db):
    """workspace_tools_only no longer applies in code mode (the scratch is not 'the source
    of truth' when a repo is attached), but still in the others — via the real loader."""
    from jeanmichel import db as jdb

    modes = {
        r["mode"]
        for r in schema_db.execute(
            "SELECT mode FROM paradigm_modes pm JOIN paradigms p ON p.id=pm.paradigm_id "
            "WHERE p.code='workspace_tools_only'"
        )
    }
    assert modes == {"analyse", "chat", "vocal"}  # gated OUT of code
    cr = schema_db.execute("SELECT id FROM agents WHERE code='code-runner'").fetchone()["id"]
    in_code = {p.code for p in jdb.load_paradigms_for_agent(schema_db, cr, "code")}
    in_chat = {p.code for p in jdb.load_paradigms_for_agent(schema_db, cr, "chat")}
    assert "code_space_doctrine" in in_code and "code_space_doctrine" not in in_chat
    assert "workspace_tools_only" not in in_code  # the fix
    assert "workspace_tools_only" in in_chat  # non-regression


def test_git_checkpoint_discipline_gated_and_bound(schema_db):
    """git_checkpoint_discipline exists, gated to 'code' only, bound to both coding workers."""
    modes = {
        r["mode"]
        for r in schema_db.execute(
            "SELECT mode FROM paradigm_modes pm JOIN paradigms p ON p.id=pm.paradigm_id "
            "WHERE p.code='git_checkpoint_discipline'"
        )
    }
    assert modes == {"code"}
    agents = {
        r["code"]
        for r in schema_db.execute(
            "SELECT a.code FROM agent_paradigms ap JOIN agents a ON a.id=ap.agent_id "
            "JOIN paradigms p ON p.id=ap.paradigm_id WHERE p.code='git_checkpoint_discipline'"
        )
    }
    assert {"code-runner", "code-runner-node"} <= agents


# ---- Projects table -------------------------------------------------------


def test_projects_has_dockerfile_column(schema_db):
    cols = {r["name"] for r in schema_db.execute("PRAGMA table_info(projects)")}
    assert "dockerfile" in cols


def test_projects_repo_columns(schema_db):
    cols = {r["name"] for r in schema_db.execute("PRAGMA table_info(projects)")}
    assert {"code_repo", "repo_kind"} <= cols


# ---- Delegation whitelists + the dedicated code-router --------------------


def test_comparator_delegation_whitelist(schema_db):
    """comparator-specialist has an explicit delegation whitelist."""
    targets = {
        r["target_code"]
        for r in schema_db.execute(
            "SELECT target_code FROM agent_delegation_targets ad "
            "JOIN agents a ON a.id = ad.agent_id WHERE a.code = 'comparator-specialist'"
        )
    }
    assert targets == {
        "web-search-specialist",
        "wikipedia-specialist",
        "weather-specialist",
        "news-specialist",
    }


def test_code_router_agent(schema_db):
    """code-router is a router on qwen3:14b, lean paradigm set, delegating to the code workers."""
    row = schema_db.execute(
        "SELECT role, model_override FROM agents WHERE code='code-router' AND active=1"
    ).fetchone()
    assert row is not None and row["role"] == "router"
    assert row["model_override"] == "qwen3:14b"
    n_para = schema_db.execute(
        "SELECT COUNT(*) AS c FROM agent_paradigms ap JOIN agents a ON a.id=ap.agent_id "
        "WHERE a.code='code-router'"
    ).fetchone()["c"]
    assert n_para == 15
    targets = {
        r["target_code"]
        for r in schema_db.execute(
            "SELECT target_code FROM agent_delegation_targets ad JOIN agents a ON a.id=ad.agent_id "
            "WHERE a.code='code-router'"
        )
    }
    assert targets == {"code-runner", "code-runner-node", "code-fetcher", "code-analyst"}


def test_code_router_is_leaner_than_jean_michel(schema_db):
    """The whole point: code-router carries far fewer bound paradigms than the generalist
    jean-michel (focus → reliable delegation for small models)."""
    from jeanmichel.orchestrator_v2 import load_agent_spec_v2

    cr = load_agent_spec_v2(schema_db, "code-router", mode="code")
    assert cr.role == "router" and cr.model == "qwen3:14b"

    def n_bound(code):
        return schema_db.execute(
            "SELECT COUNT(*) AS c FROM agent_paradigms ap JOIN agents a ON a.id=ap.agent_id "
            "WHERE a.code=?",
            (code,),
        ).fetchone()["c"]

    assert n_bound("code-router") < n_bound("jean-michel")  # 15 vs 46


# ---- Memory read/propose split + meta-analyst -----------------------------


def test_propose_memory_granted_to_manage_memory_agents(schema_db):
    """propose_memory (the write-proposal channel) is granted to every agent that holds
    manage_memory (now read-only) ; the memory_discipline paradigm frames the read/propose
    split (no note_for/save)."""
    mm = {
        r["agent_id"]
        for r in schema_db.execute("SELECT agent_id FROM agent_tools WHERE tool_code='manage_memory'")
    }
    pm = {
        r["agent_id"]
        for r in schema_db.execute("SELECT agent_id FROM agent_tools WHERE tool_code='propose_memory'")
    }
    assert mm <= pm and len(mm) >= 1  # every memory reader can also propose
    content = schema_db.execute(
        "SELECT content FROM paradigms WHERE code='memory_discipline'"
    ).fetchone()["content"]
    assert "propose_memory" in content and "note_for" not in content


def test_meta_analyst_proposes_grounded_rules(schema_db):
    """The meta-analyst gets propose_memory and its no_self_modification doctrine routes
    improvements through grounded rule proposals (it proposes, never applies)."""
    granted = schema_db.execute(
        "SELECT 1 FROM agent_tools t JOIN agents a ON a.id=t.agent_id "
        "WHERE a.code='meta-analyst' AND t.tool_code='propose_memory'"
    ).fetchone()
    assert granted is not None
    content = schema_db.execute(
        "SELECT content FROM paradigms WHERE code='no_self_modification'"
    ).fetchone()["content"]
    assert "propose_memory" in content
