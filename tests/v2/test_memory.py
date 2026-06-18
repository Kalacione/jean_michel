"""Tests for memory : the read-only `manage_memory` tool (recall/search/list) + the
`service.memory` write/validation layer (save/update/delete — no longer reachable through
the tool, which is read-only ; writes flow through propose_memory → human review) + the
deterministic memory-block renderer + bootstrap. Memory has a single `scope` dimension
(user/project/tool) ; FTS5/BM25 powers search."""

from __future__ import annotations

import json
import sqlite3

import pytest

from jeanmichel.bootstrap import bootstrap_user_memory_from_profile
from jeanmichel.config import UserProfile
from jeanmichel.db import cli_user_id
from jeanmichel.db import connect as db_connect
from jeanmichel.prompts import render_memory_block
from jeanmichel.service import memory
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


def _save_user(code: str, title: str = "t", description: str = "d", content: str = "c") -> None:
    """Seed a user-scope memory directly through the service (the tool no longer writes)."""
    with db_connect() as conn:
        memory.save(conn, scope="user", code=code, title=title, description=description,
                    content=content, user_id=cli_user_id(conn))


# ---- Tool spec (read-only) ------------------------------------------------


def test_spec_is_read_only():
    assert MEMORY_SPEC.name == "manage_memory"
    assert MEMORY_SPEC.parameters["required"] == ["action"]
    actions = set(MEMORY_SPEC.parameters["properties"]["action"]["enum"])
    assert actions == {"recall", "search", "list"}  # writes go through propose_memory


def test_tool_rejects_write_actions(tmp_db_v2):
    for act in ("save", "update", "delete", "note_for_user"):
        result = json.loads(_handler(action=act, scope="user", code="x"))
        assert result["error_code"] == "invalid_action"


def test_invalid_action_rejected(tmp_db_v2):
    result = json.loads(_handler(action="evict"))
    assert result["error_code"] == "invalid_action"


# ---- service.memory : save (scopes + validation) --------------------------


def test_save_user_scope(tmp_db_v2):
    with db_connect() as conn:
        saved = memory.save(conn, scope="user", code="unity-montreal",
                            title="Dev Unity Montreal", description="Senior dev at Unity in Montreal",
                            content="Works on Editor team.", user_id=cli_user_id(conn))
    assert saved["scope"] == "user" and saved["code"] == "unity-montreal"
    with db_connect() as conn:
        rows = conn.execute("SELECT * FROM memory").fetchall()
    assert len(rows) == 1
    assert rows[0]["scope"] == "user" and rows[0]["user_id"] == _uid()


def test_save_tool_scope(tmp_db_v2):
    with db_connect() as conn:
        memory.save(conn, scope="tool", code="latlong-beats-city", title="Prefer lat/long",
                    description="lat/long is more reliable than city name",
                    content="Pass coordinates when known.", tool_code="weather")
    with db_connect() as conn:
        row = conn.execute("SELECT scope, tool_code FROM memory").fetchone()
    assert row["scope"] == "tool" and row["tool_code"] == "weather"


def test_save_project_scope(tmp_db_v2):
    pid = _make_project()
    with db_connect() as conn:
        memory.save(conn, scope="project", code="decision-1", title="Use FTS5",
                    description="Chose FTS5 over embeddings", content="KISS.", project_id=pid)
    with db_connect() as conn:
        row = conn.execute("SELECT scope, project_id FROM memory").fetchone()
    assert row["scope"] == "project" and row["project_id"] == pid


def test_save_project_without_target_rejected(tmp_db_v2):
    with db_connect() as conn, pytest.raises(memory.MemoryOpError) as e:
        memory.save(conn, scope="project", code="x", title="t", description="d", content="c")
    assert e.value.code == "invalid_args"


def test_save_tool_without_tool_code_rejected(tmp_db_v2):
    with db_connect() as conn, pytest.raises(memory.MemoryOpError) as e:
        memory.save(conn, scope="tool", code="x", title="t", description="d", content="c")
    assert e.value.code == "invalid_args"


def test_save_duplicate_rejected(tmp_db_v2):
    _save_user("x")
    with db_connect() as conn, pytest.raises(memory.MemoryOpError) as e:
        memory.save(conn, scope="user", code="x", title="t2", description="d2", content="c2",
                    user_id=cli_user_id(conn))
    assert e.value.code == "already_exists"


