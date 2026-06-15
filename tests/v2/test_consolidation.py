"""Tests for the shadow consolidation engine : grounded proposal + FTS dedup +
pending persistence + apply. Determinism : ungrounded candidates are dropped."""

from __future__ import annotations

import json

from jeanmichel.db import cli_user_id
from jeanmichel.db import connect as db_connect
from jeanmichel.models import LLMResponse
from jeanmichel.service import consolidation
from jeanmichel.tools.manage_memory import _handler


class FakeLLM:
    """Returns a canned JSON payload for chat_messages (format='json')."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls = 0

    def chat_messages(self, **kwargs) -> LLMResponse:
        self.calls += 1
        return LLMResponse(thinking="", content=json.dumps(self._payload))


def _uid() -> int:
    with db_connect() as conn:
        return cli_user_id(conn)


def _messages(user_text: str, answer: str = "ok") -> list[dict]:
    return [
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": answer},
    ]


# ---- grounding gate -------------------------------------------------------


def test_grounded_candidate_kept(tmp_db_v2):
    llm = FakeLLM({"candidates": [{
        "scope": "user", "code": "likes-rust", "title": "Likes Rust",
        "description": "The user likes Rust", "content": "Prefers Rust for systems work.",
        "grounding_quote": "I really like Rust",
    }]})
    msgs = _messages("Honestly I really like Rust a lot these days.")
    with db_connect() as conn:
        cands = consolidation.propose(conn, msgs, llm=llm, user_id=_uid())
    assert len(cands) == 1
    assert cands[0]["code"] == "likes-rust"
    assert cands[0]["suggested_action"] == "new"


def test_ungrounded_candidate_dropped(tmp_db_v2):
    """Anti-hallucination : a quote that doesn't appear in the conversation → dropped."""
    llm = FakeLLM({"candidates": [{
        "scope": "user", "code": "likes-go", "title": "Likes Go",
        "description": "The user likes Go", "content": "...",
        "grounding_quote": "I love the Go programming language",  # never said
    }]})
    msgs = _messages("I really like Rust a lot.")
    with db_connect() as conn:
        cands = consolidation.propose(conn, msgs, llm=llm, user_id=_uid())
    assert cands == []


def test_invalid_scope_or_code_dropped(tmp_db_v2):
    llm = FakeLLM({"candidates": [
        {"scope": "galaxy", "code": "x", "title": "t", "description": "d", "content": "c",
         "grounding_quote": "this is grounded text here"},
        {"scope": "user", "code": "has spaces", "title": "t", "description": "d", "content": "c",
         "grounding_quote": "this is grounded text here"},
    ]})
    msgs = _messages("this is grounded text here, definitely.")
    with db_connect() as conn:
        assert consolidation.propose(conn, msgs, llm=llm, user_id=_uid()) == []


def test_project_scope_dropped_without_project(tmp_db_v2):
    llm = FakeLLM({"candidates": [{
        "scope": "project", "code": "dec", "title": "t", "description": "d", "content": "c",
        "grounding_quote": "we decided to use postgres",
    }]})
    msgs = _messages("For this project we decided to use postgres.")
    with db_connect() as conn:
        # No project_id supplied → project candidate can't be satisfied.
        assert consolidation.propose(conn, msgs, llm=llm, user_id=_uid()) == []
        # With a project_id → kept.
        cands = consolidation.propose(conn, msgs, llm=llm, user_id=_uid(), project_id=1)
    assert len(cands) == 1
    assert cands[0]["project_id"] == 1


def test_assistant_only_grounding_rejected(tmp_db_v2):
    """Anti-GIGO : a quote that appears ONLY in the ASSISTANT's own message is dropped
    (the model must not memorize its own, possibly hallucinated, claims)."""
    llm = FakeLLM({"candidates": [{
        "scope": "world", "code": "grain-volume", "title": "Grain volume",
        "description": "d", "content": "c",
        "grounding_quote": "a semolina grain is about 0.04 cubic centimeters",  # assistant said it
    }]})
    msgs = [
        {"role": "user", "content": "how many grains of semolina in a couscous plate?"},
        {"role": "assistant", "content": "Well, a semolina grain is about 0.04 cubic centimeters, so…"},
    ]
    with db_connect() as conn:
        assert consolidation.propose(conn, msgs, llm=llm, user_id=_uid()) == []


def test_tool_result_grounding_kept(tmp_db_v2):
    """A quote from a TOOL result (a real source) is accepted."""
    llm = FakeLLM({"candidates": [{
        "scope": "world", "code": "py314-release", "title": "Python 3.14",
        "description": "d", "content": "c",
        "grounding_quote": "Python 3.14 was released in October 2025",
    }]})
    msgs = [
        {"role": "user", "content": "when did python 3.14 come out?"},
        {"role": "tool", "content": "search result: Python 3.14 was released in October 2025."},
        {"role": "assistant", "content": "It shipped in late 2025."},
    ]
    with db_connect() as conn:
        cands = consolidation.propose(conn, msgs, llm=llm, user_id=_uid())
    assert len(cands) == 1 and cands[0]["code"] == "py314-release"


