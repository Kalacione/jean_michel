"""Tests for set_task_class tool and the classify_first / plan_first orchestrator gates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jeanmichel import db
from jeanmichel.config import UserProfile
from jeanmichel.llm import MockClient
from jeanmichel.models import LLMResponse, ToolCall
from jeanmichel.orchestrator import (
    AgentStarted,
    FinalAnswer,
    Orchestrator,
    ToolCallEmitted,
    ToolResponseRecorded,
)

PROFILE = UserProfile(notes="test user")
_ARCHIVIST = LLMResponse(thinking="", content="", tool_calls=[
    ToolCall(name="return_to_user", arguments={
        "answer": (
            "## Established facts\n- ok\n"
            "## Open threads\n(none)\n"
            "## Resolved contradictions\n(none)\n"
            "## User preferences observed\n(none)"
        ),
    }),
])
_SUMMARIZER_REPORT = LLMResponse(thinking="", content="", tool_calls=[
    ToolCall(name="report_findings", arguments={"summary": "Done.", "confidence": "high"}),
])


def _orch(script, tmp_env, mode="analyse"):
    return Orchestrator(
        llm=MockClient(script=script),
        profile=PROFILE,
        mode=mode,
        ask_human_callback=lambda q, w: "n/a",
    )


def _delegate(agent="summarizer"):
    return ToolCall(name="delegate_to", arguments={
        "agent_code": agent,
        "briefing": "Do some research.",
        "expected": "summary",
        "support_files": [],
    })


# ── Tool unit tests ──────────────────────────────────────────────────────────

class TestSetTaskClassTool:
    def test_valid_single_fact(self, tmp_env):
        from jeanmichel.tools.set_task_class import make_spec
        with db.connect() as conn:
            conv_id = "abc123"
            db.create_conversation(conn, conv_id, str(tmp_env), user_language="fr", mode="analyse")
        spec = make_spec(conv_id)
        result = json.loads(spec.handler(task_class="single_fact"))
        assert "error_code" not in result
        assert result["task_class"] == "single_fact"

    def test_valid_medium_task(self, tmp_env):
        from jeanmichel.tools.set_task_class import make_spec
        with db.connect() as conn:
            conv_id = "abc456"
            db.create_conversation(conn, conv_id, str(tmp_env), user_language="fr", mode="analyse")
        spec = make_spec(conv_id)
        result = json.loads(spec.handler(task_class="medium_task"))
        assert "error_code" not in result

    def test_valid_deep_research(self, tmp_env):
        from jeanmichel.tools.set_task_class import make_spec
        with db.connect() as conn:
            conv_id = "abc789"
            db.create_conversation(conn, conv_id, str(tmp_env), user_language="fr", mode="analyse")
        spec = make_spec(conv_id)
        result = json.loads(spec.handler(task_class="deep_research"))
        assert "error_code" not in result
        # Persisted to DB
        with db.connect() as conn:
            tc, _ = db.get_pipeline_state(conn, conv_id)
        assert tc == "deep_research"

    def test_invalid_class_returns_error(self, tmp_env):
        from jeanmichel.tools.set_task_class import make_spec
        with db.connect() as conn:
            conv_id = "abcerr"
            db.create_conversation(conn, conv_id, str(tmp_env), user_language="fr", mode="analyse")
        spec = make_spec(conv_id)
        result = json.loads(spec.handler(task_class="mega_complex"))
        assert result["error_code"] == "invalid_task_class"


# ── Gate 1: classify_first ───────────────────────────────────────────────────

class TestClassifyFirstGate:
    def test_delegate_without_classify_is_blocked(self, tmp_env):
        """First delegate_to without set_task_class → error injected, classify_first."""
        orch = _orch([
            # jm tries to delegate directly → blocked
            LLMResponse(thinking="", content="", tool_calls=[_delegate()]),
            # jm classifies after seeing error
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="set_task_class", arguments={"task_class": "medium_task"}),
            ]),
            # jm delegates successfully
            LLMResponse(thinking="", content="", tool_calls=[_delegate()]),
            _SUMMARIZER_REPORT,
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "done"}),
            ]),
            _ARCHIVIST,
        ], tmp_env)
        events = list(orch.run("complex research"))
        # delegate_to was blocked once (no AgentStarted for summarizer until 2nd attempt)
        agents = [e.agent_code for e in events if isinstance(e, AgentStarted)]
        assert agents.count("summarizer") == 1  # only spawned once, correctly
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "done"

    def test_classify_error_is_injected_as_tool_response(self, tmp_env):
        """The classify_first error is visible to the LLM as a tool response."""
        orch = _orch([
            LLMResponse(thinking="", content="", tool_calls=[_delegate()]),
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="set_task_class", arguments={"task_class": "single_fact"}),
            ]),
            LLMResponse(thinking="", content="", tool_calls=[_delegate()]),
            _SUMMARIZER_REPORT,
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "ok"}),
            ]),
            _ARCHIVIST,
        ], tmp_env)
        events = list(orch.run("research"))
        recorded = [e for e in events if isinstance(e, ToolResponseRecorded)]
        # set_task_class response should be in recorded tool responses
        assert any(e.tool_name == "set_task_class" for e in recorded)

    def test_gate_not_active_in_vocal_mode(self, tmp_env):
        """In vocal mode, delegate_to without set_task_class passes immediately."""
        orch = _orch([
            LLMResponse(thinking="", content="", tool_calls=[_delegate()]),
            _SUMMARIZER_REPORT,
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "done"}),
            ]),
            # vocal mode has no archivist
        ], tmp_env, mode="vocal")
        events = list(orch.run("tell me something"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "done"

    def test_task_class_persisted_across_turns(self, tmp_env):
        """Turn 2: task_class already set → gate 1 does not fire again."""
        archivist_resp = _ARCHIVIST
        orch = _orch([
            # Turn 1: classify + delegate
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="set_task_class", arguments={"task_class": "medium_task"}),
            ]),
            LLMResponse(thinking="", content="", tool_calls=[_delegate()]),
            _SUMMARIZER_REPORT,
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "turn1 done"}),
            ]),
            archivist_resp,
            # Turn 2: delegate directly without re-classifying → should pass
            LLMResponse(thinking="", content="", tool_calls=[_delegate()]),
            _SUMMARIZER_REPORT,
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "turn2 done"}),
            ]),
            archivist_resp,
        ], tmp_env)
        list(orch.run("first question"))
        events2 = list(orch.run("second question"))
        fa2 = next(e for e in events2 if isinstance(e, FinalAnswer))
        assert fa2.text == "turn2 done"


# ── Gate 2: plan_first (deep_research) ──────────────────────────────────────

class TestPlanFirstGate:
    def _todo_write(self):
        return ToolCall(name="manage_todo_list", arguments={
            "operation": "write",
            "todos": [
                {"id": "t1", "title": "Search info", "status": "pending",
                 "assignee_hint": "web-search-specialist"},
                {"id": "t2", "title": "Write report", "status": "pending",
                 "assignee_hint": "document-builder"},
            ],
        })

    def test_deep_research_without_todo_is_blocked(self, tmp_env):
        """After set_task_class('deep_research'), delegate_to without manage_todo_list → blocked."""
        orch = _orch([
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="set_task_class", arguments={"task_class": "deep_research"}),
            ]),
            # jm tries to delegate → blocked (plan_first)
            LLMResponse(thinking="", content="", tool_calls=[_delegate()]),
            # jm writes todo
            LLMResponse(thinking="", content="", tool_calls=[self._todo_write()]),
            # jm delegates successfully
            LLMResponse(thinking="", content="", tool_calls=[_delegate()]),
            _SUMMARIZER_REPORT,
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "deep done"}),
            ]),
            _ARCHIVIST,
        ], tmp_env)
        events = list(orch.run("deep research request"))
        # summarizer only spawned once (2nd attempt, after todo written)
        agents = [e.agent_code for e in events if isinstance(e, AgentStarted)]
        assert agents.count("summarizer") == 1
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "deep done"

    def test_deep_research_with_todo_passes(self, tmp_env):
        """set_task_class('deep_research') + manage_todo_list → delegate_to passes."""
        orch = _orch([
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="set_task_class", arguments={"task_class": "deep_research"}),
            ]),
            LLMResponse(thinking="", content="", tool_calls=[self._todo_write()]),
            LLMResponse(thinking="", content="", tool_calls=[_delegate()]),
            _SUMMARIZER_REPORT,
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "perfect"}),
            ]),
            _ARCHIVIST,
        ], tmp_env)
        events = list(orch.run("deep research with planning"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "perfect"

    def test_medium_task_no_todo_required(self, tmp_env):
        """medium_task classification → gate 2 does not fire, delegate_to passes."""
        orch = _orch([
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="set_task_class", arguments={"task_class": "medium_task"}),
            ]),
            LLMResponse(thinking="", content="", tool_calls=[_delegate()]),
            _SUMMARIZER_REPORT,
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "medium done"}),
            ]),
            _ARCHIVIST,
        ], tmp_env)
        events = list(orch.run("medium task"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "medium done"

    def test_todo_written_in_prior_turn_satisfies_gate2(self, tmp_env):
        """Turn 2: todo.json already exists from turn 1 → gate 2 does not fire."""
        orch = _orch([
            # Turn 1: deep_research + todo + delegate
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="set_task_class", arguments={"task_class": "deep_research"}),
            ]),
            LLMResponse(thinking="", content="", tool_calls=[self._todo_write()]),
            LLMResponse(thinking="", content="", tool_calls=[_delegate()]),
            _SUMMARIZER_REPORT,
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "turn1"}),
            ]),
            _ARCHIVIST,
            # Turn 2: task_class still deep_research, todo.json exists → no gate
            LLMResponse(thinking="", content="", tool_calls=[_delegate()]),
            _SUMMARIZER_REPORT,
            LLMResponse(thinking="", content="", tool_calls=[
                ToolCall(name="return_to_user", arguments={"answer": "turn2"}),
            ]),
            _ARCHIVIST,
        ], tmp_env)
        list(orch.run("turn 1"))
        events2 = list(orch.run("turn 2 follow-up"))
        fa = next(e for e in events2 if isinstance(e, FinalAnswer))
        assert fa.text == "turn2"
