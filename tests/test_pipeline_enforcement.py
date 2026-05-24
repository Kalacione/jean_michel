"""Tests for the deep_research pipeline enforcement (sprint 07)."""

from __future__ import annotations

import sqlite3

from jeanmichel import db
from jeanmichel.config import UserProfile
from jeanmichel.llm import MockClient
from jeanmichel.models import LLMResponse, ToolCall
from jeanmichel.orchestrator import (
    DelegationStarted,
    FinalAnswer,
    Orchestrator,
    PhaseCompleted,
    _expected_completion_for_target,
    _PHASE_NEXT,
    _pipeline_state_block,
)

PROFILE = UserProfile(notes="test user")
_STEPS = [
    {"id": "S1", "title": "Gather", "agent": "web-search-specialist",
     "deliverable": "workspace/gather.md"},
    {"id": "S2", "title": "Critique", "agent": "critical-thinker",
     "deliverable": "workspace/critique.md"},
    {"id": "S3", "title": "Build", "agent": "document-builder",
     "deliverable": "workspace/report.md"},
]


def _tc(name: str, **kwargs) -> ToolCall:
    return ToolCall(name=name, arguments=kwargs)


def _resp(*calls: ToolCall) -> LLMResponse:
    return LLMResponse(thinking="", content="", tool_calls=list(calls))


def _orch(script: list[LLMResponse]) -> Orchestrator:
    return Orchestrator(llm=MockClient(script=script), profile=PROFILE, mode="analyse")


# ---- Unit tests for constants / helpers ------------------------------------

class TestPipelineHelpers:
    def test_expected_completion_for_gather_agents(self):
        assert _expected_completion_for_target("web-search-specialist") == "gather_done"
        assert _expected_completion_for_target("wikipedia-specialist") == "gather_done"

    def test_expected_completion_for_critic(self):
        assert _expected_completion_for_target("critical-thinker") == "critic_done"

    def test_expected_completion_for_builder(self):
        assert _expected_completion_for_target("document-builder") == "build_done"

    def test_expected_completion_other_returns_none(self):
        assert _expected_completion_for_target("synthesizer") is None
        assert _expected_completion_for_target("summarizer") is None

    def test_phase_next_transitions(self):
        assert "gather_done" in _PHASE_NEXT["planner_done"]
        assert "gather_done" not in _PHASE_NEXT["planner_done"] - {"gather_done"}
        assert "critic_done" in _PHASE_NEXT["gather_done"]
        assert "build_done" in _PHASE_NEXT["critic_done"]
        assert "return_to_user" in _PHASE_NEXT["build_done"]

    def test_pipeline_state_block_none_for_non_deep_research(self):
        assert _pipeline_state_block("single_fact", None) is None
        assert _pipeline_state_block(None, None) is None

    def test_pipeline_state_block_deep_research(self):
        block = _pipeline_state_block("deep_research", "planner_done")
        assert block is not None
        assert "task_class: deep_research" in block
        assert "current_phase: planner_done" in block
        assert "gather_done" in block


# ---- Integration tests via orchestrator ------------------------------------

class TestSkipGatherBlocked:
    def test_skip_gather_blocked(self, tmp_env):
        """delegate_to(critical-thinker) when current_phase=planner_done is rejected.

        Flow:
        1. jean-michel calls plan_update(init) → sets deep_research / planner_done
        2. jean-michel tries delegate_to(critical-thinker) → BLOCKED (need gather first)
        3. jean-michel calls delegate_to(web-search-specialist) → allowed → gather_done
        4. jean-michel calls delegate_to(critical-thinker) → allowed → critic_done
        5. jean-michel calls delegate_to(document-builder) → build_done
        6. jean-michel calls return_to_user → OK
        7. archivist
        """
        orch = _orch([
            # Turn 1: jean-michel — plan_update init
            _resp(_tc("plan_update", action="init", title="Research", steps=_STEPS)),
            # Turn 2: jean-michel — tries critical-thinker (blocked), then web-search
            _resp(
                _tc("delegate_to", agent_code="critical-thinker",
                    briefing="critique this", expected="critic_done"),
                _tc("delegate_to", agent_code="web-search-specialist",
                    briefing="search this", expected="gather_done"),
            ),
            # web-search-specialist
            _resp(_tc("gather_done", summary="found sources", artifacts=[])),
            # Turn 3: jean-michel — now can delegate to critical-thinker
            _resp(_tc("delegate_to", agent_code="critical-thinker",
                      briefing="critique sources", expected="critic_done")),
            # critical-thinker
            _resp(_tc("critic_done", summary="analysis complete", artifacts=[])),
            # Turn 4: jean-michel — document-builder
            _resp(_tc("delegate_to", agent_code="document-builder",
                      briefing="build report", expected="build_done")),
            # document-builder
            _resp(_tc("build_done", summary="report written", artifacts=[])),
            # Turn 5: jean-michel — return
            _resp(_tc("return_to_user", answer="Research complete.")),
            # archivist
            _resp(_tc("return_to_user", answer="session archived")),
        ])
        events = list(orch.run("Research the topic deeply"))

        # Should complete successfully
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "Research complete."

        # Critical-thinker was NOT the first delegation (it was blocked)
        delegations = [e for e in events if isinstance(e, DelegationStarted)]
        assert delegations[0].child_agent == "web-search-specialist"


