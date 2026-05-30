"""Tests for `manage_user_memory` tool + index renderer + bootstrap."""

from __future__ import annotations

import json
import sqlite3

from jeanmichel.bootstrap import bootstrap_user_memory_from_profile
from jeanmichel.config import UserProfile
from jeanmichel.db import connect as db_connect
from jeanmichel.prompts import render_user_memory_index
from jeanmichel.tools.manage_user_memory import SPEC as USER_MEMORY_SPEC
from jeanmichel.tools.manage_user_memory import _handler

# ---- Tool spec ------------------------------------------------------------


def test_spec_has_required_fields():
    assert USER_MEMORY_SPEC.name == "manage_user_memory"
    assert "action" in USER_MEMORY_SPEC.parameters["properties"]
    assert USER_MEMORY_SPEC.parameters["required"] == ["action"]
    actions = USER_MEMORY_SPEC.parameters["properties"]["action"]["enum"]
    assert set(actions) == {"save", "recall", "list", "update", "delete"}


# ---- save -----------------------------------------------------------------


def test_save_creates_entry(tmp_db_v2):
    result = json.loads(_handler(
        action="save",
        type="user",
        code="unity-montreal",
        title="Dev Unity Montreal",
        description="Senior dev at Unity in Montreal",
        content="Works on Editor team, prefers terse responses.",
    ))
    assert "error" not in result
    assert result["action"] == "save"
    assert result["entry_code"] == "unity-montreal"

    with db_connect() as conn:
        rows = conn.execute("SELECT * FROM user_memory").fetchall()
    assert len(rows) == 1
    assert rows[0]["code"] == "unity-montreal"
    assert rows[0]["title"] == "Dev Unity Montreal"


def test_save_duplicate_suggests_update(tmp_db_v2):
    _handler(
        action="save",
        type="user",
        code="x",
        title="t",
        description="d",
        content="c",
    )
    result = json.loads(_handler(
        action="save",
        type="user",
        code="x",
        title="t2",
        description="d2",
        content="c2",
    ))
    assert result["error_code"] == "already_exists"
    assert "update" in result["summary"].lower()


def test_save_invalid_type_rejected(tmp_db_v2):
    result = json.loads(_handler(
        action="save",
        type="garbage_type",
        code="x",
        title="t",
        description="d",
        content="c",
    ))
    assert result["error_code"] == "invalid_type"


def test_save_code_with_spaces_rejected(tmp_db_v2):
    result = json.loads(_handler(
        action="save",
        type="user",
        code="has spaces",
        title="t",
        description="d",
        content="c",
    ))
    assert result["error_code"] == "invalid_code"
    assert "kebab-case" in result["summary"].lower()


def test_save_title_too_long_rejected(tmp_db_v2):
    result = json.loads(_handler(
        action="save",
        type="user",
        code="x",
        title="x" * 100,  # > 60 chars
        description="d",
        content="c",
    ))
    assert result["error_code"] == "title_too_long"


def test_save_missing_required_field_rejected(tmp_db_v2):
    result = json.loads(_handler(
        action="save",
        type="user",
        code="x",
        title="t",
        description="",  # empty → rejected
        content="c",
    ))
    assert result["error_code"] == "invalid_args"


# ---- recall ---------------------------------------------------------------


def test_recall_returns_full_entry(tmp_db_v2):
    _handler(
        action="save",
        type="project",
        code="revolucion",
        title="Revolution branch",
        description="Architectural rewrite of Jean-Michel",
        content="Tier 0/1/2 with hooks + multi-turn messages.",
    )
    result = json.loads(_handler(action="recall", code="revolucion"))
    assert "error" not in result
    entry = result["entry"]
    assert entry["type"] == "project"
    assert entry["code"] == "revolucion"
    assert "Tier 0/1/2" in entry["content"]


def test_recall_not_found(tmp_db_v2):
    result = json.loads(_handler(action="recall", code="nonexistent"))
    assert result["error_code"] == "not_found"


def test_recall_no_code_arg_rejected(tmp_db_v2):
    result = json.loads(_handler(action="recall"))
    assert result["error_code"] == "invalid_args"


# ---- list -----------------------------------------------------------------


def test_list_returns_index_only_no_content(tmp_db_v2):
    _handler(
        action="save", type="user", code="a", title="A", description="a desc", content="A CONTENT BODY",
    )
    _handler(
        action="save", type="feedback", code="b", title="B", description="b desc", content="B CONTENT BODY",
    )
    result = json.loads(_handler(action="list"))
    assert result["count"] == 2
    assert len(result["entries"]) == 2
    for entry in result["entries"]:
        # `content` must NOT be present in list output (only in recall).
        assert "content" not in entry
        # Index fields are present.
        assert {"id", "type", "code", "title", "description", "modified_at"} <= set(entry.keys())


