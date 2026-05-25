"""Tests for normalised-fingerprint duplicate detection and forced-convergence."""

from __future__ import annotations

from jeanmichel.config import UserProfile
from jeanmichel.llm import MockClient
from jeanmichel.models import LLMResponse, ToolCall
from jeanmichel.orchestrator import (
    DuplicateCallBlocked,
    FinalAnswer,
    ForcedConvergence,
    Orchestrator,
    _fingerprint,
)

PROFILE = UserProfile(notes="test user")


def _orch(script):
    return Orchestrator(llm=MockClient(script=script), profile=PROFILE, mode="analyse")


# ---- _fingerprint unit tests ------------------------------------------------

class TestFingerprint:
    def test_case_insensitive(self):
        fp1 = _fingerprint("wikipedia_search", {"query": "MediaWiki API"}, {})
        fp2 = _fingerprint("wikipedia_search", {"query": "mediawiki api"}, {})
        assert fp1 == fp2

    def test_default_value_merged(self):
        fp1 = _fingerprint("mytool", {"query": "X"}, {"results": 5})
        fp2 = _fingerprint("mytool", {"query": "X", "results": 5}, {"results": 5})
        assert fp1 == fp2

    def test_whitespace_normalised(self):
        fp1 = _fingerprint("search", {"query": "hello world"}, {})
        fp2 = _fingerprint("search", {"query": "hello  world"}, {})
        assert fp1 == fp2

    def test_leading_trailing_whitespace(self):
        fp1 = _fingerprint("t", {"query": "  foo  "}, {})
        fp2 = _fingerprint("t", {"query": "foo"}, {})
        assert fp1 == fp2

    def test_different_args_not_equal(self):
        fp1 = _fingerprint("clock", {"timezone": "UTC"}, {})
        fp2 = _fingerprint("clock", {"timezone": "PST"}, {})
        assert fp1 != fp2

    def test_different_tools_not_equal(self):
        fp1 = _fingerprint("tool_a", {"q": "x"}, {})
        fp2 = _fingerprint("tool_b", {"q": "x"}, {})
        assert fp1 != fp2


# ---- Orchestrator integration tests ----------------------------------------

def _clock(tz: str) -> LLMResponse:
    return LLMResponse(thinking="", content="", tool_calls=[
        ToolCall(name="clock", arguments={"timezone": tz}),
    ])


def _return(text: str = "done") -> LLMResponse:
    return LLMResponse(thinking="", content="", tool_calls=[
        ToolCall(name="return_to_user", arguments={"answer": text}),
    ])


class TestDuplicateBlockedEvent:
    def test_second_identical_call_blocked(self, tmp_env):
        """Second identical native-tool call yields DuplicateCallBlocked."""
        orch = _orch([
            _clock("UTC"),       # succeeds
            _clock("UTC"),       # blocked
            _return("ok"),       # exit
            _return("summary"),  # archivist
        ])
        events = list(orch.run("test"))
        blocked = [e for e in events if isinstance(e, DuplicateCallBlocked)]
        assert len(blocked) == 1
        assert blocked[0].tool_name == "clock"

    def test_case_variant_blocked(self, tmp_env):
        """'UTC' and 'utc' produce the same fingerprint → second is blocked."""
        orch = _orch([
            _clock("UTC"),
            _clock("utc"),
            _return("ok"),
            _return("summary"),
        ])
        events = list(orch.run("test"))
        blocked = [e for e in events if isinstance(e, DuplicateCallBlocked)]
        assert len(blocked) == 1


class TestForcedConvergence:
    def test_three_consecutive_duplicates_triggers_convergence(self, tmp_env):
        """After 3 consecutive duplicate-blocked calls, ForcedConvergence is emitted."""
        orch = _orch([
            _clock("UTC"),   # succeeds
            _clock("UTC"),   # dup 1
            _clock("UTC"),   # dup 2
            _clock("UTC"),   # dup 3 → ForcedConvergence
            _return("summary"),  # archivist
        ])
        events = list(orch.run("test"))
        assert any(isinstance(e, ForcedConvergence) for e in events)

    def test_forced_convergence_agent_code(self, tmp_env):
        """ForcedConvergence reports the correct agent."""
        orch = _orch([
            _clock("UTC"),
            _clock("UTC"),
            _clock("UTC"),
            _clock("UTC"),
            _return("summary"),
        ])
        events = list(orch.run("test"))
        fc = next(e for e in events if isinstance(e, ForcedConvergence))
        assert fc.agent_code == "jean-michel"

    def test_forced_convergence_emits_final_answer(self, tmp_env):
        """Run completes normally (FinalAnswer emitted) even after ForcedConvergence."""
        orch = _orch([
            _clock("UTC"),
            _clock("UTC"),
            _clock("UTC"),
            _clock("UTC"),
            _return("summary"),
        ])
        events = list(orch.run("test"))
        assert any(isinstance(e, FinalAnswer) for e in events)


class TestCounterReset:
    def test_non_duplicate_resets_consecutive_counter(self, tmp_env):
        """Two dupes, one new call, two more dupes → no ForcedConvergence."""
        orch = _orch([
            _clock("UTC"),   # 1st: succeeds (consecutive=0)
            _clock("UTC"),   # 2nd: dup 1 (consecutive=1)
            _clock("UTC"),   # 3rd: dup 2 (consecutive=2)
            _clock("PST"),   # 4th: different, succeeds (consecutive=0)
            _clock("PST"),   # 5th: dup 1 (consecutive=1)
            _clock("PST"),   # 6th: dup 2 (consecutive=2)
            _return("ok"),   # exit
            _return("summary"),  # archivist
        ])
        events = list(orch.run("test"))
        assert not any(isinstance(e, ForcedConvergence) for e in events)
