"""Tests for the propose_memory tool + consolidation.add_candidate — the in-turn capture
channel. Candidates are stashed in pending_consolidation for human review ; NOTHING is
written to memory directly (that happens only on human approval, via service.memory)."""

from __future__ import annotations

import json

import pytest

from jeanmichel.db import cli_user_id
from jeanmichel.db import connect as db_connect
from jeanmichel.service import consolidation, memory
from jeanmichel.tools.propose_memory import make_spec


def _uid() -> int:
    with db_connect() as conn:
        return cli_user_id(conn)


def _mk_conv(cid: str) -> None:
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO conversations (id, folder_path, status, mode, created_at, modified_at) "
            "VALUES (?, '/tmp/x', 'active', 'chat', datetime('now'), datetime('now'))",
            (cid,),
        )


def test_add_candidate_new_is_stashed_not_written(tmp_db_v2):
    _mk_conv("c1")
    cand = consolidation.add_candidate(
        "c1", scope="user", code="likes-rust", title="Rust",
        description="likes rust", content="prefers rust", user_id=_uid(),
    )
    assert cand["suggested_action"] == "new"
    assert cand["kind"] == "fact"
    assert [p["code"] for p in consolidation.load_pending("c1")] == ["likes-rust"]
    # Proposal only — NOT written to memory.
    with db_connect() as conn:
        assert memory.recall(conn, scope="user", code="likes-rust", user_id=_uid()) is None


def test_add_candidate_extend_when_exists(tmp_db_v2):
    _mk_conv("c2")
    with db_connect() as conn:
        memory.save(conn, scope="user", code="likes-rust", title="Rust",
                    description="likes rust", content="old", user_id=cli_user_id(conn))
    cand = consolidation.add_candidate(
        "c2", scope="user", code="likes-rust", title="Rust",
        description="likes rust more", content="new", user_id=_uid(),
    )
    assert cand["suggested_action"] == "extend"


def test_add_candidate_review_when_similar(tmp_db_v2):
    _mk_conv("c3")
    with db_connect() as conn:
        memory.save(conn, scope="user", code="rust-pref", title="Rust",
                    description="enjoys rust programming", content="body", user_id=cli_user_id(conn))
    cand = consolidation.add_candidate(
        "c3", scope="user", code="rust-systems", title="Rust systems",
        description="enjoys rust programming for systems", content="x", user_id=_uid(),
    )
    assert cand["suggested_action"] == "review"
    assert any(m["code"] == "rust-pref" for m in cand["existing_matches"])


def test_add_candidate_invalid_scope_raises(tmp_db_v2):
    _mk_conv("c4")
    with pytest.raises(memory.MemoryOpError):
        consolidation.add_candidate("c4", scope="galaxy", code="x", title="t",
                                    description="d", content="c", user_id=_uid())


def test_add_candidate_project_without_project_raises(tmp_db_v2):
    _mk_conv("c5")
    with pytest.raises(memory.MemoryOpError):
        consolidation.add_candidate("c5", scope="project", code="x", title="t",
                                    description="d", content="c", user_id=_uid())


def test_add_candidate_importance_clamped(tmp_db_v2):
    _mk_conv("c6")
    cand = consolidation.add_candidate(
        "c6", scope="user", code="k", title="t", description="d", content="c",
        user_id=_uid(), importance=99,
    )
    assert cand["importance"] == 5


def test_tool_proposes_for_review(tmp_db_v2):
    _mk_conv("c7")
    spec = make_spec("c7", _uid(), None)
    out = json.loads(spec.handler(
        scope="user", code="prefers-terse", title="Terse",
        description="prefers terse answers", content="Keep it short.",
    ))
    assert "error" not in out
    assert out["suggested_action"] == "new"
    assert [p["code"] for p in consolidation.load_pending("c7")] == ["prefers-terse"]


def test_tool_requires_conversation(tmp_db_v2):
    spec = make_spec("", _uid(), None)  # no conversation context
    out = json.loads(spec.handler(scope="user", code="x", title="t", description="d", content="c"))
    assert out["error_code"] == "no_conversation"