def test_list_filters_by_type(tmp_db_v2):
    _handler(action="save", type="user", code="u1", title="t", description="d", content="c")
    _handler(action="save", type="feedback", code="f1", title="t", description="d", content="c")
    _handler(action="save", type="feedback", code="f2", title="t", description="d", content="c")

    result = json.loads(_handler(action="list", type="feedback"))
    assert result["count"] == 2
    assert all(e["type"] == "feedback" for e in result["entries"])


def test_list_orders_by_modified_at_desc(tmp_db_v2):
    import time
    _handler(action="save", type="user", code="old", title="t", description="d", content="c")
    time.sleep(1.1)  # ensure modified_at differs at second resolution
    _handler(action="save", type="user", code="new", title="t", description="d", content="c")

    result = json.loads(_handler(action="list"))
    codes = [e["code"] for e in result["entries"]]
    assert codes[0] == "new"
    assert codes[1] == "old"


# ---- update ---------------------------------------------------------------


def test_update_modifies_fields(tmp_db_v2):
    _handler(
        action="save", type="user", code="x", title="old title",
        description="old desc", content="old content",
    )
    result = json.loads(_handler(
        action="update", code="x", title="new title",
    ))
    assert "error" not in result
    recall = json.loads(_handler(action="recall", code="x"))
    assert recall["entry"]["title"] == "new title"
    # Other fields untouched.
    assert recall["entry"]["description"] == "old desc"
    assert recall["entry"]["content"] == "old content"


def test_update_requires_at_least_one_field(tmp_db_v2):
    _handler(action="save", type="user", code="x", title="t", description="d", content="c")
    result = json.loads(_handler(action="update", code="x"))
    assert result["error_code"] == "invalid_args"


def test_update_not_found(tmp_db_v2):
    result = json.loads(_handler(action="update", code="ghost", title="t"))
    assert result["error_code"] == "not_found"


def test_update_with_type_disambiguates(tmp_db_v2):
    # Two entries share the code 'x' across different types.
    _handler(action="save", type="user", code="x", title="t", description="d", content="c")
    _handler(action="save", type="feedback", code="x", title="t", description="d", content="c")

    # Without type → ambiguous.
    bad = json.loads(_handler(action="update", code="x", title="new"))
    assert bad["error_code"] == "ambiguous"

    # With type → OK.
    good = json.loads(_handler(action="update", code="x", type="user", title="user-new"))
    assert "error" not in good


# ---- delete ---------------------------------------------------------------


def test_delete_removes_entry(tmp_db_v2):
    _handler(action="save", type="user", code="x", title="t", description="d", content="c")
    result = json.loads(_handler(action="delete", code="x"))
    assert "error" not in result

    recall = json.loads(_handler(action="recall", code="x"))
    assert recall["error_code"] == "not_found"


def test_delete_not_found(tmp_db_v2):
    result = json.loads(_handler(action="delete", code="ghost"))
    assert result["error_code"] == "not_found"


# ---- invalid action -------------------------------------------------------


def test_invalid_action_rejected(tmp_db_v2):
    result = json.loads(_handler(action="evict"))
    assert result["error_code"] == "invalid_action"


# ---- render_user_memory_index --------------------------------------------


def test_render_index_empty_table_returns_empty_string(tmp_db_v2):
    with db_connect() as conn:
        block, count = render_user_memory_index(conn)
    assert block == ""
    assert count == 0


def test_render_index_with_entries(tmp_db_v2):
    _handler(
        action="save", type="user", code="unity-montreal",
        title="Dev Unity", description="senior dev unity montreal", content="c",
    )
    _handler(
        action="save", type="feedback", code="kiss-religieux",
        title="KISS", description="prefers terse direct answers", content="c",
    )
    with db_connect() as conn:
        block, count = render_user_memory_index(conn)
    assert count == 2
    assert "## Known facts about the user" in block
    assert "[user] unity-montreal" in block
    assert "[feedback] kiss-religieux" in block
    # Description appears in the index.
    assert "senior dev unity montreal" in block


def test_render_index_warns_near_capacity(tmp_db_v2):
    # Insert 90 entries (>= warn_at default).
    for i in range(90):
        _handler(
            action="save", type="user", code=f"e{i:03d}",
            title=f"t{i}", description=f"d{i}", content="c",
        )
    with db_connect() as conn:
        block, count = render_user_memory_index(conn)
    assert count == 90
    assert "Memory near capacity" in block
    assert "Purge obsolete" in block


