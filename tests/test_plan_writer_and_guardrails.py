"""Tests for the deterministic plan_writer and the max_delegations guardrail.

plan.md is now a pure orchestrator side-effect:
  - written when delegate_to fires (step added, status=in_progress)
  - updated when child converges (status=done)

max_delegations prevents runaway research loops.
"""

from __future__ import annotations

import jeanmichel.config as _cfg
from jeanmichel import plan_writer
from jeanmichel.config import UserProfile
from jeanmichel.llm import MockClient
from jeanmichel.models import LLMResponse, ToolCall
from jeanmichel.orchestrator import (
    DelegationStarted,
    FinalAnswer,
    Orchestrator,
)

PROFILE = UserProfile(notes="test user")


def _tc(name: str, **kwargs) -> ToolCall:
    return ToolCall(name=name, arguments=kwargs)


def _resp(*calls: ToolCall) -> LLMResponse:
    return LLMResponse(thinking="", content="", tool_calls=list(calls))


def _orch(script: list[LLMResponse]) -> Orchestrator:
    return Orchestrator(llm=MockClient(script=script), profile=PROFILE, mode="analyse")


# ---- plan_writer unit tests ------------------------------------------------

class TestPlanWriter:
    def test_write_creates_plan_md_in_conv_folder(self, tmp_path):
        """write() creates plan.md directly in conv_folder (not workspace/)."""
        steps = [{"id": "S1", "agent": "web-search-specialist",
                   "briefing": "find X", "status": "in_progress", "summary": ""}]
        plan_writer.write(tmp_path, steps)
        plan_file = plan_writer.plan_path(tmp_path)
        assert plan_file.exists()
        assert plan_file.parent == tmp_path   # not in workspace/

    def test_render_contains_step_info(self, tmp_path):
        """Rendered plan.md contains agent, briefing, and status icon."""
        steps = [{"id": "S1", "agent": "critical-thinker",
                   "briefing": "analyse the data", "status": "done",
                   "summary": "all good"}]
        plan_writer.write(tmp_path, steps)
        content = plan_writer.plan_path(tmp_path).read_text()
        assert "S1" in content
        assert "critical-thinker" in content
        assert "analyse the data" in content
        assert "✅" in content
        assert "all good" in content

    def test_render_in_progress_icon(self, tmp_path):
        steps = [{"id": "S1", "agent": "web-search-specialist",
                   "briefing": "search", "status": "in_progress", "summary": ""}]
        plan_writer.write(tmp_path, steps)
        content = plan_writer.plan_path(tmp_path).read_text()
        assert "🔄" in content

    def test_render_multiple_steps(self, tmp_path):
        steps = [
            {"id": "S1", "agent": "web-search-specialist",
             "briefing": "gather sources", "status": "done", "summary": "found 3"},
            {"id": "S2", "agent": "critical-thinker",
             "briefing": "evaluate sources", "status": "in_progress", "summary": ""},
        ]
        plan_writer.write(tmp_path, steps)
        content = plan_writer.plan_path(tmp_path).read_text()
        assert "S1" in content
        assert "S2" in content
        assert "✅" in content
        assert "🔄" in content

    def test_render_truncates_long_briefing(self, tmp_path):
        """Briefing is truncated in the rendered plan to keep it readable."""
        long_briefing = "x" * 500
        steps = [{"id": "S1", "agent": "web-search-specialist",
                   "briefing": long_briefing, "status": "done", "summary": ""}]
        plan_writer.write(tmp_path, steps)
        content = plan_writer.plan_path(tmp_path).read_text()
        # New hierarchical format truncates briefing at ~240 chars + ellipsis.
        assert "x" * 200 in content
        assert "x" * 500 not in content
        assert "…" in content

    def test_pipes_in_briefing_preserved(self, tmp_path):
        """Pipes no longer break anything: new format is hierarchical, not table."""
        steps = [{"id": "S1", "agent": "web-search-specialist",
                   "briefing": "a | b | c", "status": "done", "summary": "x | y"}]
        plan_writer.write(tmp_path, steps)
        content = plan_writer.plan_path(tmp_path).read_text()
        assert "a | b | c" in content
        assert "x | y" in content

    def test_write_overwrites_on_second_call(self, tmp_path):
        """Second write() replaces the previous plan.md content."""
        steps1 = [{"id": "S1", "agent": "web-search-specialist",
                    "briefing": "first", "status": "in_progress", "summary": ""}]
        plan_writer.write(tmp_path, steps1)
        steps2 = [{"id": "S1", "agent": "web-search-specialist",
                    "briefing": "first", "status": "done", "summary": "completed"},
                  {"id": "S2", "agent": "critical-thinker",
                   "briefing": "second", "status": "in_progress", "summary": ""}]
        plan_writer.write(tmp_path, steps2)
        content = plan_writer.plan_path(tmp_path).read_text()
        assert "S2" in content
        assert "completed" in content


