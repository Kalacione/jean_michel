"""Integration tests for the orchestrator via MockClient."""

from __future__ import annotations

from jeanmichel.config import UserProfile
from jeanmichel.llm import MockClient
from jeanmichel.models import LLMResponse, ToolCall
from jeanmichel.orchestrator import (
    AgentStarted,
    ConversationStarted,
    FinalAnswer,
    Orchestrator,
    SummaryUpdated,
    ThoughtCaptured,
    ToolCallEmitted,
    ToolResponseRecorded,
    TurnStarted,
)

PROFILE = UserProfile(notes="test user")


def _orch(script, tmp_env, mode="analyse"):
    return Orchestrator(
        llm=MockClient(script=script),
        profile=PROFILE,
        mode=mode,
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


class TestModes:
    def test_analyse_mode_creates_conversation_with_mode(self, tmp_env):
        from jeanmichel import db
        orch = _orch([
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "ok"}),
            ]),
        ], tmp_env, mode="analyse")
        list(orch.run("hello"))
        with db.connect() as conn:
            row = conn.execute(
                "SELECT mode FROM conversations WHERE id = ?", (orch.conv_id,)
            ).fetchone()
        assert row["mode"] == "analyse"

    def test_analyse_mode_no_summary_file(self, tmp_env):
        orch = _orch([
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "ok"}),
            ]),
        ], tmp_env, mode="analyse")
        list(orch.run("hello"))
        assert not (orch.conv_folder / "summary.md").exists()

    def test_chat_mode_two_turns_produces_summary(self, tmp_env):
        # First turn: jean-michel answers, archivist writes summary
        orch = _orch([
            # turn 1 — jean-michel
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "Paris is the capital."}),
            ]),
            # archivist post-turn
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={
                    "answer": (
                        "## Established facts\n- Paris is the capital of France.\n"
                        "## Open threads\n(none)\n"
                        "## Resolved contradictions\n(none)\n"
                        "## User preferences observed\n(none)"
                    ),
                }),
            ]),
            # turn 2 — jean-michel
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "About 2 million in Paris proper."}),
            ]),
            # archivist post-turn 2
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={
                    "answer": (
                        "## Established facts\n- Paris is the capital of France.\n- Population ~2M.\n"
                        "## Open threads\n(none)\n"
                        "## Resolved contradictions\n(none)\n"
                        "## User preferences observed\n(none)"
                    ),
                }),
            ]),
        ], tmp_env, mode="chat")

        events1 = list(orch.run("What is the capital of France?"))
        assert any(isinstance(e, FinalAnswer) for e in events1)
        assert any(isinstance(e, SummaryUpdated) for e in events1)
        assert (orch.conv_folder / "summary.md").exists()

        events2 = list(orch.run("What is its population?"))
        assert any(isinstance(e, TurnStarted) for e in events2)
        turn_ev = next(e for e in events2 if isinstance(e, TurnStarted))
        assert turn_ev.turn_index == 1

        assert any(isinstance(e, FinalAnswer) for e in events2)
        assert any(isinstance(e, SummaryUpdated) for e in events2)

        summary = (orch.conv_folder / "summary.md").read_text(encoding="utf-8")
        assert "Population" in summary

    def test_archivist_blocked_via_delegate_to(self, tmp_env):
        orch = _orch([
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="delegate_to", arguments={
                    "agent_code": "archivist",
                    "briefing": "please summarise",
                    "expected": "summary",
                    "support_files": [],
                }),
            ]),
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "done"}),
            ]),
        ], tmp_env, mode="chat")
        events = list(orch.run("test"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        # Should still answer (REJECTED fed back as tool_response, agent recovers)
        assert fa.text == "done"