def test_render_index_below_warn_threshold_has_no_warning(tmp_db_v2):
    for i in range(5):
        _handler(
            action="save", type="user", code=f"e{i}",
            title="t", description="d", content="c",
        )
    with db_connect() as conn:
        block, count = render_user_memory_index(conn)
    assert count == 5
    assert "Memory near capacity" not in block


def test_render_index_table_missing_returns_empty(tmp_path):
    """If migrate_101 hasn't been applied, the renderer gracefully degrades."""
    db_path = tmp_path / "no_user_memory.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # No user_memory table at all.
    block, count = render_user_memory_index(conn)
    conn.close()
    assert block == ""
    assert count == 0


def test_render_index_truncates_to_limit(tmp_db_v2):
    # Insert 5 entries, render with limit=2 → should only see 2.
    for i in range(5):
        _handler(
            action="save", type="user", code=f"e{i}",
            title="t", description=f"d{i}", content="c",
        )
    with db_connect() as conn:
        block, count = render_user_memory_index(conn, limit=2, warn_at=999)
    assert count == 5  # full count
    # But block only contains 2 entries.
    assert block.count("- [") == 2


# ---- bootstrap_user_memory_from_profile ----------------------------------


def test_bootstrap_creates_entry_from_profile(tmp_db_v2):
    profile = UserProfile(
        name="Jeremy",
        city="Montreal",
        country="Canada",
        language="fr",
        notes="Senior dev at Unity, prefers terse direct answers.",
    )
    with db_connect() as conn:
        created = bootstrap_user_memory_from_profile(conn, profile)
    assert created is True

    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM user_memory WHERE code='personal-profile'"
        ).fetchall()
    assert len(rows) == 1
    entry = rows[0]
    assert entry["type"] == "user"
    assert "Senior dev at Unity" in entry["description"]
    assert "Jeremy" in entry["content"]


def test_bootstrap_idempotent(tmp_db_v2):
    profile = UserProfile(name="X", notes="some user")
    with db_connect() as conn:
        first = bootstrap_user_memory_from_profile(conn, profile)
        second = bootstrap_user_memory_from_profile(conn, profile)
    assert first is True
    assert second is False  # second call no-op

    with db_connect() as conn:
        rows = conn.execute("SELECT COUNT(*) AS c FROM user_memory").fetchone()
    assert rows["c"] == 1  # still just one entry


def test_bootstrap_empty_profile_skips(tmp_db_v2):
    profile = UserProfile()  # all fields empty
    with db_connect() as conn:
        created = bootstrap_user_memory_from_profile(conn, profile)
    assert created is False

    with db_connect() as conn:
        rows = conn.execute("SELECT COUNT(*) AS c FROM user_memory").fetchone()
    assert rows["c"] == 0


def test_bootstrap_table_missing_skips(tmp_path):
    """Without migrate_101, the table doesn't exist → bootstrap returns False quietly."""
    db_path = tmp_path / "no_table.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    profile = UserProfile(notes="dev")
    created = bootstrap_user_memory_from_profile(conn, profile)
    conn.close()
    assert created is False


def test_bootstrap_skips_when_table_already_populated(tmp_db_v2):
    """If the user manually added an entry, bootstrap doesn't overwrite."""
    _handler(
        action="save", type="user", code="manual",
        title="Manual entry", description="something the user added",
        content="c",
    )
    profile = UserProfile(notes="some bootstrap")
    with db_connect() as conn:
        created = bootstrap_user_memory_from_profile(conn, profile)
    assert created is False


# ---- Hard limits caught at save time -------------------------------------


def test_save_description_at_limit_accepted(tmp_db_v2):
    desc = "x" * 150  # exactly at limit
    result = json.loads(_handler(
        action="save", type="user", code="x", title="t",
        description=desc, content="c",
    ))
    assert "error" not in result


def test_save_description_over_limit_rejected(tmp_db_v2):
    desc = "x" * 151
    result = json.loads(_handler(
        action="save", type="user", code="x", title="t",
        description=desc, content="c",
    ))
    assert result["error_code"] == "description_too_long"


def test_save_content_over_limit_rejected(tmp_db_v2):
    content = "x" * 1_001
    result = json.loads(_handler(
        action="save", type="user", code="x", title="t",
        description="d", content=content,
    ))
    assert result["error_code"] == "content_too_long"
