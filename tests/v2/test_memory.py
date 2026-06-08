"""Tests for the scoped `manage_memory` tool + FTS search + memory block renderer
+ bootstrap. Memory has a single `scope` dimension (world/user/project/tool) that
drives deterministic prompt inclusion ; FTS5/BM25 powers search."""

from __future__ import annotations

import json
import sqlite3

from jeanmichel.bootstrap import bootstrap_user_memory_from_profile
from jeanmichel.config import UserProfile
from jeanmichel.db import cli_user_id
from jeanmichel.db import connect as db_connect
from jeanmichel.prompts import render_memory_block
from jeanmichel.tools.manage_memory import SPEC as MEMORY_SPEC
from jeanmichel.tools.manage_memory import _handler


def _uid() -> int:
    with db_connect() as conn:
        return cli_user_id(conn)


def _make_project(code: str = "alpha", name: str = "Alpha") -> int:
    """Insert a project owned by the cli user, return its id."""
    with db_connect() as conn:
        cur = conn.execute(
            "INSERT INTO projects (user_id, code, name, created_at, modified_at) "
            "VALUES (?, ?, ?, datetime('now'), datetime('now'))",
            (cli_user_id(conn), code, name),
        )
        return cur.lastrowid


# ---- Tool spec ------------------------------------------------------------


def test_spec_has_required_fields():
    assert MEMORY_SPEC.name == "manage_memory"
    assert MEMORY_SPEC.parameters["required"] == ["action"]
    actions = set(MEMORY_SPEC.parameters["properties"]["action"]["enum"])
    assert {"save", "recall", "search", "list", "update", "delete"} <= actions
    assert {"note_for_world", "note_for_user", "note_for_project", "note_for_tool"} <= actions


# ---- save (scopes) --------------------------------------------------------


def test_save_user_scope(tmp_db_v2):
    result = json.loads(_handler(
        action="save", scope="user", code="unity-montreal",
        title="Dev Unity Montreal", description="Senior dev at Unity in Montreal",
        content="Works on Editor team.",
    ))
    assert "error" not in result
    assert result["scope"] == "user"
    assert result["entry_code"] == "unity-montreal"
    with db_connect() as conn:
        rows = conn.execute("SELECT * FROM memory").fetchall()
    assert len(rows) == 1
    assert rows[0]["scope"] == "user"
    assert rows[0]["user_id"] == _uid()


def test_save_world_scope_no_target(tmp_db_v2):
    result = json.loads(_handler(
        action="note_for_world", code="fact", title="Fact",
        description="A global fact", content="Applies everywhere.",
    ))
    assert "error" not in result
    with db_connect() as conn:
        row = conn.execute("SELECT scope, user_id, project_id, tool_code FROM memory").fetchone()
    assert row["scope"] == "world"
    assert row["user_id"] is None and row["project_id"] is None and row["tool_code"] is None


def test_save_tool_scope(tmp_db_v2):
    result = json.loads(_handler(
        action="note_for_tool", tool_code="weather", code="latlong-beats-city",
        title="Prefer lat/long", description="lat/long is more reliable than city name",
        content="Pass coordinates when known.",
    ))
    assert "error" not in result
    with db_connect() as conn:
        row = conn.execute("SELECT scope, tool_code FROM memory").fetchone()
    assert row["scope"] == "tool"
    assert row["tool_code"] == "weather"


def test_save_project_scope(tmp_db_v2):
    pid = _make_project()
    result = json.loads(_handler(
        action="save", scope="project", code="decision-1",
        title="Use FTS5", description="Chose FTS5 over embeddings",
        content="KISS.", project_id=pid,
    ))
    assert "error" not in result
    with db_connect() as conn:
        row = conn.execute("SELECT scope, project_id FROM memory").fetchone()
    assert row["scope"] == "project"
    assert row["project_id"] == pid


def test_save_project_scope_without_project_rejected(tmp_db_v2):
    result = json.loads(_handler(
        action="save", scope="project", code="x", title="t",
        description="d", content="c",
    ))
    assert result["error_code"] == "no_project"


def test_save_tool_scope_without_tool_code_rejected(tmp_db_v2):
    result = json.loads(_handler(
        action="save", scope="tool", code="x", title="t", description="d", content="c",
    ))
    assert result["error_code"] == "invalid_args"


def test_save_duplicate_suggests_update(tmp_db_v2):
    _handler(action="save", scope="user", code="x", title="t", description="d", content="c")
    result = json.loads(_handler(
        action="save", scope="user", code="x", title="t2", description="d2", content="c2",
    ))
    assert result["error_code"] == "already_exists"
    assert "update" in result["summary"].lower()


def test_same_code_different_scope_coexists(tmp_db_v2):
    a = json.loads(_handler(action="note_for_world", code="kiss", title="t", description="d", content="c"))
    b = json.loads(_handler(action="note_for_user", code="kiss", title="t", description="d", content="c"))
    assert "error" not in a and "error" not in b
    with db_connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM memory WHERE code='kiss'").fetchone()["c"]
    assert n == 2


