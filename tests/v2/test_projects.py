"""Tests for projects : DB helpers + service CRUD + conversation attachment +
project-scope memory injection."""

from __future__ import annotations

import pytest

from jeanmichel.db import cli_user_id
from jeanmichel.db import connect as db_connect
from jeanmichel.prompts import render_memory_block
from jeanmichel.service import conversation as conversation_svc
from jeanmichel.service import memory
from jeanmichel.service import project as project_svc


def _uid() -> int:
    with db_connect() as conn:
        return cli_user_id(conn)


# ---- service CRUD ---------------------------------------------------------


def test_create_and_get(tmp_db_v2):
    with db_connect() as conn:
        proj = project_svc.create(conn, user_id=_uid(), code="jean-michel", name="Jean-Michel")
        assert proj["code"] == "jean-michel"
        assert proj["status"] == "active"
        got = project_svc.get_owned(conn, user_id=_uid(), project_id=proj["id"])
        assert got["name"] == "Jean-Michel"


def test_duplicate_code_conflict(tmp_db_v2):
    with db_connect() as conn:
        project_svc.create(conn, user_id=_uid(), code="dup", name="A")
        with pytest.raises(project_svc.ProjectOpError) as exc:
            project_svc.create(conn, user_id=_uid(), code="dup", name="B")
        assert exc.value.code == "already_exists"


def test_code_with_spaces_rejected(tmp_db_v2):
    with db_connect() as conn:
        with pytest.raises(project_svc.ProjectOpError) as exc:
            project_svc.create(conn, user_id=_uid(), code="has spaces", name="A")
        assert exc.value.code == "invalid_code"


def test_list_and_archive_filter(tmp_db_v2):
    with db_connect() as conn:
        a = project_svc.create(conn, user_id=_uid(), code="a", name="A")
        project_svc.create(conn, user_id=_uid(), code="b", name="B")
        project_svc.update(conn, user_id=_uid(), project_id=a["id"], status="archived")
        assert len(project_svc.list_(conn, user_id=_uid())) == 2
        active = project_svc.list_(conn, user_id=_uid(), include_archived=False)
        assert {p["code"] for p in active} == {"b"}


def test_update_and_delete(tmp_db_v2):
    with db_connect() as conn:
        proj = project_svc.create(conn, user_id=_uid(), code="x", name="X")
        project_svc.update(conn, user_id=_uid(), project_id=proj["id"], name="Renamed")
        assert project_svc.get_owned(conn, user_id=_uid(), project_id=proj["id"])["name"] == "Renamed"
        project_svc.delete(conn, user_id=_uid(), project_id=proj["id"])
        with pytest.raises(project_svc.ProjectOpError) as exc:
            project_svc.get_owned(conn, user_id=_uid(), project_id=proj["id"])
        assert exc.value.code == "not_found"


def test_update_invalid_status_rejected(tmp_db_v2):
    with db_connect() as conn:
        proj = project_svc.create(conn, user_id=_uid(), code="x", name="X")
        with pytest.raises(project_svc.ProjectOpError) as exc:
            project_svc.update(conn, user_id=_uid(), project_id=proj["id"], status="frozen")
        assert exc.value.code == "invalid_status"


# ---- code_repo / repo_kind ------------------------------------------------


def test_create_repo_defaults_and_explicit(tmp_db_v2):
    with db_connect() as conn:
        # Default: no repo attached (fallback to PROJECT_ROOT downstream).
        plain = project_svc.create(conn, user_id=_uid(), code="plain", name="P")
        assert plain["code_repo"] == "" and plain["repo_kind"] == "local"
        # Explicit ssh repo.
        ssh = project_svc.create(
            conn, user_id=_uid(), code="cloud", name="Cloud",
            code_repo="git@github.com:org/repo.git", repo_kind="ssh",
        )
        assert ssh["code_repo"] == "git@github.com:org/repo.git"
        assert ssh["repo_kind"] == "ssh"
        # Persisted.
        got = project_svc.get_owned(conn, user_id=_uid(), project_id=ssh["id"])
        assert got["repo_kind"] == "ssh"


def test_create_invalid_repo_kind_rejected(tmp_db_v2):
    with db_connect() as conn:
        with pytest.raises(project_svc.ProjectOpError) as exc:
            project_svc.create(conn, user_id=_uid(), code="bad", name="B", repo_kind="svn")
        assert exc.value.code == "invalid_repo_kind"


