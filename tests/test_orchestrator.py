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
            # summarizer returns via report_findings
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="report_findings", arguments={"summary": "Hello.", "confidence": "high"}),
            ]),
            # jean-michel resumes and returns
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "Résumé: Hello."}),
            ]),
            # archivist post-turn
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={
                    "answer": "## Established facts\n- Hello.\n## Open threads\n(none)\n## Resolved contradictions\n(none)\n## User preferences observed\n(none)",
                }),
            ]),
        ], tmp_env)
        events = list(orch.run("résume hello world"))
        agents_started = [e for e in events if isinstance(e, AgentStarted)]
        codes = [e.agent_code for e in agents_started]
        assert "jean-michel" in codes
        assert "summarizer" in codes
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

    def test_analyse_mode_creates_summary_after_first_turn(self, tmp_env):
        orch = _orch([
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "ok"}),
            ]),
            # archivist post-turn
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={
                    "answer": (
                        "## Established facts\n- ok\n"
                        "## Open threads\n(none)\n"
                        "## Resolved contradictions\n(none)\n"
                        "## User preferences observed\n(none)"
                    ),
                }),
            ]),
        ], tmp_env, mode="analyse")
        list(orch.run("hello"))
        assert (orch.conv_folder / "summary.md").exists()

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


class TestConversationLifecycle:
    """Tests for bootstrap_conversation, close_conversation, resume_conversation."""

    def test_bootstrap_conversation_creates_folder(self, tmp_env):
        orch = _orch([], tmp_env)
        orch.bootstrap_conversation()
        assert orch.conv_folder is not None
        assert orch.conv_folder.exists()

    def test_bootstrap_conversation_idempotent(self, tmp_env):
        orch = _orch([], tmp_env)
        orch.bootstrap_conversation()
        folder_first = orch.conv_folder
        orch.bootstrap_conversation()
        assert orch.conv_folder == folder_first

    def test_bootstrap_creates_db_row(self, tmp_env):
        from jeanmichel import db as jmdb
        orch = _orch([], tmp_env)
        orch.bootstrap_conversation()
        with jmdb.connect() as conn:
            row = conn.execute(
                "SELECT status FROM conversations WHERE id=?", (orch.conv_id,)
            ).fetchone()
        assert row is not None
        assert row["status"] == "active"

    def test_analyse_multi_turn_same_folder(self, tmp_env):
        """In analyse mode, two run() calls must use the same conversation folder."""
        _archivist = LLMResponse(thinking="", content="", tool_calls=[
            ToolCall(name="return_to_user", arguments={
                "answer": "## Established facts\n(none)\n## Open threads\n(none)\n## Resolved contradictions\n(none)\n## User preferences observed\n(none)",
            }),
        ])
        script = [
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "first"}),
            ]),
            _archivist,
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "second"}),
            ]),
            _archivist,
        ]
        orch = _orch(script, tmp_env, mode="analyse")
        orch.bootstrap_conversation()
        list(orch.run("question 1"))
        folder_after_turn1 = orch.conv_folder
        list(orch.run("question 2"))
        assert orch.conv_folder == folder_after_turn1

    def test_analyse_multi_turn_turn_index_increments(self, tmp_env):
        """Turn index must increment on subsequent turns in analyse mode."""
        _archivist = LLMResponse(thinking="", content="", tool_calls=[
            ToolCall(name="return_to_user", arguments={
                "answer": "## Established facts\n(none)\n## Open threads\n(none)\n## Resolved contradictions\n(none)\n## User preferences observed\n(none)",
            }),
        ])
        script = [
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "a"}),
            ]),
            _archivist,
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "b"}),
            ]),
            _archivist,
        ]
        orch = _orch(script, tmp_env, mode="analyse")
        orch.bootstrap_conversation()
        list(orch.run("q1"))
        assert orch.turn_index == 0
        events2 = list(orch.run("q2"))
        assert orch.turn_index == 1
        assert any(isinstance(e, TurnStarted) and e.turn_index == 1 for e in events2)

    def test_close_conversation_sets_status_closed(self, tmp_env):
        from jeanmichel import db as jmdb
        orch = _orch([
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "ok"}),
            ]),
        ], tmp_env)
        list(orch.run("hi"))
        orch.close_conversation()
        with jmdb.connect() as conn:
            row = conn.execute(
                "SELECT status FROM conversations WHERE id=?", (orch.conv_id,)
            ).fetchone()
        assert row["status"] == "closed"

    def test_close_conversation_noop_if_awaiting_human(self, tmp_env):
        """close_conversation must NOT override awaiting_human status."""
        from jeanmichel import db as jmdb
        orch = _orch([
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "ok"}),
            ]),
        ], tmp_env)
        list(orch.run("hi"))
        # Manually set a request to awaiting_human to simulate ask_human in flight.
        with jmdb.connect() as conn:
            jm = jmdb.get_agent_by_code(conn, "jean-michel")
            conn.execute(
                "INSERT INTO requests (id, conversation_id, depth, agent_id, status, created_at) "
                "VALUES ('fake-req', ?, 0, ?, 'awaiting_human', datetime('now'))",
                (orch.conv_id, jm.id),
            )
        orch.close_conversation()
        with jmdb.connect() as conn:
            row = conn.execute(
                "SELECT status FROM conversations WHERE id=?", (orch.conv_id,)
            ).fetchone()
        # Conversation must remain active (not closed) because a request is awaiting_human.
        assert row["status"] != "closed"

    def test_resume_conversation_restores_state(self, tmp_env):
        """resume_conversation must reattach conv_folder and restore turn_index."""
        script = [
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "t0"}),
            ]),
        ]
        orch = _orch(script, tmp_env, mode="analyse")
        list(orch.run("first"))
        conv_id = orch.conv_id
        folder_path = str(orch.conv_folder)

        # Simulate a new session: fresh Orchestrator with same conv_id.
        orch2 = Orchestrator(
            llm=MockClient(script=[
                LLMResponse(thinking="", content="", tool_calls=[
                    ToolCall(name="return_to_user", arguments={"answer": "resumed"}),
                ]),
            ]),
            profile=PROFILE,
            mode="analyse",
            conv_id=conv_id,
        )
        orch2.resume_conversation(folder_path=folder_path, user_language="fr")
        assert orch2.conv_folder is not None
        assert orch2.turn_index == 0  # turn 0 was completed in original session

        events = list(orch2.run("second"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "resumed"
        assert orch2.turn_index == 1  # incremented from 0

    def test_close_then_resume_reactivates(self, tmp_env):
        from jeanmichel import db as jmdb
        orch = _orch([
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "ok"}),
            ]),
        ], tmp_env)
        list(orch.run("hi"))
        orch.close_conversation()

        orch2 = Orchestrator(
            llm=MockClient(script=[]),
            profile=PROFILE,
            mode="analyse",
            conv_id=orch.conv_id,
        )
        orch2.resume_conversation(str(orch.conv_folder), user_language="fr")

        with jmdb.connect() as conn:
            row = conn.execute(
                "SELECT status FROM conversations WHERE id=?", (orch.conv_id,)
            ).fetchone()
        assert row["status"] == "active"
