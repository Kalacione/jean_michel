"""Integration tests for the orchestrator via MockClient."""

from __future__ import annotations

from jeanmichel.config import UserProfile
from jeanmichel.llm import MockClient
from jeanmichel.models import LLMResponse, ToolCall
from jeanmichel.orchestrator import (
    AgentStarted,
    ConversationStarted,
    DelegationStarted,
    FinalAnswer,
    Orchestrator,
    ThoughtCaptured,
    ToolCallEmitted,
    ToolResponseRecorded,
)

PROFILE = UserProfile(description="test user")


def _orch(script, tmp_env):
    return Orchestrator(
        llm=MockClient(script=script),
        profile=PROFILE,
        ask_human_callback=lambda question, why: "test answer",
    )


class TestSimpleAnswer:
    def test_final_answer_text(self, tmp_env):
        orch = _orch([
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "42"}),
            ]),
        ], tmp_env)
        events = list(orch.run("What is the answer?"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "42"

    def test_conversation_started_emitted(self, tmp_env):
        orch = _orch([
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "ok"}),
            ]),
        ], tmp_env)
        events = list(orch.run("hi"))
        assert any(isinstance(e, ConversationStarted) for e in events)

    def test_thought_captured(self, tmp_env):
        orch = _orch([
            LLMResponse(thinking="deep thought", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "done"}),
            ]),
        ], tmp_env)
        events = list(orch.run("think"))
        thoughts = [e for e in events if isinstance(e, ThoughtCaptured)]
        assert len(thoughts) == 1
        assert thoughts[0].text == "deep thought"


class TestNativeTool:
    def test_clock_tool_call_and_response(self, tmp_env):
        orch = _orch([
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="clock", arguments={"timezone": "UTC"}),
                ToolCall(name="return_to_user", arguments={"answer": "done"}),
            ]),
        ], tmp_env)
        events = list(orch.run("what time is it?"))
        emitted = [e for e in events if isinstance(e, ToolCallEmitted)]
        recorded = [e for e in events if isinstance(e, ToolResponseRecorded)]
        assert any(e.tool_name == "clock" for e in emitted)
        assert any(e.tool_name == "clock" for e in recorded)


class TestDelegation:
    def test_delegation_spawns_second_agent(self, tmp_env):
        orch = _orch([
            # jean-michel delegates
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="delegate_to", arguments={
                    "agent_code": "summarizer",
                    "briefing": "Summarize: hello world.",
                    "expected": "one sentence",
                    "support_files": [],
                }),
            ]),
            # summarizer returns
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "Hello."}),
            ]),
            # jean-michel resumes and returns
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "Résumé: Hello."}),
            ]),
        ], tmp_env)
        events = list(orch.run("résume hello world"))
        agents_started = [e for e in events if isinstance(e, AgentStarted)]
        assert len(agents_started) == 2
        assert agents_started[0].agent_code == "jean-michel"
        assert agents_started[1].agent_code == "summarizer"
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert "Résumé" in fa.text


class TestAskHuman:
    def test_ask_human_injects_answer(self, tmp_env):
        orch = _orch([
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="ask_human", arguments={
                    "question": "Which format?", "why": "needed to proceed",
                }),
            ]),
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "done"}),
            ]),
        ], tmp_env)
        events = list(orch.run("process this"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "done"
