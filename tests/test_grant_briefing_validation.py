"""Tests for grant/briefing validation — structured expected + artifact guard."""

from __future__ import annotations

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


# ---- C. Artifact guard on phase verbs --------------------------------------

class TestArtifactGuard:
    def test_gather_done_without_artifact_rejected(self, tmp_env):
        """gather_done(artifacts=["nope.md"]) blocked when file does not exist.

        Specialist tries again with empty artifacts — allowed.
        """
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find stuff", expected="gather_done")),
            # specialist declares nope.md which doesn't exist → blocked
            _resp(_tc("gather_done", summary="done", artifacts=["nope.md"])),
            # retry with no artifacts → accepted
            _resp(_tc("gather_done", summary="done", artifacts=[])),
            # jean-michel returns
            _resp(_tc("return_to_user", answer="ok")),
            # archivist
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("Search for something"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "ok"
        # PhaseCompleted with phase=gather was emitted (second gather_done accepted)
        phases = [e for e in events if isinstance(e, PhaseCompleted)]
        assert any(e.phase == "gather" for e in phases)

    def test_gather_done_with_existing_artifact_accepted(self, tmp_env):
        """gather_done(artifacts=["report.md"]) accepted when file exists in workspace."""
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find stuff", expected="gather_done")),
            # specialist writes the file first
            _resp(_tc("workspace_create_file",
                      relative_path="report.md",
                      content="# Report",
                      description="gathered data")),
            # then signals gather_done with the artifact
            _resp(_tc("gather_done", summary="report written", artifacts=["report.md"])),
            # jean-michel returns
            _resp(_tc("return_to_user", answer="ok")),
            # archivist
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("Search for something"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "ok"
        phases = [e for e in events if isinstance(e, PhaseCompleted)]
        assert any(e.phase == "gather" and e.artifacts == ["report.md"] for e in phases)

    def test_gather_done_empty_artifacts_allowed(self, tmp_env):
        """gather_done with no artifacts is allowed (soft: not all searches produce files)."""
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="quick lookup", expected="gather_done")),
            _resp(_tc("gather_done", summary="found nothing notable", artifacts=[])),
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
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find info",
                      expected={
                          "completion_verb": "gather_done",
                          "workspace_artifacts": ["x.md"],
                      })),
            # specialist calls gather_done without writing x.md
            _resp(_tc("gather_done", summary="done", artifacts=[])),
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
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="write x.md",
                      expected={
                          "completion_verb": "gather_done",
                          "workspace_artifacts": ["x.md"],
                      })),
            # specialist writes x.md then signals gather_done
            _resp(_tc("workspace_create_file",
                      relative_path="x.md",
                      content="data",
                      description="gathered")),
            _resp(_tc("gather_done", summary="x.md written", artifacts=["x.md"])),
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
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="find something",
                      expected="a markdown summary")),   # legacy string
            _resp(_tc("gather_done", summary="done", artifacts=[])),
            _resp(_tc("return_to_user", answer="ok")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("Find something"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "ok"
