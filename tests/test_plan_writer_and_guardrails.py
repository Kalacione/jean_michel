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
        long_briefing = "x" * 200
        steps = [{"id": "S1", "agent": "web-search-specialist",
                   "briefing": long_briefing, "status": "done", "summary": ""}]
        plan_writer.write(tmp_path, steps)
        content = plan_writer.plan_path(tmp_path).read_text()
        # briefing truncated to 80 chars in the table cell
        assert "x" * 80 in content
        assert "x" * 81 not in content

    def test_pipes_in_briefing_escaped(self, tmp_path):
        """Pipe chars in briefing are replaced to avoid breaking markdown table."""
        steps = [{"id": "S1", "agent": "web-search-specialist",
                   "briefing": "a | b | c", "status": "done", "summary": "x | y"}]
        plan_writer.write(tmp_path, steps)
        content = plan_writer.plan_path(tmp_path).read_text()
        # Markdown table pipes should be ∣ (U+2223), not |
        lines = [l for l in content.splitlines() if "S1" in l]
        assert len(lines) == 1
        # Count raw pipes: 6 separators for a 5-column table (| c1 | c2 | c3 | c4 | c5 |)
        assert lines[0].count("|") == 6   # exactly 6 column separators

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