def test_same_code_different_scope_coexists(tmp_db_v2):
    with db_connect() as conn:
        memory.save(conn, scope="tool", code="kiss", title="t", description="d", content="c",
                    tool_code="weather")
        memory.save(conn, scope="user", code="kiss", title="t", description="d", content="c",
                    user_id=cli_user_id(conn))
        n = conn.execute("SELECT COUNT(*) AS c FROM memory WHERE code='kiss'").fetchone()["c"]
    assert n == 2


def test_save_invalid_scope_rejected(tmp_db_v2):
    with db_connect() as conn, pytest.raises(memory.MemoryOpError) as e:
        memory.save(conn, scope="galaxy", code="x", title="t", description="d", content="c")
    assert e.value.code == "invalid_scope"


def test_save_code_with_spaces_rejected(tmp_db_v2):
    with db_connect() as conn, pytest.raises(memory.MemoryOpError) as e:
        memory.save(conn, scope="user", code="has spaces", title="t", description="d",
                    content="c", user_id=cli_user_id(conn))
    assert e.value.code == "invalid_code"


def test_save_title_too_long_rejected(tmp_db_v2):
    with db_connect() as conn, pytest.raises(memory.MemoryOpError) as e:
        memory.save(conn, scope="user", code="x", title="x" * 100, description="d",
                    content="c", user_id=cli_user_id(conn))
    assert e.value.code == "title_too_long"


def test_save_missing_required_field_rejected(tmp_db_v2):
    with db_connect() as conn, pytest.raises(memory.MemoryOpError) as e:
        memory.save(conn, scope="user", code="x", title="t", description="",
                    content="c", user_id=cli_user_id(conn))
    assert e.value.code == "invalid_args"


def test_save_description_at_limit_accepted(tmp_db_v2):
    with db_connect() as conn:
        saved = memory.save(conn, scope="user", code="x", title="t", description="x" * 150,
                            content="c", user_id=cli_user_id(conn))
    assert saved["code"] == "x"


def test_save_content_over_limit_rejected(tmp_db_v2):
    with db_connect() as conn, pytest.raises(memory.MemoryOpError) as e:
        memory.save(conn, scope="user", code="x", title="t", description="d",
                    content="x" * 1001, user_id=cli_user_id(conn))
    assert e.value.code == "content_too_long"


# ---- service.memory : update / delete -------------------------------------


def test_update_modifies_fields(tmp_db_v2):
    _save_user("x", title="old", description="od", content="oc")
    with db_connect() as conn:
        memory.update(conn, scope="user", code="x", title="new", user_id=cli_user_id(conn))
    result = json.loads(_handler(action="recall", scope="user", code="x"))
    assert result["entry"]["title"] == "new"
    assert result["entry"]["description"] == "od"  # untouched


def test_update_requires_a_field(tmp_db_v2):
    _save_user("x")
    with db_connect() as conn, pytest.raises(memory.MemoryOpError) as e:
        memory.update(conn, scope="user", code="x", user_id=cli_user_id(conn))
    assert e.value.code == "invalid_args"


def test_update_not_found(tmp_db_v2):
    with db_connect() as conn, pytest.raises(memory.MemoryOpError) as e:
        memory.update(conn, scope="user", code="ghost", title="t", user_id=cli_user_id(conn))
    assert e.value.code == "not_found"


def test_delete_removes_entry(tmp_db_v2):
    _save_user("x")
    with db_connect() as conn:
        memory.delete(conn, scope="user", code="x", user_id=cli_user_id(conn))
    assert json.loads(_handler(action="recall", scope="user", code="x"))["error_code"] == "not_found"


def test_delete_syncs_fts(tmp_db_v2):
    _save_user("x", title="findme", description="findme token", content="findme")
    with db_connect() as conn:
        memory.delete(conn, scope="user", code="x", user_id=cli_user_id(conn))
    result = json.loads(_handler(action="search", query="findme"))
    assert result["count"] == 0  # FTS trigger removed it


# ---- recall (tool) --------------------------------------------------------


def test_recall_returns_full_entry(tmp_db_v2):
    _save_user("revolucion", title="Revolution", description="rewrite", content="Tier 0/1/2 with hooks.")
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


# ---- search (FTS5 + BM25, tool) -------------------------------------------


def test_search_ranks_by_relevance(tmp_db_v2):
    _save_user("pancake-note", title="Pancakes", description="pancake recipe pancake",
               content="pancakes pancakes pancakes")
    _save_user("waffle-note", title="Waffles", description="waffle recipe",
               content="a single pancake mention")
    result = json.loads(_handler(action="search", query="pancake"))
    assert result["count"] >= 1
    assert result["results"][0]["code"] == "pancake-note"
    assert "content" in result["results"][0]  # search returns full rows