# ---- Orchestrator plan integration tests -----------------------------------

class TestPlanWrittenByOrchestrator:
    def test_plan_md_created_on_first_delegation(self, tmp_env):
        """plan.md is created as soon as the first delegate_to fires."""
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find info on X", expected="report_findings")),
            _resp(_tc("report_findings", summary="found X", confidence="high")),
            _resp(_tc("return_to_user", answer="ok")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("What is X?"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "ok"
        # plan.md must exist in the conversation folder
        plan_file = plan_writer.plan_path(orch.conv_folder)
        assert plan_file.exists()

    def test_plan_md_not_in_workspace(self, tmp_env):
        """plan.md is in conv_folder, NOT inside workspace/ (quota-free)."""
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find Y", expected="report_findings")),
            _resp(_tc("report_findings", summary="done", confidence="high")),
            _resp(_tc("return_to_user", answer="ok")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        list(orch.run("Find Y"))
        plan_file = plan_writer.plan_path(orch.conv_folder)
        workspace_plan = orch.conv_folder / "workspace" / "plan.md"
        assert plan_file.exists()
        assert not workspace_plan.exists()

    def test_plan_step_ids_sequential(self, tmp_env):
        """Two top-level delegations produce S1 then S2."""
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="gather sources", expected="report_findings")),
            _resp(_tc("report_findings", summary="3 sources", confidence="high")),
            _resp(_tc("delegate_to", agent_code="critical-thinker",
                      briefing="evaluate sources", expected="report_findings")),
            _resp(_tc("report_findings", summary="sources good", confidence="high")),
            _resp(_tc("return_to_user", answer="done")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        list(orch.run("Research topic"))
        content = plan_writer.plan_path(orch.conv_folder).read_text()
        assert "S1" in content
        assert "S2" in content

    def test_plan_step_marked_done_after_child_converges(self, tmp_env):
        """After a child converges, its plan step shows done status (✅)."""
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="search for Z", expected="report_findings")),
            _resp(_tc("report_findings", summary="found Z", confidence="high")),
            _resp(_tc("return_to_user", answer="ok")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        list(orch.run("Find Z"))
        content = plan_writer.plan_path(orch.conv_folder).read_text()
        assert "✅" in content
        assert "🔄" not in content   # no step still in_progress


# ---- max_delegations guardrail tests ---------------------------------------

class TestMaxDelegationsGuardrail:
    def test_delegation_budget_exhausted_error_returned(self, tmp_env, monkeypatch):
        """When total delegations exceed MAX_DELEGATIONS, router gets an error."""
        # Set a very small budget: 2
        monkeypatch.setattr(_cfg, "MAX_DELEGATIONS", 2)
        import jeanmichel.orchestrator as _orch_mod
        monkeypatch.setattr(_orch_mod, "MAX_DELEGATIONS", 2)

        orch = _orch([
            # delegation 1 — succeeds
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="search 1", expected="report_findings")),
            _resp(_tc("report_findings", summary="done 1", confidence="high")),
            # delegation 2 — succeeds
            _resp(_tc("delegate_to", agent_code="critical-thinker",
                      briefing="analyse 1", expected="report_findings")),
            _resp(_tc("report_findings", summary="done 2", confidence="high")),
            # delegation 3 — blocked (budget exhausted; jean-michel sees error)
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="search 2", expected="report_findings")),
            # jean-michel reacts to budget error and returns
            _resp(_tc("return_to_user", answer="budget exhausted, returning")),
            # archivist
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("Research deeply"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "budget exhausted, returning"
        # Only 2 successful delegations should have started
        started = [e for e in events if isinstance(e, DelegationStarted)]
        assert len(started) == 2

    def test_delegation_budget_resets_per_turn(self, tmp_env, monkeypatch):
        """MAX_DELEGATIONS budget resets between separate user turns."""
        monkeypatch.setattr(_cfg, "MAX_DELEGATIONS", 1)
        import jeanmichel.orchestrator as _orch_mod
        monkeypatch.setattr(_orch_mod, "MAX_DELEGATIONS", 1)

        orch = _orch([
            # Turn 1: 1 delegation — succeeds
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="search", expected="report_findings")),
            _resp(_tc("report_findings", summary="done", confidence="high")),
            _resp(_tc("return_to_user", answer="turn1")),
            _resp(_tc("return_to_user", answer="archived")),
            # Turn 2: 1 delegation — also succeeds (budget reset)
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="search again", expected="report_findings")),
            _resp(_tc("report_findings", summary="done again", confidence="high")),
            _resp(_tc("return_to_user", answer="turn2")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events1 = list(orch.run("First question"))
        fa1 = next(e for e in events1 if isinstance(e, FinalAnswer))
        assert fa1.text == "turn1"

        events2 = list(orch.run("Second question"))
        fa2 = next(e for e in events2 if isinstance(e, FinalAnswer))
        assert fa2.text == "turn2"


# ---- Router deep-research guard --------------------------------------------

class _SpyLLM:
    """MockClient wrapper that records the `tools` payload at each chat call."""

    def __init__(self, script: list[LLMResponse]) -> None:
        self._inner = MockClient(script=script)
        self.tools_per_call: list[list[str]] = []

    def chat(self, *, system, user, tools, temperature, thinking):
        self.tools_per_call.append(
            [t.get("function", {}).get("name", "") for t in tools]
        )
        return self._inner.chat(
            system=system, user=user, tools=tools,
            temperature=temperature, thinking=thinking,
        )


class TestRouterDeepResearchGuard:
    def test_web_search_dropped_once_plan_md_exists(self, tmp_env):
        """After the router's first delegation (plan.md created), web_search
        is filtered out of subsequent tools_payload calls — the router must
        delegate, not gather data itself."""
        spy = _SpyLLM([
            # Call 1 (router, no plan.md yet) → delegate
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find X", expected="report_findings")),
            # Call 2 (specialist) → report
            _resp(_tc("report_findings", summary="found X", confidence="high")),
            # Call 3 (router, plan.md now exists) → must not see web_search
            _resp(_tc("return_to_user", answer="ok")),
            # Call 4 (archivist)
            _resp(_tc("return_to_user", answer="archived")),
        ])
        orch = Orchestrator(llm=spy, profile=PROFILE, mode="analyse")
        list(orch.run("question"))

        # First router call: plan.md does not exist yet → web_search present
        assert "web_search" in spy.tools_per_call[0]
        # Third call is the router resuming after the delegation → plan.md
        # exists → web_search must have been stripped.
        assert "web_search" not in spy.tools_per_call[2]
        # delegate_to remains available so the router can still orchestrate.
        assert "delegate_to" in spy.tools_per_call[2]

    def test_trivial_request_keeps_web_search(self, tmp_env):
        """A trivial request that does not trigger any delegation keeps the
        full toolset (no plan.md created → no stripping)."""
        spy = _SpyLLM([
            # Router answers directly without delegating
            _resp(_tc("return_to_user", answer="42")),
            # Archivist
            _resp(_tc("return_to_user", answer="archived")),
        ])
        orch = Orchestrator(llm=spy, profile=PROFILE, mode="analyse")
        list(orch.run("trivial question"))

        # Router's call (index 0) should still have web_search.
        assert "web_search" in spy.tools_per_call[0]

