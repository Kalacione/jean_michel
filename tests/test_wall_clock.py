"""Tests for wall-clock timeout behaviour (LLM call, request, turn)."""

from __future__ import annotations

import time
from typing import Any

import jeanmichel.orchestrator as orch_mod
from jeanmichel.config import UserProfile
from jeanmichel.llm import LLMTimeoutError, MockClient
from jeanmichel.models import LLMResponse, ToolCall
from jeanmichel.orchestrator import (
    FinalAnswer,
    OrchestrationFailed,
    Orchestrator,
    SoftDeadlineReached,
    WallClockExceeded,
)

PROFILE = UserProfile(notes="test user")


def _orch(llm, mode="analyse"):
    return Orchestrator(llm=llm, profile=PROFILE, mode=mode)


# ---- Mock that raises LLMTimeoutError on first call, succeeds after ------

class _OneTimeLLMTimeoutClient:
    """Raises LLMTimeoutError on the first call, then returns a normal response."""

    def __init__(self, recovery_response: LLMResponse) -> None:
        self._recovery = recovery_response
        self._calls = 0

    def chat(self, *, system: str, user: str, tools: list[dict[str, Any]],
             temperature: float, thinking: bool) -> LLMResponse:
        self._calls += 1
        if self._calls == 1:
            raise LLMTimeoutError("Simulated LLM timeout")
        return self._recovery


# ---- Tests ---------------------------------------------------------------

class TestLLMCallTimeout:
    def test_wall_clock_exceeded_llm_call_emitted(self, tmp_env):
        """LLMTimeoutError → WallClockExceeded(scope='llm_call') yielded, then recovery."""
        recovery = LLMResponse(
            thinking="",
            content="",
            tool_calls=[ToolCall(name="return_to_user", arguments={"answer": "recovered"})],
        )
        orch = _orch(_OneTimeLLMTimeoutClient(recovery))
        events = list(orch.run("test timeout"))

        wce = [e for e in events if isinstance(e, WallClockExceeded)]
        assert len(wce) == 1
        assert wce[0].scope == "llm_call"
        assert wce[0].agent_code == "jean-michel"

    def test_recovery_produces_final_answer(self, tmp_env):
        """After LLM timeout the agent recovers and returns a final answer."""
        recovery = LLMResponse(
            thinking="",
            content="",
            tool_calls=[ToolCall(name="return_to_user", arguments={"answer": "recovered"})],
        )
        orch = _orch(_OneTimeLLMTimeoutClient(recovery))
        events = list(orch.run("test timeout"))

        final = [e for e in events if isinstance(e, FinalAnswer)]
        assert len(final) == 1
        assert final[0].text == "recovered"


class TestRequestWallClock:
    def test_request_timeout_emits_wall_clock_exceeded(self, tmp_env, monkeypatch):
        """Request exceeds wall-clock → WallClockExceeded(scope='request') + OrchestrationFailed."""
        # Call 1 (turn_started_at) → base
        # Call 2 (start_ts)        → base
        # Call 3+ (now in loop)    → base + 10000  → now - start_ts = 10000 > 500
        base = time.monotonic()
        _calls = [0]

        def fake_monotonic() -> float:
            _calls[0] += 1
            return base if _calls[0] <= 2 else base + 10_000.0

        monkeypatch.setattr(orch_mod.time, "monotonic", fake_monotonic)
        monkeypatch.setattr(orch_mod, "REQUEST_WALL_CLOCK_SECONDS", 500)
        monkeypatch.setattr(orch_mod, "TURN_WALL_CLOCK_SECONDS", 99_999)

        orch = _orch(MockClient(script=[
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "ok"}),
            ]),
        ]))
        events = list(orch.run("test"))

        wce = [e for e in events if isinstance(e, WallClockExceeded)]
        assert any(e.scope == "request" for e in wce)
        assert any(isinstance(e, OrchestrationFailed) for e in events)

    def test_request_timeout_elapsed_reported(self, tmp_env, monkeypatch):
        """WallClockExceeded reports a positive elapsed_seconds value."""
        base = time.monotonic()
        _calls = [0]

        def fake_monotonic() -> float:
            _calls[0] += 1
            return base if _calls[0] <= 2 else base + 10_000.0

        monkeypatch.setattr(orch_mod.time, "monotonic", fake_monotonic)
        monkeypatch.setattr(orch_mod, "REQUEST_WALL_CLOCK_SECONDS", 500)
        monkeypatch.setattr(orch_mod, "TURN_WALL_CLOCK_SECONDS", 99_999)

        orch = _orch(MockClient(script=[
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "ok"}),
            ]),
        ]))
        events = list(orch.run("test"))

        wce = next(e for e in events if isinstance(e, WallClockExceeded))
        assert wce.elapsed_seconds > 0


