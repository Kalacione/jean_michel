"""Unit tests for src/jeanmichel/db.py."""

from __future__ import annotations

import pytest

from jeanmichel import db


class TestAgents:
    def test_list_active_agents_returns_five(self, tmp_env):
        with db.connect() as conn:
            agents = db.list_active_agents(conn)
        codes = {a.code for a in agents}
        assert codes == {"jean-michel", "summarizer", "synthesizer", "weather-specialist", "wikipedia-specialist"}

    def test_get_agent_by_code(self, tmp_env):
        with db.connect() as conn:
            agent = db.get_agent_by_code(conn, "jean-michel")
        assert agent.role == "router"

    def test_get_agent_unknown_raises(self, tmp_env):
        with db.connect() as conn:
            with pytest.raises(KeyError):
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
        assert grants == ["conv_read_file"]

    def test_synthesizer_has_no_tools(self, tmp_env):
        with db.connect() as conn:
            sy = db.get_agent_by_code(conn, "synthesizer")
            grants = db.load_tool_grants(conn, sy.id)
        assert grants == []


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