def test_save_invalid_scope_rejected(tmp_db_v2):
    result = json.loads(_handler(
        action="save", scope="galaxy", code="x", title="t", description="d", content="c",
    ))
    assert result["error_code"] == "invalid_scope"


def test_save_code_with_spaces_rejected(tmp_db_v2):
    result = json.loads(_handler(
        action="save", scope="user", code="has spaces", title="t", description="d", content="c",
    ))
    assert result["error_code"] == "invalid_code"


def test_save_title_too_long_rejected(tmp_db_v2):
    result = json.loads(_handler(
        action="save", scope="user", code="x", title="x" * 100, description="d", content="c",
    ))
    assert result["error_code"] == "title_too_long"


def test_save_missing_required_field_rejected(tmp_db_v2):
    result = json.loads(_handler(
        action="save", scope="user", code="x", title="t", description="", content="c",
    ))
    assert result["error_code"] == "invalid_args"


# ---- recall ---------------------------------------------------------------


def test_recall_returns_full_entry(tmp_db_v2):
    _handler(action="save", scope="user", code="revolucion", title="Revolution",
             description="rewrite", content="Tier 0/1/2 with hooks.")
    result = json.loads(_handler(action="recall", scope="user", code="revolucion"))
    assert "error" not in result
    assert result["entry"]["code"] == "revolucion"
    assert "Tier 0/1/2" in result["entry"]["content"]


def test_recall_not_found(tmp_db_v2):
    result = json.loads(_handler(action="recall", scope="user", code="nope"))
    assert result["error_code"] == "not_found"


def test_recall_requires_scope(tmp_db_v2):
    result = json.loads(_handler(action="recall", code="x"))
    assert result["error_code"] == "invalid_args"


# ---- search (FTS5 + BM25) -------------------------------------------------


def test_search_ranks_by_relevance(tmp_db_v2):
    _handler(action="save", scope="user", code="pancake-note", title="Pancakes",
             description="pancake recipe pancake", content="pancakes pancakes pancakes")
    _handler(action="save", scope="user", code="waffle-note", title="Waffles",
             description="waffle recipe", content="a single pancake mention")
    result = json.loads(_handler(action="search", query="pancake"))
    assert result["count"] >= 1
    # The pancake-heavy entry ranks first.
    assert result["results"][0]["code"] == "pancake-note"
    assert "content" in result["results"][0]  # search returns full rows


def test_search_only_matches_query_terms(tmp_db_v2):
    _handler(action="save", scope="user", code="a", title="alpha", description="about cats", content="x")
    _handler(action="save", scope="user", code="b", title="beta", description="about dogs", content="y")
    result = json.loads(_handler(action="search", query="cats"))
    codes = [r["code"] for r in result["results"]]
    assert codes == ["a"]  # 'dogs' entry not returned at all


def test_search_requires_query(tmp_db_v2):
    result = json.loads(_handler(action="search"))
    assert result["error_code"] == "invalid_args"


def test_search_scope_filter(tmp_db_v2):
    _handler(action="note_for_world", code="w", title="t", description="shared widget", content="c")
    _handler(action="note_for_user", code="u", title="t", description="user widget", content="c")
    result = json.loads(_handler(action="search", query="widget", scope="world"))
    codes = [r["code"] for r in result["results"]]
    assert codes == ["w"]


# ---- list -----------------------------------------------------------------


def test_list_index_only_no_content(tmp_db_v2):
    _handler(action="save", scope="user", code="a", title="A", description="a", content="BODY")
    result = json.loads(_handler(action="list", scope="user"))
    assert result["count"] == 1
    e = result["results"] if "results" in result else result["entries"]
    assert "content" not in e[0]
    assert {"scope", "code", "title", "description"} <= set(e[0].keys())


def test_list_default_spans_visible_scopes(tmp_db_v2):
    _handler(action="note_for_world", code="w", title="t", description="d", content="c")
    _handler(action="note_for_user", code="u", title="t", description="d", content="c")
    result = json.loads(_handler(action="list"))
    scopes = {e["scope"] for e in result["entries"]}
    assert scopes == {"world", "user"}


# ---- update / delete ------------------------------------------------------


def test_update_modifies_fields(tmp_db_v2):
    _handler(action="save", scope="user", code="x", title="old", description="od", content="oc")
    result = json.loads(_handler(action="update", scope="user", code="x", title="new"))
    assert "error" not in result
    recall = json.loads(_handler(action="recall", scope="user", code="x"))
    assert recall["entry"]["title"] == "new"
    assert recall["entry"]["description"] == "od"  # untouched


def test_update_requires_a_field(tmp_db_v2):
    _handler(action="save", scope="user", code="x", title="t", description="d", content="c")
    result = json.loads(_handler(action="update", scope="user", code="x"))
    assert result["error_code"] == "invalid_args"


def test_update_not_found(tmp_db_v2):
    result = json.loads(_handler(action="update", scope="user", code="ghost", title="t"))
    assert result["error_code"] == "not_found"


def test_delete_removes_entry(tmp_db_v2):
    _handler(action="save", scope="user", code="x", title="t", description="d", content="c")
    assert "error" not in json.loads(_handler(action="delete", scope="user", code="x"))
    assert json.loads(_handler(action="recall", scope="user", code="x"))["error_code"] == "not_found"


