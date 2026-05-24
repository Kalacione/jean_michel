"""Tests for router synthesis discipline (sub-sprint C)."""

from __future__ import annotations

from jeanmichel.config import UserProfile
from jeanmichel.llm import MockClient
from jeanmichel.models import LLMResponse, ToolCall
from jeanmichel.orchestrator import (
    FinalAnswer,
    Orchestrator,
    PlanInitLoopDetected,
    SynthesisReminderInjected,
)

PROFILE = UserProfile(notes="test user")


def _tc(name: str, **kwargs) -> ToolCall:
    return ToolCall(name=name, arguments=kwargs)


def _resp(*calls: ToolCall) -> LLMResponse:
    return LLMResponse(thinking="", content="", tool_calls=list(calls))


def _orch(script: list[LLMResponse]) -> Orchestrator:
    return Orchestrator(llm=MockClient(script=script), profile=PROFILE, mode="analyse")


# ---- Synthesis reminder ----------------------------------------------------

class TestSynthesisReminder:

    def test_reminder_injected_when_router_skips_plan_update(self, tmp_env):
        """If the router calls something other than plan_update/delegate_to/ask_human/return_to_user
        after a specialist report_findings, a reminder is injected and the call is skipped."""
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find sources", expected={"completion_verb": "report_findings"})),
            _resp(_tc("report_findings", summary="Found 5 APIs.", confidence="high")),
            # router's first call after report is a random tool (should get reminder)
            _resp(_tc("conv_read_file", relative_path="some_artifact.md")),
            # router corrects after reminder
            _resp(_tc("plan_update", action="mark", step_id="S1",
                      status="done", findings="Found 5 APIs via web search.")),
            _resp(_tc("return_to_user", answer="Done.")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("find things"))
        reminders = [e for e in events if isinstance(e, SynthesisReminderInjected)]
        assert len(reminders) == 1
        assert reminders[0].child_agent_code == "web-search-specialist"

    def test_no_reminder_when_router_calls_plan_update_mark_first(self, tmp_env):
        """No reminder when router immediately calls plan_update(mark) after specialist."""
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find sources", expected={"completion_verb": "report_findings"})),
            _resp(_tc("report_findings", summary="Found 5 APIs.", confidence="high")),
            # router marks immediately
            _resp(_tc("plan_update", action="mark", step_id="S1",
                      status="done", findings="Found 5 APIs via web search.")),
            _resp(_tc("return_to_user", answer="Done.")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("find things"))
        reminders = [e for e in events if isinstance(e, SynthesisReminderInjected)]
        assert len(reminders) == 0

    def test_no_reminder_when_router_delegates_next(self, tmp_env):
        """delegate_to is in the allowed list — no reminder."""
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find sources", expected={"completion_verb": "report_findings"})),
            _resp(_tc("report_findings", summary="Found 5 APIs.", confidence="high")),
            # router delegates again immediately (allowed)
            _resp(_tc("delegate_to", agent_code="summarizer",
                      briefing="summarize", expected={"completion_verb": "report_findings"})),
            _resp(_tc("report_findings", summary="Summary done.", confidence="high")),
            _resp(_tc("return_to_user", answer="Done.")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("find things"))
        reminders = [e for e in events if isinstance(e, SynthesisReminderInjected)]
        assert len(reminders) == 0

    def test_reminder_cap_at_one_per_pending_synthesis(self, tmp_env):
        """Reminder is sent at most once per pending synthesis — not twice."""
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find sources", expected={"completion_verb": "report_findings"})),
            _resp(_tc("report_findings", summary="Found 5 APIs.", confidence="high")),
            # router ignores reminder twice
            _resp(_tc("conv_read_file", relative_path="some_artifact.md")),
            _resp(_tc("conv_read_file", relative_path="other_artifact.md")),
            # router finally marks
            _resp(_tc("plan_update", action="mark", step_id="S1",
                      status="done", findings="Found 5 APIs.")),
            _resp(_tc("return_to_user", answer="Done.")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("find things"))
        reminders = [e for e in events if isinstance(e, SynthesisReminderInjected)]
        # Cap at 1 reminder per pending synthesis
        assert len(reminders) == 1

    def test_pending_synthesis_cleared_after_mark(self, tmp_env):
        """After plan_update(mark), pending synthesis is cleared — no spurious reminders."""
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find sources", expected={"completion_verb": "report_findings"})),
            _resp(_tc("report_findings", summary="Found 5 APIs.", confidence="high")),
            _resp(_tc("plan_update", action="mark", step_id="S1",
                      status="done", findings="Found 5 APIs.")),
            # router now calls conv_read_file (fine — pending is cleared)
            _resp(_tc("conv_read_file", relative_path="some_artifact.md")),
            _resp(_tc("return_to_user", answer="Done.")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("find things"))
        reminders = [e for e in events if isinstance(e, SynthesisReminderInjected)]
        assert len(reminders) == 0


# ---- Idempotent init loop detection ----------------------------------------

class TestIdempotentInitLoop:

    def test_idempotent_init_warns_at_second_call(self, tmp_env):
        """Second already_exists yields PlanInitLoopDetected(count=2).
        Each init has slightly different args so duplicate detection doesn't block."""
        orch = _orch([
            # First init creates the plan (triggers deep_research pipeline)
            _resp(_tc("plan_update", action="init", title="My plan",
                      steps=[{"title": "Step 1"}])),
            # 1st already_exists (count=1, no event yet) — different title to bypass dedup
            _resp(_tc("plan_update", action="init", title="My plan v2",
                      steps=[{"title": "Step 1"}])),
            # 2nd already_exists (count=2, warning + PlanInitLoopDetected) — different title
            _resp(_tc("plan_update", action="init", title="My plan v3",
                      steps=[{"title": "Step 1"}])),
            # 3rd already_exists (count=3, fail-fast → exits deep_research cleanly)
            _resp(_tc("plan_update", action="init", title="My plan v4",
                      steps=[{"title": "Step 1"}])),
            # archivist (run() still fires after fail-fast)
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("do something"))
        loop_events = [e for e in events if isinstance(e, PlanInitLoopDetected)]
        counts = [e.count for e in loop_events]
        assert 2 in counts

    def test_idempotent_init_fails_fast_at_third_call(self, tmp_env):
        """Third already_exists from plan_update(init) fails fast with error answer."""
        orch = _orch([
            _resp(_tc("plan_update", action="init", title="My plan",
                      steps=[{"title": "Step 1"}])),
            # 1st already_exists (count=1) — different args to bypass dedup
            _resp(_tc("plan_update", action="init", title="My plan v2",
                      steps=[{"title": "Step 1"}])),
            # 2nd already_exists (count=2, warning)
            _resp(_tc("plan_update", action="init", title="My plan v3",
                      steps=[{"title": "Step 1"}])),
            # 3rd already_exists (count=3, fail-fast)
            _resp(_tc("plan_update", action="init", title="My plan v4",
                      steps=[{"title": "Step 1"}])),
            # archivist (run() still calls it even after fail-fast)
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("do something"))
        loop_events = [e for e in events if isinstance(e, PlanInitLoopDetected)]
        counts = [e.count for e in loop_events]
        assert 3 in counts
        # FinalAnswer is emitted with the error payload
        answers = [e for e in events if isinstance(e, FinalAnswer)]
        assert len(answers) == 1
        assert "plan_init_loop" in answers[0].text