class TestTurnWallClock:
    def test_turn_timeout_emits_wall_clock_exceeded(self, tmp_env, monkeypatch):
        """Turn exceeds wall-clock → WallClockExceeded(scope='turn') + OrchestrationFailed."""
        # First call (turn_started_at) → base
        # All subsequent calls → base + 10000, so turn elapsed >> TURN_WALL_CLOCK_SECONDS
        base = time.monotonic()
        _calls = [0]

        def fake_monotonic() -> float:
            _calls[0] += 1
            return base if _calls[0] == 1 else base + 10_000.0

        monkeypatch.setattr(orch_mod.time, "monotonic", fake_monotonic)
        monkeypatch.setattr(orch_mod, "REQUEST_WALL_CLOCK_SECONDS", 99_999)
        monkeypatch.setattr(orch_mod, "TURN_WALL_CLOCK_SECONDS", 500)

        orch = _orch(MockClient(script=[
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "ok"}),
            ]),
        ]))
        events = list(orch.run("test"))

        wce = [e for e in events if isinstance(e, WallClockExceeded)]
        assert any(e.scope == "turn" for e in wce)
        assert any(isinstance(e, OrchestrationFailed) for e in events)

    def test_turn_timeout_only_fires_once(self, tmp_env, monkeypatch):
        """Exactly one WallClockExceeded is emitted for jean-michel (archivist is separate)."""
        base = time.monotonic()
        _calls = [0]

        def fake_monotonic() -> float:
            _calls[0] += 1
            return base if _calls[0] == 1 else base + 10_000.0

        monkeypatch.setattr(orch_mod.time, "monotonic", fake_monotonic)
        monkeypatch.setattr(orch_mod, "REQUEST_WALL_CLOCK_SECONDS", 99_999)
        monkeypatch.setattr(orch_mod, "TURN_WALL_CLOCK_SECONDS", 500)

        orch = _orch(MockClient(script=[
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "ok"}),
            ]),
        ]))
        events = list(orch.run("test"))

        wce = [e for e in events
               if isinstance(e, WallClockExceeded) and e.agent_code == "jean-michel"]
        assert len(wce) == 1


class TestSoftDeadline:
    def test_soft_deadline_emits_event_and_lets_agent_conclude(
        self, tmp_env, monkeypatch
    ):
        """Soft deadline crossed → SoftDeadlineReached + FinalAnswer, no hard cut."""
        # Call 1 (turn_started_at) → base
        # Call 2 (start_ts)        → base
        # Calls 3+ (loop)          → base + 600 (past soft=500, below hard=1000)
        base = time.monotonic()
        _calls = [0]

        def fake_monotonic() -> float:
            _calls[0] += 1
            return base if _calls[0] <= 2 else base + 600.0

        monkeypatch.setattr(orch_mod.time, "monotonic", fake_monotonic)
        monkeypatch.setattr(orch_mod, "REQUEST_WALL_CLOCK_SECONDS", 1000)
        monkeypatch.setattr(orch_mod, "TURN_WALL_CLOCK_SECONDS", 99_999)
        monkeypatch.setattr(orch_mod, "SOFT_DEADLINE_RATIO", 0.5)

        orch = _orch(MockClient(script=[
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "partial ok"}),
            ]),
        ]))
        events = list(orch.run("test"))

        soft = [e for e in events if isinstance(e, SoftDeadlineReached)]
        assert len(soft) >= 1
        assert soft[0].scope == "request"
        assert soft[0].elapsed_seconds >= 500
        # The agent should conclude, not be hard-cut.
        assert any(isinstance(e, FinalAnswer) for e in events)
        assert not any(isinstance(e, WallClockExceeded) for e in events)

    def test_soft_deadline_disabled_when_ratio_is_one(self, tmp_env, monkeypatch):
        """SOFT_DEADLINE_RATIO=1.0 disables the soft mechanism entirely."""
        base = time.monotonic()
        _calls = [0]

        def fake_monotonic() -> float:
            _calls[0] += 1
            # Stay safely below all deadlines so nothing fires.
            return base if _calls[0] <= 2 else base + 10.0

        monkeypatch.setattr(orch_mod.time, "monotonic", fake_monotonic)
        monkeypatch.setattr(orch_mod, "REQUEST_WALL_CLOCK_SECONDS", 1000)
        monkeypatch.setattr(orch_mod, "TURN_WALL_CLOCK_SECONDS", 99_999)
        monkeypatch.setattr(orch_mod, "SOFT_DEADLINE_RATIO", 1.0)

        orch = _orch(MockClient(script=[
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "ok"}),
            ]),
        ]))
        events = list(orch.run("test"))

        assert not any(isinstance(e, SoftDeadlineReached) for e in events)
        assert any(isinstance(e, FinalAnswer) for e in events)

