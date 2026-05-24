"""Unit tests for src/jeanmichel/db.py."""

from __future__ import annotations

import pytest

from jeanmichel import db


class TestAgents:
    def test_list_active_agents_returns_all(self, tmp_env):
        with db.connect() as conn:
            agents = db.list_active_agents(conn)
        codes = {a.code for a in agents}
        assert codes == {
            "jean-michel", "summarizer", "synthesizer",
            "weather-specialist", "wikipedia-specialist",
            "comparator-specialist", "archivist",
            "critical-thinker", "document-builder", "workspace-manager",
            "meta-analyst", "code-runner", "web-search-specialist",
        }

    def test_get_agent_by_code(self, tmp_env):
        with db.connect() as conn:
            agent = db.get_agent_by_code(conn, "jean-michel")
        assert agent.role == "router"

    def test_get_agent_unknown_raises(self, tmp_env):
        with db.connect() as conn, pytest.raises(KeyError):
            db.get_agent_by_code(conn, "ghost")


class TestToolGrants:
    def test_jean_michel_has_clock_and_conv_read_file(self, tmp_env):
        with db.connect() as conn:
            jm = db.get_agent_by_code(conn, "jean-michel")
            grants = db.load_tool_grants(conn, jm.id)
        assert "clock" in grants
        assert "conv_read_file" in grants

    def test_summarizer_has_conv_read_file_only(self, tmp_env):
        with db.connect() as conn:
            sm = db.get_agent_by_code(conn, "summarizer")
            grants = db.load_tool_grants(conn, sm.id)
        assert "conv_read_file" in grants
        assert all(g.startswith(("conv_", "workspace_")) for g in grants)

    def test_synthesizer_has_conv_read_file(self, tmp_env):
        with db.connect() as conn:
            sy = db.get_agent_by_code(conn, "synthesizer")
            grants = db.load_tool_grants(conn, sy.id)
        assert "conv_read_file" in grants


class TestAdminHelpers:
    def test_grant_and_revoke_tool(self, tmp_env):
        with db.connect() as conn:
            db.grant_tool(conn, "synthesizer", "clock")
            sy = db.get_agent_by_code(conn, "synthesizer")
            grants = db.load_tool_grants(conn, sy.id)
        assert "clock" in grants

        with db.connect() as conn:
            db.revoke_tool(conn, "synthesizer", "clock")
            sy = db.get_agent_by_code(conn, "synthesizer")
            grants = db.load_tool_grants(conn, sy.id)
        assert "clock" not in grants

    def test_grant_tool_idempotent(self, tmp_env):
        with db.connect() as conn:
            db.grant_tool(conn, "synthesizer", "clock")
            db.grant_tool(conn, "synthesizer", "clock")  # no error
            sy = db.get_agent_by_code(conn, "synthesizer")
            grants = db.load_tool_grants(conn, sy.id)
        assert grants.count("clock") == 1

    def test_grant_tool_unknown_agent_raises(self, tmp_env):
        with db.connect() as conn, pytest.raises(KeyError):
            db.grant_tool(conn, "ghost", "clock")

    def test_bind_and_unbind_paradigm(self, tmp_env):
        with db.connect() as conn:
            sy = db.get_agent_by_code(conn, "synthesizer")
            before = db.load_paradigms_for_agent(conn, sy.id, "analyse")
            before_codes = {p.code for p in before}

            db.bind_paradigm(conn, "synthesizer", "audit_phase")

        with db.connect() as conn:
            sy = db.get_agent_by_code(conn, "synthesizer")
            after = {p.code for p in db.load_paradigms_for_agent(conn, sy.id, "analyse")}
        assert "audit_phase" in after

        with db.connect() as conn:
            db.unbind_paradigm(conn, "synthesizer", "audit_phase")
            sy = db.get_agent_by_code(conn, "synthesizer")
            final = {p.code for p in db.load_paradigms_for_agent(conn, sy.id, "analyse")}
        assert "audit_phase" not in final

    def test_bind_unknown_paradigm_raises(self, tmp_env):
        with db.connect() as conn, pytest.raises(KeyError):
            db.bind_paradigm(conn, "synthesizer", "nonexistent_paradigm")

    def test_create_paradigm(self, tmp_env):
        with db.connect() as conn:
            pid = db.create_paradigm(
                conn,
                section_code="reasoning",
                category_code="sources",
                code="test_paradigm",
                title="Test Paradigm",
                content="- Test bullet",
            )
        assert isinstance(pid, int)

        with db.connect() as conn:
            sy = db.get_agent_by_code(conn, "synthesizer")
            db.bind_paradigm(conn, "synthesizer", "test_paradigm")
            paradigms = db.load_paradigms_for_agent(conn, sy.id, "analyse")
        assert any(p.code == "test_paradigm" for p in paradigms)

    def test_create_paradigm_unknown_category_raises(self, tmp_env):
        with db.connect() as conn, pytest.raises(KeyError):
            db.create_paradigm(
                conn,
                section_code="nope",
                category_code="nope",
                code="x",
                title="X",
                content="- x",
            )

    def test_set_paradigm_active_toggle(self, tmp_env):
        with db.connect() as conn:
            db.set_paradigm_active(conn, "brutal_truth", False)
        with db.connect() as conn:
            row = conn.execute(
                "SELECT active FROM paradigms WHERE code='brutal_truth'"
            ).fetchone()
        assert row["active"] == 0

        with db.connect() as conn:
            db.set_paradigm_active(conn, "brutal_truth", True)
        with db.connect() as conn:
            row = conn.execute(
                "SELECT active FROM paradigms WHERE code='brutal_truth'"
            ).fetchone()
        assert row["active"] == 1

    def test_set_paradigm_active_unknown_raises(self, tmp_env):
        with db.connect() as conn, pytest.raises(KeyError):
            db.set_paradigm_active(conn, "ghost_paradigm", True)


