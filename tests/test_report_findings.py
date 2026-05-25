"""Tests for the report_findings control verb."""

from __future__ import annotations

from jeanmichel.config import UserProfile
from jeanmichel.llm import MockClient
from jeanmichel.models import LLMResponse, ToolCall
from jeanmichel.orchestrator import (
    FinalAnswer,
    Orchestrator,
    ReportFindingsReceived,
    SignalConvergenceRedirected,
)

PROFILE = UserProfile(notes="test user")


def _tc(name: str, **kwargs) -> ToolCall:
    return ToolCall(name=name, arguments=kwargs)


def _resp(*calls: ToolCall) -> LLMResponse:
    return LLMResponse(thinking="", content="", tool_calls=list(calls))


def _orch(script: list[LLMResponse]) -> Orchestrator:
    return Orchestrator(llm=MockClient(script=script), profile=PROFILE, mode="analyse")


# ---- Validation ------------------------------------------------------------

class TestReportFindingsValidation:

    def test_validates_summary_required(self, tmp_env):
        orch = _orch([
            # router classifies then delegates
            _resp(_tc("set_task_class", task_class="medium_task")),
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find sources", expected={"completion_verb": "report_findings"})),
            # specialist calls report_findings without summary → error
            _resp(_tc("report_findings", confidence="medium")),
            # specialist corrects
            _resp(_tc("report_findings", summary="Found 5 sources.", confidence="medium")),
            # router returns
            _resp(_tc("return_to_user", answer="Done.")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("find things"))
        reports = [e for e in events if isinstance(e, ReportFindingsReceived)]
        assert len(reports) == 1
        assert reports[0].confidence == "medium"

    def test_validates_confidence_enum(self, tmp_env):
        orch = _orch([
            _resp(_tc("set_task_class", task_class="medium_task")),
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find sources", expected={"completion_verb": "report_findings"})),
            # bad confidence
            _resp(_tc("report_findings", summary="Done.", confidence="excellent")),
            # correct
            _resp(_tc("report_findings", summary="Done.", confidence="high")),
            _resp(_tc("return_to_user", answer="Done.")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("find things"))
        reports = [e for e in events if isinstance(e, ReportFindingsReceived)]
        assert len(reports) == 1
        assert reports[0].confidence == "high"

    def test_rejects_missing_files_produced(self, tmp_env):
        orch = _orch([
            _resp(_tc("set_task_class", task_class="medium_task")),
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find sources", expected={"completion_verb": "report_findings"})),
            # declares a file that doesn't exist
            _resp(_tc("report_findings", summary="Done.", confidence="medium",
                      files_produced=["gather/nonexistent.md"])),
            # correct (no files)
            _resp(_tc("report_findings", summary="Done.", confidence="medium")),
            _resp(_tc("return_to_user", answer="Done.")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("find things"))
        reports = [e for e in events if isinstance(e, ReportFindingsReceived)]
        assert len(reports) == 1

    def test_terminates_specialist_request(self, tmp_env):
        """report_findings must terminate the specialist sub-request (convergent=True)."""
        orch = _orch([
            _resp(_tc("set_task_class", task_class="medium_task")),
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find sources", expected={"completion_verb": "report_findings"})),
            _resp(_tc("report_findings", summary="Found sources.", confidence="high")),
            _resp(_tc("return_to_user", answer="Research complete.")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("find things"))
        reports = [e for e in events if isinstance(e, ReportFindingsReceived)]
        assert len(reports) == 1
        answers = [e for e in events if isinstance(e, FinalAnswer)]
        assert answers[0].text == "Research complete."


# ---- Parent context --------------------------------------------------------

class TestReportFindingsParentView:

    def test_parent_sees_report_markdown(self, tmp_env):
        """After specialist returns, the router's next prompt context must contain
        the rendered report (## Report from web-search-specialist)."""
        received_user_messages: list[str] = []

        class _RecordingMock(MockClient):
            def chat(self, *, system: str, user: str, tools, **kwargs):
                received_user_messages.append(user)
                return super().chat(system=system, user=user, tools=tools, **kwargs)

        script = [
            _resp(_tc("set_task_class", task_class="medium_task")),
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find sources", expected={"completion_verb": "report_findings"})),
            _resp(_tc("report_findings", summary="Found 10 APIs.", confidence="medium",
                      sub_questions=[{"question": "Free tier only?", "why": "budget"}])),
            _resp(_tc("return_to_user", answer="Done.")),
            _resp(_tc("return_to_user", answer="archived")),
        ]
        orch = Orchestrator(
            llm=_RecordingMock(script=script),
            profile=PROFILE,
            mode="analyse",
        )
        list(orch.run("find things"))

        # Find a router user message that comes AFTER the report
        report_in_prompts = [p for p in received_user_messages
                             if "Report from web-search-specialist" in p]
        assert len(report_in_prompts) >= 1
        assert "confidence: medium" in report_in_prompts[0]
        assert "Free tier only?" in report_in_prompts[0]


# ---- Specialist cannot return_to_user -------------------------------------

class TestSpecialistCannotReturnToUser:

    def test_specialist_return_to_user_redirected(self, tmp_env):
        """When a specialist calls return_to_user, it must receive an error
        steering it to report_findings instead."""
        orch = _orch([
            _resp(_tc("set_task_class", task_class="medium_task")),
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find sources", expected={"completion_verb": "report_findings"})),
            # specialist (wrongly) calls return_to_user
            _resp(_tc("return_to_user", answer="Here are my results.")),
            # specialist corrects
            _resp(_tc("report_findings", summary="Found 10 APIs.", confidence="medium")),
            _resp(_tc("return_to_user", answer="Done.")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("find things"))
        reports = [e for e in events if isinstance(e, ReportFindingsReceived)]
        assert len(reports) == 1
        answers = [e for e in events if isinstance(e, FinalAnswer)]
        assert answers[0].text == "Done."


# ---- signal_convergence redirect ------------------------------------------

class TestSignalConvergenceDeprecated:

    def test_signal_convergence_redirected_to_report_findings(self, tmp_env):
        """If a specialist (via inertia) calls signal_convergence,
        the orchestrator must issue a redirect error and yield SignalConvergenceRedirected."""
        orch = _orch([
            _resp(_tc("set_task_class", task_class="medium_task")),
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find sources", expected={"completion_verb": "report_findings"})),
            # specialist uses old verb
            _resp(_tc("signal_convergence", synthesis="Here are my findings.")),
            # specialist corrects
            _resp(_tc("report_findings", summary="Found APIs.", confidence="medium")),
            _resp(_tc("return_to_user", answer="Done.")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("find things"))
        redirects = [e for e in events if isinstance(e, SignalConvergenceRedirected)]
        assert len(redirects) == 1
        reports = [e for e in events if isinstance(e, ReportFindingsReceived)]
        assert len(reports) == 1
