"""Tests for depth promotion — delegation whitelist and sub-research at depth=2."""

from __future__ import annotations

from jeanmichel import db
from jeanmichel.config import UserProfile
from jeanmichel.llm import MockClient
from jeanmichel.models import LLMResponse, ToolCall
from jeanmichel.orchestrator import (
    DelegationStarted,
    FinalAnswer,
    Orchestrator,
)
from jeanmichel.prompts import tools_payload_for_agent

PROFILE = UserProfile(notes="test user")


def _tc(name: str, **kwargs) -> ToolCall:
    return ToolCall(name=name, arguments=kwargs)


def _resp(*calls: ToolCall) -> LLMResponse:
    return LLMResponse(thinking="", content="", tool_calls=list(calls))


def _orch(script: list[LLMResponse]) -> Orchestrator:
    return Orchestrator(llm=MockClient(script=script), profile=PROFILE, mode="analyse")


# ---- Delegation whitelist --------------------------------------------------

class TestDelegationWhitelist:
    def test_specialist_cannot_delegate_to_planner(self, tmp_env):
        """wikipedia-specialist has no delegation targets → delegate_to(planner) rejected.

        Planner is also inactive so KeyError would follow anyway, but the whitelist
        fires first with a clearer error message.
        """
        orch = _orch([
            # jean-michel routes to wikipedia-specialist
            _resp(_tc("set_task_class", task_class="medium_task")),
            _resp(_tc("delegate_to", agent_code="wikipedia-specialist",
                      briefing="find info", expected="gather_done")),
            # wikipedia-specialist tries to delegate to planner (whitelist blocks it)
            _resp(_tc("delegate_to", agent_code="planner",
                      briefing="plan this", expected="plan")),
            # after block, use report_findings
            _resp(_tc("report_findings", summary="done", confidence="high")),
            # jean-michel returns
            _resp(_tc("return_to_user", answer="done")),
            # archivist
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("Find info on topic"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "done"
        # planner should NOT appear as a DelegationStarted child
        delegations = [e for e in events if isinstance(e, DelegationStarted)]
        child_agents = {e.child_agent for e in delegations}
        assert "planner" not in child_agents

    def test_jean_michel_can_delegate_to_all_listed(self, tmp_env):
        """jean-michel can delegate to web-search and critical-thinker."""
        orch = _orch([
            _resp(_tc("set_task_class", task_class="medium_task")),
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="search", expected="gather_done")),
            _resp(_tc("report_findings", summary="sources found", confidence="high")),
            _resp(_tc("delegate_to", agent_code="critical-thinker",
                      briefing="critique", expected="critic_done")),
            _resp(_tc("report_findings", summary="ok", confidence="high")),
            _resp(_tc("return_to_user", answer="done")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("Research and critique"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "done"

    def test_whitelist_empty_means_no_delegation(self, tmp_env):
        """summarizer has no delegation targets → any delegate_to is rejected."""
        orch = _orch([
            _resp(_tc("set_task_class", task_class="medium_task")),
            _resp(_tc("delegate_to", agent_code="summarizer",
                      briefing="summarize", expected="summary")),
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="search", expected="results")),
            # summarizer tries to delegate (blocked), then reports findings
            _resp(_tc("report_findings", summary="summary text", confidence="high")),
            _resp(_tc("return_to_user", answer="done")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("Summarize this"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "done"
        # web-search-specialist was never reached as a delegation
        delegations = [e for e in events if isinstance(e, DelegationStarted)]
        child_agents = [e.child_agent for e in delegations]
        assert "web-search-specialist" not in child_agents


# ---- Depth 2 via critical-thinker -----------------------------------------

class TestCriticCanDelegateAtDepth2:
    def test_critic_can_delegate_to_websearch(self, tmp_env):
        """critical-thinker (depth=1) can delegate to web-search-specialist (depth=2)."""
        orch = _orch([
            # jean-michel delegates to critical-thinker
            _resp(_tc("set_task_class", task_class="medium_task")),
            _resp(_tc("delegate_to", agent_code="critical-thinker",
                      briefing="check this claim", expected="critic_done")),
            # critical-thinker delegates to web-search to verify a fact (depth=2)
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find sources for claim X", expected="gather_done")),
            # web-search at depth=2 returns via report_findings
            _resp(_tc("report_findings", summary="found confirmation", confidence="high")),
            # critical-thinker resumes and concludes
            _resp(_tc("report_findings", summary="claim is supported", confidence="high")),
            # jean-michel returns
            _resp(_tc("return_to_user", answer="done")),
            # archivist
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("Verify claim X"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "done"
        delegations = [e for e in events if isinstance(e, DelegationStarted)]
        # critical-thinker delegated to web-search at depth=2
        depth2 = [e for e in delegations if e.parent_agent == "critical-thinker"]
        assert len(depth2) == 1
        assert depth2[0].child_agent == "web-search-specialist"

    def test_critic_cannot_delegate_to_document_builder(self, tmp_env):
        """critical-thinker cannot delegate to document-builder (not in its whitelist)."""
        orch = _orch([
            _resp(_tc("set_task_class", task_class="medium_task")),
            _resp(_tc("delegate_to", agent_code="critical-thinker",
                      briefing="critique", expected="critic_done")),
            # critic tries to delegate to document-builder (blocked)
            _resp(_tc("delegate_to", agent_code="document-builder",
                      briefing="write report", expected="report")),
            # after block, conclude
            _resp(_tc("report_findings", summary="critique done", confidence="high")),
            _resp(_tc("return_to_user", answer="done")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("Critique and build"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "done"
        delegations = [e for e in events if isinstance(e, DelegationStarted)]
        child_agents = {e.child_agent for e in delegations}
        assert "document-builder" not in child_agents


# ---- report_findings replaces signal_convergence at all depths -------------

class TestSignalConvergenceAtDepth2:
    def test_report_findings_offered_to_specialist(self, tmp_env):
        """specialists have report_findings (replaces signal_convergence)."""
        with db.connect() as conn:
            agent = db.get_agent_by_code(conn, "web-search-specialist")
            tool_grants = db.load_tool_grants(conn, agent.id)
            registry: dict = {}
        payload = tools_payload_for_agent(
            agent_role="specialist",
            tool_grants=tool_grants,
            registry=registry,
            depth=2,
        )
        tool_names = [t["function"]["name"] for t in payload]
        assert "report_findings" in tool_names
        assert "signal_convergence" not in tool_names
        assert "return_to_user" not in tool_names

    def test_report_findings_offered_at_depth_1_too(self, tmp_env):
        """report_findings is statically granted to specialists at any depth."""
        with db.connect() as conn:
            tool_grants = db.load_tool_grants(conn,
                db.get_agent_by_code(conn, "critical-thinker").id)
        payload = tools_payload_for_agent("specialist", tool_grants, {}, depth=1)
        tool_names = [t["function"]["name"] for t in payload]
        assert "report_findings" in tool_names
        assert "signal_convergence" not in tool_names


# ---- DB helpers ------------------------------------------------------------

class TestDelegationTargetsDB:
    def test_load_delegation_targets_jean_michel(self, tmp_env):
        with db.connect() as conn:
            jm = db.get_agent_by_code(conn, "jean-michel")
            targets = db.load_delegation_targets(conn, jm.id)
        assert "web-search-specialist" in targets
        assert "critical-thinker" in targets
        assert "document-builder" in targets
        assert "archivist" not in targets

    def test_load_delegation_targets_critic(self, tmp_env):
        with db.connect() as conn:
            critic = db.get_agent_by_code(conn, "critical-thinker")
            targets = db.load_delegation_targets(conn, critic.id)
        assert targets == {"web-search-specialist", "wikipedia-specialist"}

    def test_load_delegation_targets_empty_for_specialist(self, tmp_env):
        with db.connect() as conn:
            summarizer = db.get_agent_by_code(conn, "summarizer")
            targets = db.load_delegation_targets(conn, summarizer.id)
        assert targets == set()
