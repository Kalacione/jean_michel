"""Tests for phase control verbs (planner_done / gather_done / critic_done / build_done)
and planner agent removal (migration 044)."""

from __future__ import annotations

from jeanmichel import db
from jeanmichel.config import UserProfile
from jeanmichel.llm import MockClient
from jeanmichel.models import LLMResponse, ToolCall
from jeanmichel.orchestrator import (
    FinalAnswer,
    Orchestrator,
    PhaseCompleted,
)

PROFILE = UserProfile(notes="test user")


def _tc(name: str, **kwargs) -> ToolCall:
    return ToolCall(name=name, arguments=kwargs)


def _resp(*calls: ToolCall) -> LLMResponse:
    return LLMResponse(thinking="", content="", tool_calls=list(calls))


def _orch(script: list[LLMResponse]) -> Orchestrator:
    return Orchestrator(llm=MockClient(script=script), profile=PROFILE, mode="analyse")


# ---- Agent removal ---------------------------------------------------------

class TestPlannerAgentRemoved:
    def test_planner_inactive_in_db(self, tmp_env):
        with db.connect() as conn:
            row = conn.execute(
                "SELECT active FROM agents WHERE code='planner'"
            ).fetchone()
        assert row is not None
        assert row["active"] == 0

    def test_planner_absent_from_active_list(self, tmp_env):
        with db.connect() as conn:
            agents = db.list_active_agents(conn)
        codes = {a.code for a in agents}
        assert "planner" not in codes

    def test_delegate_to_planner_returns_error(self, tmp_env):
        """Attempting to delegate to the inactive planner raises a KeyError → error in tool_response."""
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="planner",
                      briefing="plan this", expected="plan.md")),
            _resp(_tc("return_to_user", answer="fallback")),
            _resp(_tc("return_to_user", answer="summary")),  # archivist
        ])
        events = list(orch.run("complex task"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "fallback"


# ---- gather_done -----------------------------------------------------------

class TestGatherDone:
    def test_gather_done_emits_phase_completed(self, tmp_env):
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find info", expected="sources")),
            _resp(_tc("gather_done", summary="found relevant sources",
                      artifacts=["gather/sources.md"])),
            _resp(_tc("return_to_user", answer="done")),
            _resp(_tc("return_to_user", answer="summary")),  # archivist
        ])
        events = list(orch.run("research task"))
        pc = next(e for e in events if isinstance(e, PhaseCompleted))
        assert pc.phase == "gather"
        assert pc.agent_code == "web-search-specialist"
        assert pc.summary == "found relevant sources"
        assert pc.artifacts == ["gather/sources.md"]

    def test_gather_done_records_in_db(self, tmp_env):
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find info", expected="sources")),
            _resp(_tc("gather_done", summary="db test summary")),
            _resp(_tc("return_to_user", answer="done")),
            _resp(_tc("return_to_user", answer="summary")),  # archivist
        ])
        list(orch.run("research task"))
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT phase, agent_code, summary FROM conversation_phases"
            ).fetchall()
        assert len(rows) == 1
        assert tuple(rows[0]) == ("gather", "web-search-specialist", "db test summary")

    def test_gather_done_completes_request(self, tmp_env):
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find info", expected="sources")),
            _resp(_tc("gather_done", summary="ok")),
            _resp(_tc("return_to_user", answer="final")),
            _resp(_tc("return_to_user", answer="summary")),  # archivist
        ])
        list(orch.run("research task"))
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT status FROM requests WHERE agent_id="
                "(SELECT id FROM agents WHERE code='web-search-specialist') "
                "ORDER BY created_at"
            ).fetchall()
        assert rows[-1][0] == "completed"


# ---- Wrong owner rejected --------------------------------------------------

class TestWrongOwnerRejected:
    def test_critic_done_rejected_for_web_search(self, tmp_env):
        """web-search-specialist calling critic_done gets an error, then succeeds with gather_done."""
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find info", expected="sources")),
            _resp(_tc("critic_done", summary="wrong verb")),   # rejected
            _resp(_tc("gather_done", summary="correct verb")), # accepted
            _resp(_tc("return_to_user", answer="done")),
            _resp(_tc("return_to_user", answer="summary")),    # archivist
        ])
        events = list(orch.run("research task"))
        pc = next(e for e in events if isinstance(e, PhaseCompleted))
        assert pc.phase == "gather"
        assert not any(
            isinstance(e, PhaseCompleted) and e.phase == "critic"
            for e in events
        )


# ---- Parent receives phase payload -----------------------------------------

class TestParentReceivesPhasePayload:
    def test_jean_michel_sees_phase_payload(self, tmp_env):
        """After gather_done, jean-michel receives the phase JSON in tool_responses."""
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find info", expected="sources")),
            _resp(_tc("gather_done", summary="found info", artifacts=["out.md"],
                      next_hint="ready for critic")),
            _resp(_tc("return_to_user", answer="got it")),
            _resp(_tc("return_to_user", answer="summary")),  # archivist
        ])
        events = list(orch.run("research task"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        # jean-michel saw the phase payload and returned cleanly
        assert fa.text == "got it"
        pc = next(e for e in events if isinstance(e, PhaseCompleted))
        assert pc.next_hint == "ready for critic"