class TestConversations:
    def test_create_and_fetch(self, tmp_env):
        with db.connect() as conn:
            conv = db.create_conversation(conn, "abc123", "/tmp/conv", "fr")
        assert conv.id == "abc123"
        assert conv.user_language == "fr"

    def test_request_status_lifecycle(self, tmp_env):
        with db.connect() as conn:
            db.create_conversation(conn, "c1", "/tmp/c1", "en")
            jm = db.get_agent_by_code(conn, "jean-michel")
            db.create_request(conn, req_id="r1", conv_id="c1", parent_id=None,
                              depth=0, agent_id=jm.id,
                              inbound_briefing="hello", expected_outcome="answer")
            db.update_request_status(conn, "r1", "running")
            db.update_request_status(conn, "r1", "completed", completed=True)
            row = conn.execute("SELECT status FROM requests WHERE id='r1'").fetchone()
        assert row[0] == "completed"

    def test_update_conversation_language(self, tmp_env):
        with db.connect() as conn:
            db.create_conversation(conn, "lang1", "/tmp/lang1", None)
            db.update_conversation_language(conn, "lang1", "fr")
            row = conn.execute(
                "SELECT user_language FROM conversations WHERE id='lang1'"
            ).fetchone()
        assert row[0] == "fr"

    def test_list_active_conversations_excludes_closed(self, tmp_env):
        with db.connect() as conn:
            db.create_conversation(conn, "active1", "/tmp/a1", "fr")
            db.create_conversation(conn, "closed1", "/tmp/c1", "en")
            conn.execute("UPDATE conversations SET status='closed' WHERE id='closed1'")
            rows = db.list_active_conversations(conn)
        ids = [r["id"] for r in rows]
        assert "active1" in ids
        assert "closed1" not in ids

    def test_list_active_conversations_includes_awaiting_human(self, tmp_env):
        with db.connect() as conn:
            db.create_conversation(conn, "await1", "/tmp/aw1", "fr")
            conn.execute("UPDATE conversations SET status='awaiting_human' WHERE id='await1'")
            rows = db.list_active_conversations(conn)
        ids = [r["id"] for r in rows]
        assert "await1" in ids

    def test_get_conversation_by_exact_id(self, tmp_env):
        with db.connect() as conn:
            db.create_conversation(conn, "exact123456", "/tmp/exact", "fr")
            row = db.get_conversation(conn, "exact123456")
        assert row is not None
        assert row["id"] == "exact123456"

    def test_get_conversation_by_prefix(self, tmp_env):
        with db.connect() as conn:
            db.create_conversation(conn, "prefix123456", "/tmp/prefix", "fr")
            row = db.get_conversation(conn, "prefix12")
        assert row is not None
        assert row["id"] == "prefix123456"

    def test_get_conversation_unknown_returns_none(self, tmp_env):
        with db.connect() as conn:
            row = db.get_conversation(conn, "doesnotexist")
        assert row is None