def test_delete_syncs_fts(tmp_db_v2):
    _handler(action="save", scope="user", code="x", title="findme", description="findme token", content="findme")
    _handler(action="delete", scope="user", code="x")
    result = json.loads(_handler(action="search", query="findme"))
    assert result["count"] == 0  # FTS trigger removed it


def test_invalid_action_rejected(tmp_db_v2):
    result = json.loads(_handler(action="evict"))
    assert result["error_code"] == "invalid_action"


# ---- render_memory_block (deterministic, scope-driven) --------------------


def test_render_empty_returns_empty(tmp_db_v2):
    with db_connect() as conn:
        block, count = render_memory_block(conn)
    assert block == ""
    assert count == 0


def test_render_world_and_user_sections(tmp_db_v2):
    _handler(action="note_for_world", code="global-fact", title="t", description="shared everywhere", content="c")
    _handler(action="note_for_user", code="unity-montreal", title="t", description="senior dev unity", content="c")
    with db_connect() as conn:
        block, count = render_memory_block(conn, user_id=_uid())
    assert "## World knowledge" in block
    assert "## Known facts about the user" in block
    assert "global-fact : shared everywhere" in block
    assert "unity-montreal : senior dev unity" in block
    assert count == 1  # user-scope count only


def test_render_project_only_when_project_id(tmp_db_v2):
    pid = _make_project()
    _handler(action="save", scope="project", code="dec", title="t", description="project decision", content="c", project_id=pid)
    # Without project_id → no project section.
    with db_connect() as conn:
        block_no, _ = render_memory_block(conn, user_id=_uid())
    assert "## Project context" not in block_no
    # With project_id → section appears.
    with db_connect() as conn:
        block_yes, _ = render_memory_block(conn, user_id=_uid(), project_id=pid)
    assert "## Project context" in block_yes
    assert "dec : project decision" in block_yes


def test_render_tool_notes_only_for_granted_tools(tmp_db_v2):
    _handler(action="note_for_tool", tool_code="weather", code="wtip", title="t", description="weather tip", content="c")
    _handler(action="note_for_tool", tool_code="web_search", code="stip", title="t", description="search tip", content="c")
    # Agent granted only 'weather' → only the weather note is injected.
    with db_connect() as conn:
        block, _ = render_memory_block(conn, user_id=_uid(), tool_codes={"weather"})
    assert "## Tool notes" in block
    assert "wtip : weather tip" in block
    assert "stip : search tip" not in block


def test_render_no_tool_section_without_grants(tmp_db_v2):
    _handler(action="note_for_tool", tool_code="weather", code="wtip", title="t", description="weather tip", content="c")
    with db_connect() as conn:
        block, _ = render_memory_block(conn, user_id=_uid(), tool_codes=frozenset())
    assert "## Tool notes" not in block


def test_render_warns_near_capacity(tmp_db_v2):
    for i in range(90):
        _handler(action="save", scope="user", code=f"e{i:03d}", title="t", description="d", content="c")
    with db_connect() as conn:
        block, count = render_memory_block(conn, user_id=_uid())
    assert count == 90
    assert "near capacity" in block


def test_render_table_missing_returns_empty(tmp_path):
    db_path = tmp_path / "no_memory.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    block, count = render_memory_block(conn)
    conn.close()
    assert block == ""
    assert count == 0


# ---- bootstrap ------------------------------------------------------------


def test_bootstrap_creates_user_scope_entry(tmp_db_v2):
    profile = UserProfile(name="Jeremy", city="Montreal", country="Canada",
                          language="fr", notes="Senior dev at Unity.")
    with db_connect() as conn:
        created = bootstrap_user_memory_from_profile(conn, profile)
    assert created is True
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM memory WHERE code='personal-profile'").fetchone()
    assert row["scope"] == "user"
    assert row["user_id"] == _uid()
    assert "Senior dev at Unity" in row["description"]
    assert "Jeremy" in row["content"]


def test_bootstrap_idempotent(tmp_db_v2):
    profile = UserProfile(name="X", notes="some user")
    with db_connect() as conn:
        first = bootstrap_user_memory_from_profile(conn, profile)
        second = bootstrap_user_memory_from_profile(conn, profile)
    assert first is True and second is False
    with db_connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM memory").fetchone()["c"] == 1


def test_bootstrap_empty_profile_skips(tmp_db_v2):
    with db_connect() as conn:
        created = bootstrap_user_memory_from_profile(conn, UserProfile())
    assert created is False


# ---- hard limits ----------------------------------------------------------


def test_save_description_at_limit_accepted(tmp_db_v2):
    result = json.loads(_handler(
        action="save", scope="user", code="x", title="t", description="x" * 150, content="c",
    ))
    assert "error" not in result


def test_save_content_over_limit_rejected(tmp_db_v2):
    result = json.loads(_handler(
        action="save", scope="user", code="x", title="t", description="d", content="x" * 1001,
    ))
    assert result["error_code"] == "content_too_long"
