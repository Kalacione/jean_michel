"""Tests for grant/briefing validation — structured expected + artifact guard."""

from __future__ import annotations

from jeanmichel.config import UserProfile
from jeanmichel.llm import MockClient
from jeanmichel.models import LLMResponse, ToolCall
from jeanmichel.orchestrator import (
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


# ---- C. Artifact guard on report_findings ---------------------------------

class TestArtifactGuard:
    def test_report_findings_without_artifact_rejected(self, tmp_env):
        """
        report_findings(files_produced=["nope.md"]) blocked when file does not exist.

        Specialist tries again with empty files_produced — allowed.
        """
        orch = _orch([
            _resp(_tc("set_task_class", task_class="medium_task")),
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find stuff", expected="report_findings")),
            # specialist declares nope.md which doesn't exist → blocked
            _resp(_tc("report_findings", summary="done", confidence="high",
                      files_produced=["nope.md"])),
            # retry with no files → accepted
            _resp(_tc("report_findings", summary="done", confidence="high")),
            # jean-michel returns
            _resp(_tc("return_to_user", answer="ok")),
            # archivist
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("Search for something"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "ok"

    def test_report_findings_with_existing_file_accepted(self, tmp_env):
        """report_findings(files_produced=["report.md"]) accepted when file exists."""
        orch = _orch([
            _resp(_tc("set_task_class", task_class="medium_task")),
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find stuff", expected="report_findings")),
            # specialist writes the file first
            _resp(_tc("workspace_create_file",
                      relative_path="report.md",
                      content="# Report",
                      description="gathered data")),
            # then reports findings with the artifact
            _resp(_tc("report_findings", summary="report written", confidence="high",
                      files_produced=["report.md"])),
            # jean-michel returns
            _resp(_tc("return_to_user", answer="ok")),
            # archivist
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("Search for something"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "ok"

    def test_report_findings_empty_files_allowed(self, tmp_env):
        """report_findings with no files_produced is allowed."""
        orch = _orch([
            _resp(_tc("set_task_class", task_class="medium_task")),
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="quick lookup", expected="report_findings")),
            _resp(_tc("report_findings", summary="found nothing notable",
                      confidence="low")),
            _resp(_tc("return_to_user", answer="done")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("Quick search"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "done"


# ---- B. Post-delegation validation -----------------------------------------

class TestPostDelegationValidation:
    def test_validation_error_propagated_when_artifact_missing(self, tmp_env):
        """Parent receives validation_error when child didn't produce declared artifact."""
        orch = _orch([
            # jean-michel delegates with workspace_artifacts contract
            _resp(_tc("set_task_class", task_class="medium_task")),
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find info",
                      expected={
                          "completion_verb": "report_findings",
                          "workspace_artifacts": ["x.md"],
                      })),
            # specialist calls report_findings without writing x.md
            _resp(_tc("report_findings", summary="done", confidence="high")),
            # jean-michel sees validation_error, retries or returns
            _resp(_tc("return_to_user", answer="validation caught")),
            # archivist
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("Need x.md"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "validation caught"

    def test_validation_passes_when_artifact_present(self, tmp_env):
        """No validation_error when child produces all declared workspace_artifacts."""
        orch = _orch([
            _resp(_tc("set_task_class", task_class="medium_task")),
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="write x.md",
                      expected={
                          "completion_verb": "report_findings",
                          "workspace_artifacts": ["x.md"],
                      })),
            # specialist writes x.md then reports findings
            _resp(_tc("workspace_create_file",
                      relative_path="x.md",
                      content="data",
                      description="gathered")),
            _resp(_tc("report_findings", summary="x.md written", confidence="high",
                      files_produced=["x.md"])),
            # jean-michel returns normally (no validation_error in response)
            _resp(_tc("return_to_user", answer="success")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("Produce x.md"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "success"


# ---- A. Legacy string expected ----------------------------------------------

class TestLegacyStringExpected:
    def test_legacy_string_expected_accepted(self, tmp_env):
        """Passing expected as a plain string is still accepted (backward compat)."""
        orch = _orch([
            _resp(_tc("set_task_class", task_class="medium_task")),
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find something",
                      expected="a markdown summary")),   # legacy string
            _resp(_tc("report_findings", summary="done", confidence="high")),
            _resp(_tc("return_to_user", answer="ok")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("Find something"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "ok"
