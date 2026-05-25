"""Tests for corrupted LLM output detection, retry, and escalation."""

from __future__ import annotations

from jeanmichel.config import UserProfile
from jeanmichel.llm import MockClient, _looks_corrupted
from jeanmichel.models import LLMResponse, ToolCall
from jeanmichel.orchestrator import (
    CorruptedOutputDetected,
    FinalAnswer,
    Orchestrator,
)

PROFILE = UserProfile(notes="test user")

_CORRUPTED_CONTENT = "<thought>some raw token leakage</thought>"
_CORRUPTED_THINKING = "<|start_of_thinking|>raw"


def _orch(script):
    return Orchestrator(llm=MockClient(script=script), profile=PROFILE, mode="analyse")


def _corrupted(content: str = _CORRUPTED_CONTENT) -> LLMResponse:
    return LLMResponse(thinking="", content=content, tool_calls=[])


def _clean_return(text: str = "clean answer") -> LLMResponse:
    return LLMResponse(thinking="", content="", tool_calls=[
        ToolCall(name="return_to_user", arguments={"answer": text}),
    ])


# ---- _looks_corrupted unit tests -------------------------------------------

class TestLooksCorrupted:
    def test_thought_marker(self):
        assert _looks_corrupted("<thought>blah</thought>")

    def test_pipe_marker(self):
        assert _looks_corrupted("text <|start|> more")

    def test_end_of_turn(self):
        assert _looks_corrupted("<end_of_turn>")

    def test_close_s(self):
        assert _looks_corrupted("answer</s>")

    def test_tool_call_tag(self):
        assert _looks_corrupted("<tool_call>")

    def test_clean_text(self):
        assert not _looks_corrupted("This is a normal answer.")

    def test_empty_string(self):
        assert not _looks_corrupted("")


# ---- MockClient retry tests ------------------------------------------------

class TestMockClientRetry:
    def test_retry_on_corrupted_returns_clean(self):
        """Script [corrupted, clean] → MockClient returns clean, corrupted=False."""
        client = MockClient(script=[_corrupted(), _clean_return()])
        resp = client.chat(
            system="", user="", tools=[], temperature=0.2, thinking=False,
        )
        assert not resp.corrupted
        assert resp.tool_calls[0].name == "return_to_user"

    def test_two_corrupted_marks_response(self):
        """Script [corrupted, corrupted] → MockClient returns with corrupted=True."""
        client = MockClient(script=[_corrupted(), _corrupted()])
        resp = client.chat(
            system="", user="", tools=[], temperature=0.2, thinking=False,
        )
        assert resp.corrupted

    def test_clean_first_no_retry(self):
        """Clean first response → returned immediately, script still has item."""
        client = MockClient(script=[_clean_return(), _corrupted()])
        resp = client.chat(
            system="", user="", tools=[], temperature=0.2, thinking=False,
        )
        assert not resp.corrupted
        assert len(client.script) == 1  # second item untouched


# ---- Orchestrator escalation tests -----------------------------------------

class TestOrchestratorCorrupted:
    def test_corrupted_response_emits_event(self, tmp_env):
        """Two consecutive corrupted LLM outputs → CorruptedOutputDetected emitted."""
        orch = _orch([
            _corrupted(),          # attempt 1 (jean-michel)
            _corrupted(),          # attempt 2 (jean-michel) → corrupted=True
            _clean_return("sum"),  # archivist
        ])
        events = list(orch.run("test"))
        assert any(isinstance(e, CorruptedOutputDetected) for e in events)

    def test_corrupted_response_has_correct_agent(self, tmp_env):
        orch = _orch([
            _corrupted(),
            _corrupted(),
            _clean_return("sum"),
        ])
        events = list(orch.run("test"))
        ev = next(e for e in events if isinstance(e, CorruptedOutputDetected))
        assert ev.agent_code == "jean-michel"

    def test_corrupted_still_produces_final_answer(self, tmp_env):
        """Even after corruption escalation, FinalAnswer is emitted (error payload)."""
        orch = _orch([
            _corrupted(),
            _corrupted(),
            _clean_return("sum"),
        ])
        events = list(orch.run("test"))
        assert any(isinstance(e, FinalAnswer) for e in events)


# ---- return_to_user corruption rejection -----------------------------------

class TestReturnToUserValidation:
    def test_corrupted_return_to_user_rejected(self, tmp_env):
        """return_to_user with marker → error injected, agent re-tries with clean answer."""
        orch = _orch([
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user",
                         arguments={"answer": "<thought>bad</thought>"}),
            ]),
            _clean_return("clean answer"),  # re-try after rejection
            _clean_return("sum"),           # archivist
        ])
        events = list(orch.run("test"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "clean answer"
        assert not any(isinstance(e, CorruptedOutputDetected) for e in events)