def test_search_only_matches_query_terms(tmp_db_v2):
    _save_user("a", title="alpha", description="about cats", content="x")
    _save_user("b", title="beta", description="about dogs", content="y")
    result = json.loads(_handler(action="search", query="cats"))
    assert [r["code"] for r in result["results"]] == ["a"]  # 'dogs' entry not returned


def test_search_requires_query(tmp_db_v2):
    result = json.loads(_handler(action="search"))
    assert result["error_code"] == "invalid_args"


def test_search_scope_filter(tmp_db_v2):
    with db_connect() as conn:
        memory.save(conn, scope="tool", code="w", title="t", description="shared widget",
                    content="c", tool_code="weather")
        memory.save(conn, scope="user", code="u", title="t", description="user widget",
                    content="c", user_id=cli_user_id(conn))
    result = json.loads(_handler(action="search", query="widget", scope="tool", tool_code="weather"))
    assert [r["code"] for r in result["results"]] == ["w"]


# ---- list (tool) ----------------------------------------------------------


def test_list_index_only_no_content(tmp_db_v2):
    _save_user("a", title="A", description="a", content="BODY")
    result = json.loads(_handler(action="list", scope="user"))
    assert result["count"] == 1
    e = result["results"] if "results" in result else result["entries"]
    assert "content" not in e[0]
    assert {"scope", "code", "title", "description"} <= set(e[0].keys())


def test_list_default_spans_visible_scopes(tmp_db_v2):
    pid = _make_project()
    with db_connect() as conn:
        memory.save(conn, scope="project", code="p", title="t", description="d", content="c",
                    project_id=pid)
    _save_user("u")
    result = json.loads(_handler(action="list", project_id=pid))
    scopes = {e["scope"] for e in result["entries"]}
    assert scopes == {"project", "user"}


# ---- render_memory_block (deterministic, scope-driven) --------------------


def test_render_empty_returns_empty(tmp_db_v2):
    with db_connect() as conn:
        block, count = render_memory_block(conn)
    assert block == ""
    assert count == 0


def test_render_user_and_project_sections(tmp_db_v2):
    pid = _make_project()
    with db_connect() as conn:
        memory.save(conn, scope="project", code="proj-fact", title="t",
                    description="project decision x", content="c", project_id=pid)
    _save_user("unity-montreal", description="senior dev unity")
    with db_connect() as conn:
        block, count = render_memory_block(conn, user_id=_uid(), project_id=pid)
    assert "## Project context" in block
    assert "## Known facts about the user" in block
    assert "proj-fact : project decision x" in block
    assert "unity-montreal : senior dev unity" in block
    assert count == 1  # user-scope count only


def test_render_project_only_when_project_id(tmp_db_v2):
    pid = _make_project()
    with db_connect() as conn:
        memory.save(conn, scope="project", code="dec", title="t", description="project decision",
                    content="c", project_id=pid)
    with db_connect() as conn:
        block_no, _ = render_memory_block(conn, user_id=_uid())
    assert "## Project context" not in block_no
    with db_connect() as conn:
        block_yes, _ = render_memory_block(conn, user_id=_uid(), project_id=pid)
    assert "## Project context" in block_yes
    assert "dec : project decision" in block_yes


def test_render_tool_notes_only_for_granted_tools(tmp_db_v2):
    with db_connect() as conn:
        memory.save(conn, scope="tool", code="wtip", title="t", description="weather tip",
                    content="c", tool_code="weather")
        memory.save(conn, scope="tool", code="stip", title="t", description="search tip",
                    content="c", tool_code="web_search")
    with db_connect() as conn:
        block, _ = render_memory_block(conn, user_id=_uid(), tool_codes={"weather"})
    assert "## Tool notes" in block
    assert "wtip : weather tip" in block
    assert "stip : search tip" not in block


def test_render_no_tool_section_without_grants(tmp_db_v2):
    with db_connect() as conn:
        memory.save(conn, scope="tool", code="wtip", title="t", description="weather tip",
                    content="c", tool_code="weather")
    with db_connect() as conn:
        block, _ = render_memory_block(conn, user_id=_uid(), tool_codes=frozenset())
    assert "## Tool notes" not in block


def test_render_warns_near_capacity(tmp_db_v2):
    for i in range(90):
        _save_user(f"e{i:03d}")
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