def test_update_repo(tmp_db_v2):
    with db_connect() as conn:
        proj = project_svc.create(conn, user_id=_uid(), code="x", name="X")
        project_svc.update(
            conn, user_id=_uid(), project_id=proj["id"],
            code_repo="/abs/path/to/repo", repo_kind="local",
        )
        got = project_svc.get_owned(conn, user_id=_uid(), project_id=proj["id"])
        assert got["code_repo"] == "/abs/path/to/repo"
        with pytest.raises(project_svc.ProjectOpError) as exc:
            project_svc.update(conn, user_id=_uid(), project_id=proj["id"], repo_kind="hg")
        assert exc.value.code == "invalid_repo_kind"


def test_get_owned_rejects_other_user(tmp_db_v2):
    """Ownership : another user's project is not_found, not leaked."""
    with db_connect() as conn:
        other = conn.execute(
            "INSERT INTO web_users(username, password_hash, created_at) "
            "VALUES('bob','h',datetime('now'))"
        ).lastrowid
        proj = project_svc.create(conn, user_id=other, code="bob-proj", name="Bob")
        with pytest.raises(project_svc.ProjectOpError) as exc:
            project_svc.get_owned(conn, user_id=_uid(), project_id=proj["id"])
        assert exc.value.code == "not_found"


# ---- conversation attachment ----------------------------------------------


def test_conversation_carries_project_id(tmp_db_v2):
    with db_connect() as conn:
        proj = project_svc.create(conn, user_id=_uid(), code="p", name="P")
    conv_id, _ = conversation_svc.create_conversation("chat", project_id=proj["id"])
    with db_connect() as conn:
        row = conn.execute("SELECT project_id FROM conversations WHERE id=?", (conv_id,)).fetchone()
    assert row["project_id"] == proj["id"]


def test_set_and_detach_conversation_project(tmp_db_v2):
    from jeanmichel import db

    with db_connect() as conn:
        proj = project_svc.create(conn, user_id=_uid(), code="p", name="P")
    conv_id, _ = conversation_svc.create_conversation("chat")
    with db_connect() as conn:
        db.set_conversation_project(conn, conv_id, proj["id"])
    with db_connect() as conn:
        assert conn.execute("SELECT project_id FROM conversations WHERE id=?", (conv_id,)).fetchone()["project_id"] == proj["id"]
    with db_connect() as conn:
        db.set_conversation_project(conn, conv_id, None)
    with db_connect() as conn:
        assert conn.execute("SELECT project_id FROM conversations WHERE id=?", (conv_id,)).fetchone()["project_id"] is None


def test_delete_project_sets_conversation_null(tmp_db_v2):
    """ON DELETE SET NULL : deleting a project orphans its conversations, not deletes them."""
    with db_connect() as conn:
        proj = project_svc.create(conn, user_id=_uid(), code="p", name="P")
    conv_id, _ = conversation_svc.create_conversation("chat", project_id=proj["id"])
    with db_connect() as conn:
        project_svc.delete(conn, user_id=_uid(), project_id=proj["id"])
    with db_connect() as conn:
        row = conn.execute("SELECT id, project_id FROM conversations WHERE id=?", (conv_id,)).fetchone()
    assert row is not None  # conversation survives
    assert row["project_id"] is None


def test_delete_project_cascades_project_memory(tmp_db_v2):
    """scope='project' memory is removed when the project is deleted."""
    with db_connect() as conn:
        proj = project_svc.create(conn, user_id=_uid(), code="p", name="P")
        memory.save(conn, scope="project", code="dec", title="t", description="d",
                    content="c", project_id=proj["id"])
    with db_connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM memory WHERE scope='project'").fetchone()["c"] == 1
        project_svc.delete(conn, user_id=_uid(), project_id=proj["id"])
        assert conn.execute("SELECT COUNT(*) AS c FROM memory WHERE scope='project'").fetchone()["c"] == 0


# ---- project-scope memory injection ---------------------------------------


def test_project_memory_injected_only_with_project_id(tmp_db_v2):
    with db_connect() as conn:
        proj = project_svc.create(conn, user_id=_uid(), code="p", name="P")
        memory.save(conn, scope="project", code="arch", title="t",
                    description="project architecture note", content="c", project_id=proj["id"])
    with db_connect() as conn:
        block_no, _ = render_memory_block(conn, user_id=_uid())
        block_yes, _ = render_memory_block(conn, user_id=_uid(), project_id=proj["id"])
    assert "project architecture note" not in block_no
    assert "## Project context" in block_yes
    assert "arch : project architecture note" in block_yes