class TestFullFlow:
    def test_full_flow(self, tmp_env):
        """Full pipeline: plan_update init → gather → critic → build → return."""
        orch = _orch([
            # jean-michel: init plan
            _resp(_tc("plan_update", action="init", title="Research", steps=_STEPS)),
            # jean-michel: delegate gather
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="gather info", expected="gather_done")),
            # web-search
            _resp(_tc("gather_done", summary="sources gathered", artifacts=[])),
            # jean-michel: delegate critic
            _resp(_tc("delegate_to", agent_code="critical-thinker",
                      briefing="critique", expected="critic_done")),
            # critical-thinker
            _resp(_tc("critic_done", summary="critique done", artifacts=[])),
            # jean-michel: delegate build
            _resp(_tc("delegate_to", agent_code="document-builder",
                      briefing="build", expected="build_done")),
            # document-builder
            _resp(_tc("build_done", summary="document built", artifacts=[])),
            # jean-michel: return
            _resp(_tc("return_to_user", answer="Done.")),
            # archivist
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("Research something deeply"))

        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "Done."

        phases = [e for e in events if isinstance(e, PhaseCompleted)]
        assert [p.phase for p in phases] == ["gather", "critic", "build"]

    def test_full_flow_phase_recorded_in_db(self, tmp_env):
        """Each phase transition is recorded in conversation_phases and conversations."""
        orch = _orch([
            _resp(_tc("plan_update", action="init", title="R", steps=_STEPS)),
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="go", expected="gather_done")),
            _resp(_tc("gather_done", summary="done", artifacts=[])),
            _resp(_tc("delegate_to", agent_code="critical-thinker",
                      briefing="critique", expected="critic_done")),
            _resp(_tc("critic_done", summary="done", artifacts=[])),
            _resp(_tc("delegate_to", agent_code="document-builder",
                      briefing="build", expected="build_done")),
            _resp(_tc("build_done", summary="done", artifacts=[])),
            _resp(_tc("return_to_user", answer="ok")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("task"))
        conv_id = next(
            e for e in events if hasattr(e, "conversation_id")
        ).conversation_id
        with db.connect() as conn:
            task_class, current_phase = db.get_pipeline_state(conn, conv_id)
            rows = conn.execute(
                "SELECT phase FROM conversation_phases WHERE conversation_id=? ORDER BY recorded_at",
                (conv_id,),
            ).fetchall()
        assert task_class == "deep_research"
        assert current_phase == "build_done"
        assert [r["phase"] for r in rows] == ["gather", "critic", "build"]


class TestCriticLoopBackToGather:
    def test_critic_can_loop_back_to_gather(self, tmp_env):
        """critic_done → gather_done (substep) → critic_done → build_done flow is allowed."""
        orch = _orch([
            # init
            _resp(_tc("plan_update", action="init", title="R", steps=_STEPS)),
            # gather round 1
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="search", expected="gather_done")),
            _resp(_tc("gather_done", summary="first gather", artifacts=[])),
            # critic round 1 → critic_done
            _resp(_tc("delegate_to", agent_code="critical-thinker",
                      briefing="critique", expected="critic_done")),
            _resp(_tc("critic_done", summary="gap found", artifacts=[])),
            # loop back to gather (critic_done → gather_done allowed)
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="fill gap", expected="gather_done")),
            _resp(_tc("gather_done", summary="gap filled", artifacts=[])),
            # critic round 2
            _resp(_tc("delegate_to", agent_code="critical-thinker",
                      briefing="re-critique", expected="critic_done")),
            _resp(_tc("critic_done", summary="satisfied", artifacts=[])),
            # build
            _resp(_tc("delegate_to", agent_code="document-builder",
                      briefing="build", expected="build_done")),
            _resp(_tc("build_done", summary="built", artifacts=[])),
            # return
            _resp(_tc("return_to_user", answer="done")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("deep research task"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "done"
        phases = [e for e in events if isinstance(e, PhaseCompleted)]
        phase_names = [p.phase for p in phases]
        assert phase_names == ["gather", "critic", "gather", "critic", "build"]


class TestSingleFactNoConstraint:
    def test_single_fact_no_constraint(self, tmp_env):
        """task_class not set → return_to_user is allowed immediately."""
        orch = _orch([
            _resp(_tc("return_to_user", answer="42")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("what is 6 * 7"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "42"

    def test_return_before_build_blocked_for_deep_research(self, tmp_env):
        """return_to_user before build_done is blocked for deep_research."""
        orch = _orch([
            # init plan (sets deep_research)
            _resp(_tc("plan_update", action="init", title="R", steps=_STEPS)),
            # try to return immediately (should be blocked)
            _resp(_tc("return_to_user", answer="early exit")),
            # after being blocked, go through pipeline
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="go", expected="gather_done")),
            _resp(_tc("gather_done", summary="done", artifacts=[])),
            _resp(_tc("delegate_to", agent_code="critical-thinker",
                      briefing="critique", expected="critic_done")),
            _resp(_tc("critic_done", summary="done", artifacts=[])),
            _resp(_tc("delegate_to", agent_code="document-builder",
                      briefing="build", expected="build_done")),
            _resp(_tc("build_done", summary="done", artifacts=[])),
            _resp(_tc("return_to_user", answer="final answer")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("deep task"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        # "early exit" should NOT be the final answer (it was blocked)
        assert fa.text == "final answer"
