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


# ---- rule candidates (paradigm promotion) ---------------------------------


def test_add_rule_candidate_create_when_novel(tmp_db_v2):
    _mk_conv("r1")
    cand = consolidation.add_rule_candidate(
        "r1", section_code="process", category_code="execution",
        title="Wibblezorp the flibber", content="- Wibblezorp the flibber grik.",
    )
    assert cand["kind"] == "rule" and cand["suggested_action"] == "create"
    pending = consolidation.load_pending("r1")
    assert len(pending) == 1 and pending[0]["title"] == "Wibblezorp the flibber"


def test_add_rule_candidate_review_when_similar(tmp_db_v2):
    _mk_conv("r2")
    # 'discipline' recurs across existing paradigm titles/content → flagged for review.
    cand = consolidation.add_rule_candidate(
        "r2", section_code="process", category_code="execution",
        title="Tool discipline matters", content="- Be disciplined with tools.",
    )
    assert cand["suggested_action"] == "review"
    assert cand["existing_matches"]  # similar paradigms surfaced (so the human can bind, not dup)


def test_add_rule_candidate_unknown_category_raises(tmp_db_v2):
    _mk_conv("r3")
    with pytest.raises(memory.MemoryOpError):
        consolidation.add_rule_candidate("r3", section_code="nope", category_code="nope",
                                         title="t", content="c")


def test_apply_rule_candidate_creates_dark_paradigm(tmp_db_v2):
    cand = {"kind": "rule", "section_code": "process", "category_code": "execution",
            "title": "Frobnicate early", "content": "- frobnicate.", "grounding_quote": "src"}
    with db_connect() as conn:
        res = consolidation.apply_rule_candidate(conn, cand, action="create")
        row = conn.execute("SELECT active, content FROM paradigms WHERE code=?", (res["code"],)).fetchone()
    assert res["action"] == "create"
    assert row["active"] == 0  # DARK until the human activates/binds it
    assert "frobnicate" in row["content"]


def test_apply_rule_candidate_bind_existing(tmp_db_v2):
    cand = {"kind": "rule", "section_code": "process", "category_code": "execution",
            "title": "x", "content": "y"}
    with db_connect() as conn:
        consolidation.apply_rule_candidate(conn, cand, action="bind",
                                           bind_agent="jean-michel", bind_to_code="memory_discipline")
        row = conn.execute(
            "SELECT 1 FROM agent_paradigms ap JOIN agents a ON a.id=ap.agent_id "
            "JOIN paradigms p ON p.id=ap.paradigm_id "
            "WHERE a.code='jean-michel' AND p.code='memory_discipline'"
        ).fetchone()
    assert row is not None  # the existing paradigm is bound (no duplicate created)


def test_tool_proposes_rule(tmp_db_v2):
    _mk_conv("rt")
    spec = make_spec("rt", _uid(), None)
    out = json.loads(spec.handler(
        kind="rule", section_code="process", category_code="execution",
        title="Frobnicate early", content="- frobnicate before zorking.",
    ))
    assert "error" not in out and out["kind"] == "rule"
    assert any(p.get("kind") == "rule" and p["title"] == "Frobnicate early"
               for p in consolidation.load_pending("rt"))


def test_tool_rule_requires_category(tmp_db_v2):
    _mk_conv("rt2")
    spec = make_spec("rt2", _uid(), None)
    out = json.loads(spec.handler(kind="rule", title="t", content="c"))  # no section/category
    assert out["error_code"] == "invalid_args"