# ---- dedup / contradiction surfacing --------------------------------------


def test_existing_code_suggests_extend(tmp_db_v2):
    _handler(action="save", scope="user", code="likes-rust", title="Rust",
             description="likes rust", content="old")
    llm = FakeLLM({"candidates": [{
        "scope": "user", "code": "likes-rust", "title": "Likes Rust",
        "description": "The user likes Rust", "content": "new info",
        "grounding_quote": "I really like Rust",
    }]})
    msgs = _messages("I really like Rust.")
    with db_connect() as conn:
        cands = consolidation.propose(conn, msgs, llm=llm, user_id=_uid())
    assert cands[0]["suggested_action"] == "extend"


def test_similar_entry_surfaced_as_review(tmp_db_v2):
    _handler(action="save", scope="user", code="rust-pref", title="Rust",
             description="enjoys rust programming", content="body")
    llm = FakeLLM({"candidates": [{
        "scope": "user", "code": "rust-systems", "title": "Rust systems",
        "description": "enjoys rust programming for systems", "content": "x",
        "grounding_quote": "I really like Rust",
    }]})
    msgs = _messages("I really like Rust.")
    with db_connect() as conn:
        cands = consolidation.propose(conn, msgs, llm=llm, user_id=_uid())
    assert cands[0]["suggested_action"] == "review"
    assert any(m["code"] == "rust-pref" for m in cands[0]["existing_matches"])


# ---- pending persistence + apply ------------------------------------------


def test_pending_roundtrip_and_dedup(tmp_db_v2, conv_folder):
    c1 = {"scope": "user", "code": "a", "title": "t", "description": "d", "content": "c",
          "grounding_quote": "q", "tool_code": None, "project_id": None,
          "suggested_action": "new", "existing_matches": []}
    consolidation.add_pending(conv_folder, [c1])
    # Re-adding the same key replaces (no duplicate).
    consolidation.add_pending(conv_folder, [{**c1, "title": "t2"}])
    pending = consolidation.load_pending(conv_folder)
    assert len(pending) == 1
    assert pending[0]["title"] == "t2"
    consolidation.clear_pending(conv_folder)
    assert consolidation.load_pending(conv_folder) == []


def test_apply_save_and_extend(tmp_db_v2):
    cand = {"scope": "user", "code": "fav", "title": "Fav", "description": "d",
            "content": "v1", "project_id": None, "tool_code": None}
    with db_connect() as conn:
        consolidation.apply_candidate(conn, cand, action="save", user_id=_uid())
    rec = json.loads(_handler(action="recall", scope="user", code="fav"))
    assert rec["entry"]["content"] == "v1"
    # Extend (edited content) updates in place.
    with db_connect() as conn:
        consolidation.apply_candidate(conn, cand, action="extend", user_id=_uid(), content="v2")
    rec = json.loads(_handler(action="recall", scope="user", code="fav"))
    assert rec["entry"]["content"] == "v2"


def test_run_shadow_stashes_pending(tmp_db_v2, conv_folder):
    from jeanmichel import persistence
    # run_shadow loads messages from the conv folder.
    persistence.save_messages(conv_folder, _messages("I really like Rust a lot."))
    # Create a conversation row so project lookup works.
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO conversations (id, folder_path, status, mode, created_at, modified_at) "
            "VALUES ('cshadow', ?, 'active', 'chat', datetime('now'), datetime('now'))",
            (str(conv_folder),),
        )
        conn.commit()
    llm = FakeLLM({"candidates": [{
        "scope": "user", "code": "likes-rust", "title": "Likes Rust",
        "description": "likes rust", "content": "c", "grounding_quote": "I really like Rust",
    }]})
    new = consolidation.run_shadow(conv_folder, "cshadow", llm=llm, user_id=_uid())
    assert len(new) == 1
    assert len(consolidation.load_pending(conv_folder)) == 1


def test_run_shadow_best_effort_on_bad_llm(tmp_db_v2, conv_folder):
    from jeanmichel import persistence

    class BoomLLM:
        def chat_messages(self, **kwargs):
            raise RuntimeError("ollama down")

    persistence.save_messages(conv_folder, _messages("something durable here"))
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO conversations (id, folder_path, status, mode, created_at, modified_at) "
            "VALUES ('cboom', ?, 'active', 'chat', datetime('now'), datetime('now'))",
            (str(conv_folder),),
        )
        conn.commit()
    # Never raises ; returns [].
    assert consolidation.run_shadow(conv_folder, "cboom", llm=BoomLLM(), user_id=_uid()) == []
