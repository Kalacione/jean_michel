"""Tests for `jeanmichel.orchestrator_v2` — Tier 1 main loop + Tier 2 subagent."""

from __future__ import annotations

import json
from pathlib import Path

from jeanmichel.llm import MockClient
from jeanmichel.models import ConversationState
from jeanmichel.orchestrator_v2 import (
    AgentSpec,
    SubResult,
    _build_tools_payload,
    _initialize_state,
    run_main_loop,
    spawn_subagent,
)

from ._orchestrator_helpers import (
    assistant_response,
    make_agent,
    make_echo_tool,
    tool_call,
)

# =============================================================================
# Section 1 : helpers / state initialization
# =============================================================================


def test_build_tools_payload_router_includes_ask_human_and_delegate_to():
    agent = make_agent("jean-michel", role="router")
    payload = _build_tools_payload(agent, {})
    names = {p["function"]["name"] for p in payload}
    assert "ask_human" in names
    assert "delegate_to" in names
    assert "report_back" not in names  # router uses implicit termination


def test_build_tools_payload_specialist_includes_report_back_not_ask_human():
    agent = make_agent("summarizer", role="specialist")
    payload = _build_tools_payload(agent, {})
    names = {p["function"]["name"] for p in payload}
    assert "report_back" in names
    assert "delegate_to" in names  # specialists can also delegate (nested)
    assert "ask_human" not in names  # main-agent-only per §5 doc 06


def test_build_tools_payload_finalizer_no_control_verb():
    agent = make_agent("synthesizer", role="finalizer")
    payload = _build_tools_payload(agent, {})
    names = {p["function"]["name"] for p in payload}
    assert "ask_human" not in names
    assert "delegate_to" not in names
    assert "report_back" not in names


def test_build_tools_payload_filters_by_grants():
    agent = make_agent(
        "specialist", role="specialist", tool_grants={"echo", "calculator"}
    )
    registry = {
        "echo": make_echo_tool("echo"),
        "calculator": make_echo_tool("calculator"),
        "not_granted": make_echo_tool("not_granted"),
    }
    payload = _build_tools_payload(agent, registry)
    names = {p["function"]["name"] for p in payload}
    assert "echo" in names
    assert "calculator" in names
    assert "not_granted" not in names


def test_initialize_state_partitions_budget(monkeypatch):
    # Pin the model's ctx window explicitly so the partition is independent of the
    # (VRAM-driven) default — the override mechanism the orchestrator relies on.
    monkeypatch.setenv("JEANMICHEL_CTX_WINDOW_mock_model", "128000")
    state = ConversationState()
    messages = [{"role": "system", "content": "x" * 400}]  # ~100 tokens
    tools = [{"function": {"name": "t", "description": "d", "parameters": {}}}]
    _initialize_state(state, messages, tools, "mock-model")
    assert state.system_reserve_tokens > 0
    assert state.output_reserve_tokens > 0
    assert state.working_budget > 0
    # OUTPUT_RESERVE_RATIO = 0.15 by default → output_reserve ≈ 15 % of 128k.
    assert 15_000 <= state.output_reserve_tokens <= 25_000


# =============================================================================
# Section 2 : Main loop — simple paths (no delegation)
# =============================================================================


def test_simple_one_turn_returns_assistant_content(tmp_path: Path):
    """DoD : un tour, le main agent émet un assistant sans tool_calls → return content."""
    agent = make_agent("jean-michel", role="router")
    mock = MockClient(script=[assistant_response("The answer is 42.")])
    result = run_main_loop(
        conv_folder=tmp_path,
        agent=agent,
        tools_registry={},
        llm_client=mock,
        user_text="What is the answer?",
    )
    assert result == "The answer is 42."
    assert len(mock.calls_v2) == 1


def test_main_loop_persists_messages_and_state(tmp_path: Path):
    agent = make_agent("jean-michel", role="router")
    mock = MockClient(script=[assistant_response("hi back")])
    run_main_loop(
        conv_folder=tmp_path,
        agent=agent,
        tools_registry={},
        llm_client=mock,
        user_text="hi",
    )
    assert (tmp_path / "messages.json").exists()
    assert (tmp_path / "state.json").exists()
    # events.jsonl populated with at least RequestStarted + LLMCallStarted + LLMCallCompleted + RequestCompleted
    assert (tmp_path / "events.jsonl").exists()
    events_text = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "RequestStarted" in events_text
    assert "RequestCompleted" in events_text


def test_main_loop_with_native_tool_call(tmp_path: Path):
    """DoD : un tool_call, exécution, append role=tool, suivi d'un assistant final."""
    agent = make_agent(
        "jean-michel", role="router", tool_grants={"echo"}
    )
    registry = {"echo": make_echo_tool()}

    mock = MockClient(script=[
        # Turn 1 : emit a tool_call
        assistant_response(
            "calling echo", tool_calls=[tool_call("echo", text="hello")]
        ),
        # Turn 2 : final answer
        assistant_response("The echo replied: hello."),
    ])

    result = run_main_loop(
        conv_folder=tmp_path,
        agent=agent,
        tools_registry=registry,
        llm_client=mock,
        user_text="echo hello",
    )
    assert result == "The echo replied: hello."
    assert len(mock.calls_v2) == 2

    # The second call should have seen the tool result in its messages[].
    second_call_msgs = mock.calls_v2[1]["messages"]
    tool_msgs = [m for m in second_call_msgs if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_name"] == "echo"
    # The tool result content is the JSON string from the handler.
    parsed = json.loads(tool_msgs[0]["content"])
    assert parsed["echo"] == "hello"


def test_main_loop_unknown_tool_returns_error(tmp_path: Path):
    """If the LLM hallucinates a tool name, the loop appends an error message
    and continues (LLM gets a chance to recover)."""
    agent = make_agent(
        "jean-michel", role="router", tool_grants={"echo"}
    )
    registry = {"echo": make_echo_tool()}
    mock = MockClient(script=[
        assistant_response("calling x", tool_calls=[tool_call("hallucinated_tool")]),
        assistant_response("Final answer despite the error."),
    ])
    result = run_main_loop(
        conv_folder=tmp_path,
        agent=agent,
        tools_registry=registry,
        llm_client=mock,
        user_text="hi",
    )
    assert result == "Final answer despite the error."
    # The tool message in second call has an error.
    second_msgs = mock.calls_v2[1]["messages"]
    tool_msg = next(m for m in second_msgs if m.get("role") == "tool")
    parsed = json.loads(tool_msg["content"])
    # Either 'denied' (grant rejected) or 'unknown_tool' depending on the path.
    assert "error" in parsed


def test_main_loop_max_iterations_aborts(tmp_path: Path):
    """If the LLM keeps calling tools without ever concluding, we abort."""
    agent = make_agent("jean-michel", role="router", tool_grants={"echo"})
    registry = {"echo": make_echo_tool()}
    # Infinite loop : every response is a tool_call.
    mock = MockClient(script=[
        assistant_response("calling", tool_calls=[tool_call("echo", text=f"t{i}")])
        for i in range(20)
    ])
    result = run_main_loop(
        conv_folder=tmp_path,
        agent=agent,
        tools_registry=registry,
        llm_client=mock,
        user_text="hi",
        max_iterations=3,
    )
    assert result.startswith("[Orchestrator aborted")
    assert "max_iterations" in result


# =============================================================================
# Section 3 : Delegation (level 1)
# =============================================================================


def test_main_loop_with_delegation_level_1(tmp_path: Path):
    """DoD : main → delegate_to → subagent → report_back → main reprend."""
    main_agent = make_agent(
        "jean-michel",
        role="router",
        delegation_targets={"summarizer"},
    )
    sub_agent = make_agent("summarizer", role="specialist")

    def agent_resolver(code: str) -> AgentSpec | None:
        return sub_agent if code == "summarizer" else None

    mock = MockClient(script=[
        # Main turn 1 : delegate
        assistant_response(
            "delegating to summarizer",
            tool_calls=[tool_call(
                "delegate_to",
                agent_code="summarizer",
                briefing="summarize the user's question",
            )],
        ),
        # Subagent turn 1 : report_back
        assistant_response(
            "",
            tool_calls=[tool_call(
                "report_back",
                summary="user asked about X.",
                files_produced=[],
                confidence="high",
            )],
        ),
        # Main turn 2 : final answer
        assistant_response("Here is the summary: user asked about X."),
    ])

    result = run_main_loop(
        conv_folder=tmp_path,
        agent=main_agent,
        tools_registry={},
        llm_client=mock,
        user_text="please summarize",
        agent_resolver=agent_resolver,
    )
    assert result == "Here is the summary: user asked about X."
    assert len(mock.calls_v2) == 3

    # The subagent had a fresh messages[] (no leak from parent).
    sub_call = mock.calls_v2[1]
    sub_msgs = sub_call["messages"]
    assert sub_msgs[0]["role"] == "system"
    assert "You are summarizer" in sub_msgs[0]["content"]
    assert sub_msgs[1]["role"] == "user"
    assert "summarize the user's question" in sub_msgs[1]["content"]
    # No leak from the parent : the user's original question isn't there.
    assert not any("please summarize" in m.get("content", "") for m in sub_msgs)


def test_delegation_persists_subagent_messages(tmp_path: Path):
    main_agent = make_agent(
        "jean-michel", role="router", delegation_targets={"summarizer"}
    )
    sub_agent = make_agent("summarizer", role="specialist")

    mock = MockClient(script=[
        assistant_response(
            "delegating",
            tool_calls=[tool_call(
                "delegate_to",
                agent_code="summarizer",
                briefing="x",
            )],
        ),
        assistant_response(
            "",
            tool_calls=[tool_call(
                "report_back", summary="done", files_produced=[], confidence="high"
            )],
        ),
        assistant_response("done"),
    ])

    run_main_loop(
        conv_folder=tmp_path,
        agent=main_agent,
        tools_registry={},
        llm_client=mock,
        user_text="hi",
        agent_resolver=lambda code: sub_agent if code == "summarizer" else None,
    )

    sub_files = list(tmp_path.glob("subagent_*.json"))
    assert len(sub_files) == 1
    sub_content = json.loads(sub_files[0].read_text())
    # Subagent messages include : system, user (briefing), assistant (with tool_call).
    assert sub_content[0]["role"] == "system"
    assert sub_content[1]["role"] == "user"
    assert sub_content[2]["role"] == "assistant"


# =============================================================================
# Section 4 : Nested delegation (level 2)
# =============================================================================


def test_nested_delegation_level_2(tmp_path: Path):
    """DoD : main → sub A → sub B → report_back A → A continue → report_back main."""
    main_agent = make_agent(
        "jean-michel", role="router", delegation_targets={"agent-A"}
    )
    agent_a = make_agent(
        "agent-A", role="specialist", delegation_targets={"agent-B"}
    )
    agent_b = make_agent("agent-B", role="specialist")

    def agent_resolver(code: str) -> AgentSpec | None:
        return {"agent-A": agent_a, "agent-B": agent_b}.get(code)

    mock = MockClient(script=[
        # Main turn 1 : delegate to A
        assistant_response(
            "→ A",
            tool_calls=[tool_call(
                "delegate_to", agent_code="agent-A", briefing="do something"
            )],
        ),
        # A turn 1 : delegate to B
        assistant_response(
            "→ B",
            tool_calls=[tool_call(
                "delegate_to", agent_code="agent-B", briefing="do sub-something"
            )],
        ),
        # B turn 1 : report_back
        assistant_response(
            "",
            tool_calls=[tool_call(
                "report_back", summary="B done", files_produced=[], confidence="high"
            )],
        ),
        # A turn 2 : report_back (after seeing B's result)
        assistant_response(
            "",
            tool_calls=[tool_call(
                "report_back", summary="A done (B contributed)", files_produced=[], confidence="high"
            )],
        ),
        # Main turn 2 : final answer
        assistant_response("Both A and B completed."),
    ])

    result = run_main_loop(
        conv_folder=tmp_path,
        agent=main_agent,
        tools_registry={},
        llm_client=mock,
        user_text="do the thing",
        agent_resolver=agent_resolver,
    )
    assert result == "Both A and B completed."

    # Two subagent files persisted (one for A, one for B).
    sub_files = list(tmp_path.glob("subagent_*.json"))
    assert len(sub_files) == 2

    # B's messages don't see A's history.
    sub_contents = [json.loads(f.read_text()) for f in sub_files]
    b_msgs = next(
        c for c in sub_contents
        if any("You are agent-B" in m.get("content", "") for m in c)
    )
    # User message is the briefing, not anything from A's middle.
    assert any("do sub-something" in m.get("content", "") for m in b_msgs)


# =============================================================================
# Section 5 : Guard rails (depth, whitelist, report_back validation)
# =============================================================================


def test_max_depth_refuses_further_delegation(tmp_path: Path, monkeypatch):
    """When depth_current + 1 > MAX_DEPTH, PreToolUse denies delegate_to."""
    # Lower MAX_DEPTH for the test to keep the depth chain short.
    import jeanmichel.config as cfg
    monkeypatch.setattr(cfg, "MAX_DEPTH", 1)
    import jeanmichel.hooks as hooks_mod
    monkeypatch.setattr(hooks_mod, "MAX_DEPTH", 1)

    main_agent = make_agent(
        "jean-michel", role="router", delegation_targets={"agent-A"}
    )
    agent_a = make_agent(
        "agent-A", role="specialist", delegation_targets={"agent-B"}
    )
    agent_b = make_agent("agent-B", role="specialist")

    def agent_resolver(code: str) -> AgentSpec | None:
        return {"agent-A": agent_a, "agent-B": agent_b}.get(code)

    mock = MockClient(script=[
        # Main → A (depth 0 → 1, OK)
        assistant_response(
            "→ A",
            tool_calls=[tool_call("delegate_to", agent_code="agent-A", briefing="x")],
        ),
        # A tries → B (depth 1 → 2, MUST be denied)
        assistant_response(
            "→ B",
            tool_calls=[tool_call("delegate_to", agent_code="agent-B", briefing="y")],
        ),
        # A sees the denial and reports back.
        assistant_response(
            "",
            tool_calls=[tool_call(
                "report_back",
                summary="couldn't delegate further",
                files_produced=[],
                confidence="low",
                low_confidence_reason="MAX_DEPTH reached",
            )],
        ),
        # Main : final answer
        assistant_response("Depth limit hit."),
    ])

    result = run_main_loop(
        conv_folder=tmp_path,
        agent=main_agent,
        tools_registry={},
        llm_client=mock,
        user_text="go",
        agent_resolver=agent_resolver,
    )
    assert result == "Depth limit hit."

    # A's messages include the denial response.
    sub_files = list(tmp_path.glob("subagent_*.json"))
    a_msgs = json.loads(sub_files[0].read_text())
    tool_msgs = [m for m in a_msgs if m.get("role") == "tool"]
    # The first tool message in A's messages should be the denied delegate_to.
    first_tool_content = json.loads(tool_msgs[0]["content"])
    assert "error" in first_tool_content
    assert "MAX_DEPTH" in first_tool_content.get("summary", "")


def test_delegation_whitelist_blocks_unauthorized_target(tmp_path: Path):
    """An agent can only delegate to codes in its delegation_targets whitelist."""
    main_agent = make_agent(
        "jean-michel",
        role="router",
        delegation_targets={"summarizer"},  # only summarizer allowed
    )
    bad_agent = make_agent("code-runner", role="specialist")

    mock = MockClient(script=[
        # Main tries to delegate to an unauthorised agent.
        assistant_response(
            "→ code-runner",
            tool_calls=[tool_call(
                "delegate_to", agent_code="code-runner", briefing="run code"
            )],
        ),
        # Main sees denial and concludes.
        assistant_response("I cannot delegate to code-runner from here."),
    ])

    result = run_main_loop(
        conv_folder=tmp_path,
        agent=main_agent,
        tools_registry={},
        llm_client=mock,
        user_text="run code",
        agent_resolver=lambda c: bad_agent,
    )
    assert "cannot delegate" in result

    # Confirm denial was injected in messages.
    second_msgs = mock.calls_v2[1]["messages"]
    tool_msg = next(m for m in second_msgs if m.get("role") == "tool")
    parsed = json.loads(tool_msg["content"])
    assert "error" in parsed
    assert "whitelist" in parsed.get("summary", "")


def test_report_back_low_confidence_without_reason_is_rejected(tmp_path: Path):
    """A subagent emitting report_back(confidence='low') without low_confidence_reason
    must re-emit. The first attempt is rejected with an error message in messages[]."""
    main_agent = make_agent(
        "jean-michel", role="router", delegation_targets={"summarizer"}
    )
    sub_agent = make_agent("summarizer", role="specialist")

    mock = MockClient(script=[
        # Main → delegate
        assistant_response(
            "→",
            tool_calls=[tool_call(
                "delegate_to", agent_code="summarizer", briefing="b"
            )],
        ),
        # Sub : invalid report_back (low without reason)
        assistant_response(
            "",
            tool_calls=[tool_call(
                "report_back",
                summary="not sure",
                confidence="low",
                # low_confidence_reason MISSING
                files_produced=[],
            )],
        ),
        # Sub : retry with valid reason
        assistant_response(
            "",
            tool_calls=[tool_call(
                "report_back",
                summary="not sure",
                confidence="low",
                low_confidence_reason="Source was a disambiguation page.",
                files_produced=[],
            )],
        ),
        # Main : final answer
        assistant_response("OK, low confidence noted."),
    ])

    result = run_main_loop(
        conv_folder=tmp_path,
        agent=main_agent,
        tools_registry={},
        llm_client=mock,
        user_text="x",
        agent_resolver=lambda c: sub_agent if c == "summarizer" else None,
    )
    assert result == "OK, low confidence noted."
    # Subagent went 2 iterations (first rejected, second succeeded).
    # Total LLM calls : 1 main + 2 sub + 1 main = 4.
    assert len(mock.calls_v2) == 4

    # The subagent persisted file contains the rejection message.
    sub_files = list(tmp_path.glob("subagent_*.json"))
    sub_msgs = json.loads(sub_files[0].read_text())
    tool_msgs = [m for m in sub_msgs if m.get("role") == "tool"]
    # The first tool message is the invalid report_back rejection.
    rejection_content = json.loads(tool_msgs[0]["content"])
    assert rejection_content.get("error") == "invalid_report_back"
    assert "low_confidence_reason" in rejection_content.get("summary", "")


# =============================================================================
# Section 6 : ask_human handling
# =============================================================================


def test_ask_human_main_agent_invokes_callback(tmp_path: Path):
    agent = make_agent("jean-michel", role="router")

    captured: list[tuple[str, str, list[str], bool]] = []

    def ask_human_cb(question: str, why: str, choices: list[str], multi: bool) -> str:
        captured.append((question, why, choices, multi))
        return "Yes, of course."

    mock = MockClient(script=[
        assistant_response(
            "asking",
            tool_calls=[tool_call(
                "ask_human", question="Can I proceed?", why="user mentioned X"
            )],
        ),
        assistant_response("Great, proceeding."),
    ])

    result = run_main_loop(
        conv_folder=tmp_path,
        agent=agent,
        tools_registry={},
        llm_client=mock,
        user_text="x",
        ask_human_callback=ask_human_cb,
    )
    assert result == "Great, proceeding."
    # No choices passed → empty list + multi False (free-text question).
    assert captured == [("Can I proceed?", "user mentioned X", [], False)]

    # The human reply appears as a role=user message in the next call.
    second_msgs = mock.calls_v2[1]["messages"]
    # The human reply is the last genuine user message (a transient [ORCHESTRATOR] mode
    # banner may follow it — that's a nudge, not user content).
    human_msgs = [m for m in second_msgs
                  if m.get("role") == "user" and not (m.get("content") or "").startswith("[ORCHESTRATOR]")]
    assert human_msgs[-1]["content"] == "Yes, of course."


def test_ask_human_forwards_choices_and_multi(tmp_path: Path):
    """choices + multi reach the callback ; the reply is still a role=user msg."""
    agent = make_agent("jean-michel", role="router")

    captured: list[tuple[str, str, list[str], bool]] = []

    def ask_human_cb(question: str, why: str, choices: list[str], multi: bool) -> str:
        captured.append((question, why, choices, multi))
        return "Red, Blue"

    mock = MockClient(script=[
        assistant_response(
            "asking",
            tool_calls=[tool_call(
                "ask_human", question="Which colors?", why="palette unclear",
                choices=["Red", "Green", "Blue"], multi=True,
            )],
        ),
        assistant_response("Got it."),
    ])

    result = run_main_loop(
        conv_folder=tmp_path,
        agent=agent,
        tools_registry={},
        llm_client=mock,
        user_text="x",
        ask_human_callback=ask_human_cb,
    )
    assert result == "Got it."
    assert captured == [("Which colors?", "palette unclear", ["Red", "Green", "Blue"], True)]
    second_msgs = mock.calls_v2[1]["messages"]
    human_msgs = [m for m in second_msgs
                  if m.get("role") == "user" and not (m.get("content") or "").startswith("[ORCHESTRATOR]")]
    assert human_msgs[-1]["content"] == "Red, Blue"


def test_ask_human_in_subagent_is_unavailable(tmp_path: Path):
    """Subagents must not have ask_human in their payload — and if a malicious /
    confused subagent emits it anyway, the loop returns an error."""
    main_agent = make_agent(
        "jean-michel", role="router", delegation_targets={"summarizer"}
    )
    sub_agent = make_agent("summarizer", role="specialist")

    mock = MockClient(script=[
        # Main → delegate
        assistant_response(
            "→",
            tool_calls=[tool_call(
                "delegate_to", agent_code="summarizer", briefing="b"
            )],
        ),
        # Sub tries ask_human (it shouldn't even know it exists, but defensively
        # we verify the loop refuses).
        assistant_response(
            "asking",
            tool_calls=[tool_call("ask_human", question="hi", why="curious")],
        ),
        # Sub correctly concludes via report_back.
        assistant_response(
            "",
            tool_calls=[tool_call(
                "report_back", summary="done", files_produced=[], confidence="high"
            )],
        ),
        # Main : final
        assistant_response("OK."),
    ])

    run_main_loop(
        conv_folder=tmp_path,
        agent=main_agent,
        tools_registry={},
        llm_client=mock,
        user_text="x",
        agent_resolver=lambda c: sub_agent if c == "summarizer" else None,
    )

    # The subagent's payload did NOT include ask_human (verified by
    # _build_tools_payload covered separately) — but if the LLM emits it
    # anyway, the loop returns an error message inline.
    sub_files = list(tmp_path.glob("subagent_*.json"))
    sub_msgs = json.loads(sub_files[0].read_text())
    tool_msgs = [m for m in sub_msgs if m.get("role") == "tool"]
    # First tool msg is the ask_human refusal.
    err = json.loads(tool_msgs[0]["content"])
    assert err.get("error") == "ask_human_not_available"


def test_main_agent_without_callback_refuses_ask_human(tmp_path: Path):
    """No callback → ask_human errors out cleanly."""
    agent = make_agent("jean-michel", role="router")
    mock = MockClient(script=[
        assistant_response(
            "asking",
            tool_calls=[tool_call("ask_human", question="q", why="w")],
        ),
        assistant_response("OK without."),
    ])
    result = run_main_loop(
        conv_folder=tmp_path,
        agent=agent,
        tools_registry={},
        llm_client=mock,
        user_text="x",
        ask_human_callback=None,
    )
    assert result == "OK without."
    second_msgs = mock.calls_v2[1]["messages"]
    tool_msg = next(m for m in second_msgs if m.get("role") == "tool")
    parsed = json.loads(tool_msg["content"])
    assert parsed["error"] == "ask_human_not_available"


# =============================================================================
# Section 7 : Event emission
# =============================================================================


def test_events_emitted_for_simple_turn(tmp_path: Path):
    """The main loop emits RequestStarted, LLMCall*, RequestCompleted."""
    agent = make_agent("jean-michel", role="router")
    mock = MockClient(script=[assistant_response("hi back")])

    captured_events: list = []
    run_main_loop(
        conv_folder=tmp_path,
        agent=agent,
        tools_registry={},
        llm_client=mock,
        user_text="hi",
        event_emitter=captured_events.append,
    )

    types = [type(e).__name__ for e in captured_events]
    assert "RequestStarted" in types
    assert "LLMCallStarted" in types
    assert "LLMCallCompleted" in types
    assert "RequestCompleted" in types


def test_events_emitted_for_delegation(tmp_path: Path):
    """DelegationStarted and DelegationCompleted are emitted around the subagent."""
    main_agent = make_agent(
        "jean-michel", role="router", delegation_targets={"summarizer"}
    )
    sub_agent = make_agent("summarizer", role="specialist")
    mock = MockClient(script=[
        assistant_response(
            "→",
            tool_calls=[tool_call(
                "delegate_to", agent_code="summarizer", briefing="b"
            )],
        ),
        assistant_response(
            "",
            tool_calls=[tool_call(
                "report_back", summary="s", files_produced=[], confidence="high"
            )],
        ),
        assistant_response("end"),
    ])

    captured: list = []
    run_main_loop(
        conv_folder=tmp_path,
        agent=main_agent,
        tools_registry={},
        llm_client=mock,
        user_text="x",
        agent_resolver=lambda c: sub_agent if c == "summarizer" else None,
        event_emitter=captured.append,
    )
    types = [type(e).__name__ for e in captured]
    assert "DelegationStarted" in types
    assert "DelegationCompleted" in types


def test_hook_fired_event_on_denial(tmp_path: Path):
    """When PreToolUse denies a call, a HookFired event is emitted."""
    main_agent = make_agent(
        "jean-michel", role="router", delegation_targets={"summarizer"}
    )
    mock = MockClient(script=[
        # Attempt unauthorised delegate_to → denial → HookFired
        assistant_response(
            "→",
            tool_calls=[tool_call(
                "delegate_to", agent_code="unknown", briefing="b"
            )],
        ),
        assistant_response("done"),
    ])
    captured: list = []
    run_main_loop(
        conv_folder=tmp_path,
        agent=main_agent,
        tools_registry={},
        llm_client=mock,
        user_text="x",
        agent_resolver=lambda c: None,
        event_emitter=captured.append,
    )
    hook_events = [e for e in captured if type(e).__name__ == "HookFired"]
    assert len(hook_events) >= 1
    assert any("whitelist" in e.reason for e in hook_events)


# =============================================================================
# Section 8 : spawn_subagent direct API
# =============================================================================


def test_spawn_subagent_returns_subresult_on_clean_report_back(tmp_path: Path):
    sub_agent = make_agent("specialist", role="specialist")
    parent_state = ConversationState(depth_current=0)

    mock = MockClient(script=[
        assistant_response(
            "",
            tool_calls=[tool_call(
                "report_back",
                summary="all done",
                files_produced=["x.md"],
                confidence="high",
            )],
        ),
    ])

    result = spawn_subagent(
        conv_folder=tmp_path,
        sub_agent=sub_agent,
        tools_registry={},
        llm_client=mock,
        briefing="do x",
        support_files=[],
        expected="a summary",
        parent_state=parent_state,
        parent_agent_code="jean-michel",
    )
    assert isinstance(result, SubResult)
    assert result.agent == "specialist"
    assert result.summary == "all done"
    assert result.files_produced == ["x.md"]
    assert result.confidence == "high"


def test_spawn_subagent_returns_low_confidence_on_abort(tmp_path: Path):
    """If the subagent can't conclude (LLM script exhausted), SubResult is low-confidence."""
    sub_agent = make_agent("specialist", role="specialist")
    parent_state = ConversationState(depth_current=0)
    mock = MockClient(script=[])  # no responses available

    result = spawn_subagent(
        conv_folder=tmp_path,
        sub_agent=sub_agent,
        tools_registry={},
        llm_client=mock,
        briefing="x",
        support_files=[],
        expected="",
        parent_state=parent_state,
        max_iterations=1,
    )
    assert result.confidence == "low"
    assert result.low_confidence_reason  # non-empty


def test_subagent_prose_becomes_implicit_report_back(tmp_path: Path):
    """A specialist that CONCLUDES IN PROSE (no report_back tool_call) has its work
    preserved as an implicit report_back (confidence=medium) — not discarded, and
    WITHOUT a confusing '[ORCHESTRATOR] must terminate' role=user corrective (conv
    b2701c32 : qwen3:14b wrote a full analysis then never called report_back)."""
    sub_agent = make_agent("specialist", role="specialist")
    parent_state = ConversationState(depth_current=0)
    report = "Report on v1 usage: it is still referenced in db/ and tests/; cleanup is partial."
    mock = MockClient(script=[assistant_response(report)])
    result = spawn_subagent(
        conv_folder=tmp_path,
        sub_agent=sub_agent,
        tools_registry={},
        llm_client=mock,
        briefing="x",
        support_files=[],
        expected="",
        parent_state=parent_state,
        max_iterations=5,
    )
    # The prose IS the conclusion : captured as the summary, not lost.
    assert result.confidence == "medium"
    assert result.summary == report
    # No corrective injected, and only ONE LLM call (no wasted retries).
    assert len(mock.calls_v2) == 1
    injected = [
        m for call in mock.calls_v2 for m in call["messages"]
        if m.get("role") == "user" and "must terminate" in (m.get("content") or "")
    ]
    assert not injected


def test_subagent_empty_turn_still_aborts(tmp_path: Path):
    """A truly EMPTY turn (no tool_call AND no content) has nothing to salvage →
    bounded retries then abort low (no implicit report_back from emptiness)."""
    sub_agent = make_agent("specialist", role="specialist")
    parent_state = ConversationState(depth_current=0)
    mock = MockClient(script=[assistant_response(""), assistant_response(""), assistant_response("")])
    result = spawn_subagent(
        conv_folder=tmp_path, sub_agent=sub_agent, tools_registry={}, llm_client=mock,
        briefing="x", support_files=[], expected="", parent_state=parent_state, max_iterations=5,
    )
    assert result.confidence == "low"
    assert "neither a tool_call nor any content" in result.low_confidence_reason


# Section 7 : PLAN mode — the plan must be authored via plan_write (NOT todo_write)


def test_plan_mode_forces_plan_write_before_concluding(tmp_path: Path):
    """A PLAN turn must author the plan via plan_write before it may conclude. A prose-only
    conclusion is refused (bounded retries), re-nudged toward plan_write (NOT todo_write —
    the todo is built later, at execution), and the premature prose is dropped so it never
    shows up as a duplicate 'plan' bubble."""
    agent = make_agent("jean-michel", role="router")
    mock = MockClient(script=[
        assistant_response("Here is my plan, in prose."),
        assistant_response("Still just prose."),
        assistant_response("Prose once more."),
    ])
    result = run_main_loop(
        conv_folder=tmp_path,
        agent=agent,
        tools_registry={},
        llm_client=mock,
        user_text="build feature X",
        plan_mode=True,
    )
    # Bounded retries exhausted → concludes ; but the corrective fired meanwhile.
    assert result == "Prose once more."
    last_msgs = mock.calls_v2[-1]["messages"]
    correctives = [
        m for m in last_msgs
        if m.get("role") == "user" and "without recording the plan" in (m.get("content") or "")
    ]
    assert correctives, "a prose-only PLAN conclusion must be nudged toward plan_write"
    assert all("plan_write" in m["content"] for m in correctives)  # the new tool, not todo_write
    # The premature prose plans were DROPPED (not left as duplicate assistant bubbles).
    assert not [m for m in last_msgs if m.get("role") == "assistant"]


def test_plan_mode_halts_after_plan_write(tmp_path: Path):
    """A PLAN turn STOPS the moment plan_write has run — it must NOT chain into todo/execution
    /answering (gemma4 planned+executed+answered in one turn, conv 00-17). Deterministic."""
    from jeanmichel.todo import load_plan, load_todo
    from jeanmichel.tools import plan_write as plan_write_mod

    agent = make_agent("jean-michel", role="router", tool_grants={"plan_write"})
    registry = {"plan_write": plan_write_mod.make_spec(tmp_path)}
    mock = MockClient(script=[
        assistant_response("", tool_calls=[tool_call("plan_write", markdown="# Plan\n## Context\nx\n")]),
        assistant_response("", tool_calls=[tool_call("todo_write", goal="g", items=[])]),  # must NEVER run
    ])
    result = run_main_loop(
        conv_folder=tmp_path, agent=agent, tools_registry=registry, llm_client=mock,
        user_text="estimate grains", plan_mode=True,
    )
    assert "Plan prêt" in result            # the halt message
    assert len(mock.calls_v2) == 1          # stopped right after plan_write — no second turn
    assert load_plan(tmp_path) is not None  # the plan was authored
    assert load_todo(tmp_path) is None      # NO execution / todo built in the plan turn


def test_plan_mode_refinement_does_not_halt_before_rewrite(tmp_path: Path):
    """On a REFINEMENT turn plan.md already exists — the turn must NOT halt until plan_write is
    called AGAIN (the model may explore first). Halt fires only on the fresh plan_write."""
    from jeanmichel import todo as todomod
    from jeanmichel.tools import plan_write as plan_write_mod

    todomod.save_plan(tmp_path, "# Old plan\n")  # a plan already exists (from a prior turn)
    agent = make_agent("jean-michel", role="router", tool_grants={"plan_write", "echo"})
    registry = {"plan_write": plan_write_mod.make_spec(tmp_path), "echo": make_echo_tool()}
    mock = MockClient(script=[
        assistant_response("", tool_calls=[tool_call("echo", text="exploring")]),  # no plan_write → no halt
        assistant_response("", tool_calls=[tool_call("plan_write", markdown="# Revised plan\n")]),  # → halt
    ])
    result = run_main_loop(
        conv_folder=tmp_path, agent=agent, tools_registry=registry, llm_client=mock,
        user_text="add a sensitivity analysis", plan_mode=True,
    )
    assert "Plan prêt" in result
    assert len(mock.calls_v2) == 2  # the exploration turn was NOT cut short
    assert "Revised" in todomod.load_plan(tmp_path)  # the plan was actually revised


def test_edit_conclusion_clears_todo(tmp_path: Path):
    """Delivering the final answer completes the plan → the execution todo is cleared, even if
    the last step (the synthesis itself) was never marked done (conv 01-44 left it pending)."""
    from jeanmichel import todo as todomod
    todomod.save_todo(tmp_path, "g", [
        {"id": "1", "text": "research", "status": "done"},
        {"id": "2", "text": "synthesize the answer", "status": "in_progress"},  # never marked done
    ])
    agent = make_agent("jean-michel", role="router")
    mock = MockClient(script=[assistant_response("Voici la réponse finale.")])
    result = run_main_loop(
        conv_folder=tmp_path, agent=agent, tools_registry={}, llm_client=mock,
        user_text="finish it", plan_mode=False,
    )
    assert result == "Voici la réponse finale."
    assert todomod.load_todo(tmp_path) is None  # cleared on conclusion — no lingering pending item


def test_run_main_loop_reloads_persisted_state(tmp_path: Path):
    """Phase 0c : le référent organisationnel PERSISTE d'un tour à l'autre (rechargé en début
    de tour) tandis que l'éphémère par-tour est remis à zéro. (Avant : state recréé from scratch.)"""
    from jeanmichel import persistence
    persistence.save_state(tmp_path, ConversationState(
        plans={"id1": {"status": "in_progress", "approved": True}},
        active_plan_id="id1", phase="executing",
        search_calls_total=99,  # éphémère obsolète → doit être reset
    ))
    agent = make_agent("jean-michel", role="router")
    mock = MockClient(script=[assistant_response("Done.")])
    run_main_loop(conv_folder=tmp_path, agent=agent, tools_registry={},
                  llm_client=mock, user_text="hi", plan_mode=False)
    after = ConversationState.from_dict(persistence.load_state(tmp_path))
    assert after.plans == {"id1": {"status": "in_progress", "approved": True}}  # persisté
    assert after.active_plan_id == "id1"          # organisationnel persiste
    assert after.phase == "answered"              # phase activement gérée (tour EDIT conclu)
    assert after.search_calls_total == 0  # éphémère reset (pas 99)
    assert after.last_iteration_at_utc  # alimenté (était mort)


def test_run_main_loop_inscribes_request_log_and_phase(tmp_path: Path):
    """Phase 1 : chaque tour ouvre/ferme une entrée dans requests[] (id, mode, outcome) et pose
    la phase. Le log s'accumule + persiste (le référent)."""
    from jeanmichel import persistence
    agent = make_agent("jean-michel", role="router")
    run_main_loop(conv_folder=tmp_path, agent=agent, tools_registry={},
                  llm_client=MockClient(script=[assistant_response("Voilà.")]),
                  user_text="salut", plan_mode=False)
    st = ConversationState.from_dict(persistence.load_state(tmp_path))
    assert st.phase == "answered" and len(st.requests) == 1
    r = st.requests[0]
    assert r["mode"] == "edit" and r["outcome"] == "answered" and r["ended"] and r["id"]
    # 2e tour → le log s'accumule (requests persiste d'un tour à l'autre).
    run_main_loop(conv_folder=tmp_path, agent=make_agent("jean-michel", role="router"),
                  tools_registry={}, llm_client=MockClient(script=[assistant_response("Encore.")]),
                  user_text="rebelote", plan_mode=False)
    st2 = ConversationState.from_dict(persistence.load_state(tmp_path))
    assert len(st2.requests) == 2 and st2.requests[1]["mode"] == "edit"


def test_sync_plan_todo_referent_inscribes_and_clears(tmp_path: Path):
    """Phase 1.3a : _sync_plan_todo_referent reflète plan.md/todo.json dans le state (progression
    inscrite) et lâche le tracker quand le todo est vidé."""
    from jeanmichel import todo as todomod
    from jeanmichel.orchestrator_v2 import _sync_plan_todo_referent
    todomod.save_plan(tmp_path, "# Plan\n## Context\nx")
    todomod.save_todo(tmp_path, "g", [
        {"id": "1", "text": "a", "status": "done"},
        {"id": "2", "text": "b", "status": "in_progress"},
    ])
    s = ConversationState()
    _sync_plan_todo_referent(tmp_path, s)
    assert s.active_plan_id == "p1" and s.plans["p1"]["plan_file"] == "plan.md"
    assert s.active_todo_id == "t1"
    assert s.todos["t1"] == {"plan_id": "p1", "owner": "orchestrator", "file": "todo.json",
                             "done": 1, "total": 2, "current_step": "2"}
    todomod.clear_todo(tmp_path)  # all-done / conclusion → todo.json supprimé
    _sync_plan_todo_referent(tmp_path, s)
    assert s.active_todo_id is None and "t1" not in s.todos


def test_run_main_loop_inscribes_todo_into_referent(tmp_path: Path):
    """Phase 1.3a (câblage) : un todo_write dans la boucle peuple state.todos (additif)."""
    from jeanmichel import persistence
    from jeanmichel.tools import todo_write as todo_write_mod
    agent = make_agent("jean-michel", role="router", tool_grants={"todo_write"})
    registry = {"todo_write": todo_write_mod.make_spec(tmp_path)}
    mock = MockClient(script=[
        assistant_response("", tool_calls=[tool_call("todo_write", goal="g", items=[
            {"id": "1", "text": "a", "status": "done"},
            {"id": "2", "text": "b", "status": "in_progress"},
            {"id": "3", "text": "c", "status": "pending"},
        ])]),
        assistant_response("Fini."),
    ])
    run_main_loop(conv_folder=tmp_path, agent=agent, tools_registry=registry,
                  llm_client=mock, user_text="go", plan_mode=False)
    st = ConversationState.from_dict(persistence.load_state(tmp_path))
    assert st.active_todo_id == "t1"
    assert st.todos["t1"]["done"] == 1 and st.todos["t1"]["total"] == 3
    assert st.todos["t1"]["current_step"] == "2" and st.todos["t1"]["plan_id"] is None


def test_reconcile_plan_approval_on_referent():
    """Phase 1.3b : l'acceptation vit dans state.plans[id].approved (plus de sidecar)."""
    from jeanmichel.orchestrator_v2 import _reconcile_plan_approval
    s = ConversationState(active_plan_id="p1", plans={"p1": {"approved": False}})
    _reconcile_plan_approval(s, plan_mode=False, at_start=True)   # EDIT start sur non-approuvé
    assert s.plans["p1"]["approved"] is True                     # → accepté
    _reconcile_plan_approval(s, plan_mode=True, at_start=False)   # fin de tour PLAN (re-plan)
    assert s.plans["p1"]["approved"] is False                    # → ré-attente d'approbation
    s2 = ConversationState()                                     # pas de plan actif → no-op
    _reconcile_plan_approval(s2, plan_mode=False, at_start=True)
    assert s2.active_plan_id is None


def test_plan_then_edit_acceptance_via_referent(tmp_path: Path):
    """Phase 1.3b (bout en bout) : tour PLAN → plans[p1].approved=False ; tour EDIT → approved=True.
    Migration de l'acceptation du sidecar vers le référent."""
    from jeanmichel import persistence
    from jeanmichel.tools import plan_write as plan_write_mod
    registry = {"plan_write": plan_write_mod.make_spec(tmp_path)}
    run_main_loop(conv_folder=tmp_path,
                  agent=make_agent("jean-michel", role="router", tool_grants={"plan_write"}),
                  tools_registry=registry, user_text="planifie", plan_mode=True,
                  llm_client=MockClient(script=[assistant_response(
                      "", tool_calls=[tool_call("plan_write", markdown="# Plan\n## Context\nx")])]))
    st1 = ConversationState.from_dict(persistence.load_state(tmp_path))
    assert st1.active_plan_id == "p1" and st1.plans["p1"]["approved"] is False  # proposé
    run_main_loop(conv_folder=tmp_path, agent=make_agent("jean-michel", role="router"),
                  tools_registry={}, user_text="Approved — execute", plan_mode=False,
                  llm_client=MockClient(script=[assistant_response("Exécuté.")]))
    st2 = ConversationState.from_dict(persistence.load_state(tmp_path))
    assert st2.plans["p1"]["approved"] is True  # accepté via le référent (pas de sidecar)


def test_add_file_dedup_by_path(tmp_path: Path):
    from jeanmichel.orchestrator_v2 import _add_file
    s = ConversationState()
    _add_file(s, "x.py", layer="workspace", produced_by="r1")
    _add_file(s, "x.py", layer="worktree", produced_by="r2")  # même path → maj, pas de doublon
    assert len(s.files) == 1 and s.files[0]["layer"] == "worktree" and s.files[0]["produced_by"] == "r2"
    _add_file(s, "", layer="workspace", produced_by="r1")  # path vide → no-op
    assert len(s.files) == 1


def test_inscribe_subagent_and_its_files(tmp_path: Path):
    from jeanmichel.orchestrator_v2 import SubResult, _inscribe_subagent
    s = ConversationState(active_plan_id="p1", plans={"p1": {}}, requests=[{"id": "req_top"}])
    sr = SubResult(agent="code-runner", summary="done", confidence="high",
                   files_produced=["a.py", "b.md"], request_id="sub_1")
    _inscribe_subagent(s, tmp_path, "code-runner", sr)
    assert s.subagents == [{
        "request_id": "sub_1", "agent": "code-runner", "parent_request": "req_top",
        "plan_id": "p1", "confidence": "high", "files_produced": ["a.py", "b.md"],
    }]
    assert [f["path"] for f in s.files] == ["a.py", "b.md"]
    assert all(f["produced_by"] == "sub_1" and f["plan_id"] == "p1" and f["layer"] == "workspace"
               for f in s.files)  # pas de worktree → workspace


def test_run_main_loop_inscribes_workspace_file(tmp_path: Path):
    """Phase 1.4 (câblage) : une écriture workspace du main agent peuple state.files."""
    from jeanmichel import persistence
    from jeanmichel.tools import workspace_create_file as wcf
    agent = make_agent("jean-michel", role="router", tool_grants={"workspace_create_file"})
    registry = {"workspace_create_file": wcf.make_spec(tmp_path, has_write_grant=True)}
    mock = MockClient(script=[
        assistant_response("", tool_calls=[tool_call("workspace_create_file", path="out.md", content="hi")]),
        assistant_response("Fini."),
    ])
    run_main_loop(conv_folder=tmp_path, agent=agent, tools_registry=registry,
                  llm_client=mock, user_text="go", plan_mode=False)
    st = ConversationState.from_dict(persistence.load_state(tmp_path))
    entry = next((f for f in st.files if f["path"] == "out.md"), None)
    assert entry is not None and entry["layer"] == "workspace" and entry["produced_by"]


def test_no_plan_gate_outside_plan_mode(tmp_path: Path):
    """Without plan_mode, the router concludes in prose immediately (no todo gate)."""
    agent = make_agent("jean-michel", role="router")
    mock = MockClient(script=[assistant_response("Direct answer.")])
    result = run_main_loop(
        conv_folder=tmp_path, agent=agent, tools_registry={},
        llm_client=mock, user_text="hi", plan_mode=False,
    )
    assert result == "Direct answer."
    assert len(mock.calls_v2) == 1  # concluded on the first turn


def test_plan_mode_propagates_to_subagent(tmp_path: Path):
    """plan_mode reaches a delegated specialist (fresh sub_state) : its mutating
    tools are denied just like the router's."""
    sub_agent = make_agent("specialist", role="specialist", tool_grants={"repo_edit"})
    parent_state = ConversationState(depth_current=0, plan_mode=True)
    mock = MockClient(script=[
        assistant_response("", tool_calls=[tool_call("repo_edit", path="x.py")]),
        assistant_response("", tool_calls=[tool_call(
            "report_back", summary="blocked by plan mode", files_produced=[],
            confidence="low", low_confidence_reason="cannot edit during plan",
        )]),
    ])
    result = spawn_subagent(
        conv_folder=tmp_path,
        sub_agent=sub_agent,
        tools_registry={},
        llm_client=mock,
        briefing="edit x",
        support_files=[],
        expected="",
        parent_state=parent_state,
        parent_agent_code="jean-michel",
    )
    assert isinstance(result, SubResult)
    # The subagent's repo_edit was denied with a PLAN-mode reason (2nd LLM call sees it).
    second = mock.calls_v2[1]["messages"]
    denied = [m for m in second if m.get("role") == "tool" and "PLAN mode" in (m.get("content") or "")]
    assert denied, "subagent mutating tool should be denied in plan mode"


# Section 9 : subagent persistence isolation (bug D)


def test_subagent_does_not_clobber_main_conv_files(tmp_path: Path):
    """A subagent must persist ONLY its own subagent_<id>.json — never the main
    messages.json / state.json (those belong to the main agent)."""
    sub_agent = make_agent("specialist", role="specialist")
    parent_state = ConversationState(depth_current=0)
    mock = MockClient(script=[
        assistant_response("", tool_calls=[tool_call(
            "report_back", summary="done", files_produced=[], confidence="high",
        )]),
    ])
    spawn_subagent(
        conv_folder=tmp_path, sub_agent=sub_agent, tools_registry={}, llm_client=mock,
        briefing="x", support_files=[], expected="", parent_state=parent_state,
        parent_agent_code="jean-michel",
    )
    # The main files are untouched by the subagent ...
    assert not (tmp_path / "messages.json").exists()
    assert not (tmp_path / "state.json").exists()
    # ... but the subagent audit file IS written.
    assert list(tmp_path.glob("subagent_*.json"))


def test_subagent_prose_report_back_captured_not_looped(tmp_path: Path):
    """A subagent that narrates its conclusion as PROSE (no tool_call) is captured as
    an implicit report_back on the FIRST turn — neither looped to max_iterations (bug
    B, conv dfcafc75) nor discarded (Fix D, conv b2701c32)."""
    sub_agent = make_agent("code-analyst", role="specialist")
    parent_state = ConversationState(depth_current=0)
    # Always prose, never a real tool_call : would loop 50× before the fix.
    prose = assistant_response('report_back(summary="introuvable", confidence="low")')
    mock = MockClient(script=[prose] * 50)
    result = spawn_subagent(
        conv_folder=tmp_path, sub_agent=sub_agent, tools_registry={}, llm_client=mock,
        briefing="list deps from v1_analysis.md", support_files=[], expected="",
        parent_state=parent_state, parent_agent_code="code-router",
    )
    assert result.confidence == "medium"
    assert "introuvable" in result.summary
    # Captured immediately : ONE call, not a 50× loop and not an empty abort.
    assert len(mock.calls_v2) == 1


# Section 10 : support_file handoff validation (bug C)


def test_delegate_with_missing_support_file_is_denied(tmp_path: Path):
    """A delegation referencing a support_file that exists in NEITHER the workspace
    nor the repo is denied (no spawn) with a teaching message — bug C, conv dfcafc75."""
    agent = make_agent("code-router", role="router", delegation_targets={"code-analyst"})

    spawned = {"n": 0}

    def resolver(code):
        spawned["n"] += 1  # would be hit by spawn_subagent's agent_resolver
        return make_agent(code, role="specialist")

    mock = MockClient(script=[
        assistant_response("", tool_calls=[tool_call(
            "delegate_to", agent_code="code-analyst", briefing="list deps",
            support_files=["v1_analysis.md"],  # phantom
        )]),
        assistant_response("ok, I will not invent files."),
    ])
    run_main_loop(
        conv_folder=tmp_path, agent=agent, tools_registry={}, llm_client=mock,
        user_text="x", agent_resolver=resolver,
    )
    # The delegation was rejected before spawning : the router saw a tool error.
    msgs = mock.calls_v2[-1]["messages"]
    errs = [
        m for m in msgs
        if m.get("role") == "tool" and "missing_support_file" in (m.get("content") or "")
    ]
    assert errs, "phantom support_file should be denied"
    assert "v1_analysis.md" in errs[0]["content"]


# =============================================================================
# Section 11 : resumable subagent — the ask_human round-trip (P5)
# =============================================================================


def test_resume_subagent_reuses_trace_not_fresh(tmp_path: Path):
    """resume_subagent reloads the subagent's OWN saved trace, appends the human
    answer, and concludes — reusing the SAME request_id file (no fresh context)."""
    from jeanmichel.orchestrator_v2 import resume_subagent

    sub_agent = make_agent("specialist", role="specialist")
    parent_state = ConversationState(depth_current=0)

    # 1) Fresh spawn that blocks on a human question.
    blocking = MockClient(script=[
        assistant_response("", tool_calls=[tool_call(
            "report_back", summary="need a decision", files_produced=[],
            confidence="low", low_confidence_reason="HUMAN INPUT NEEDED: which port?",
        )]),
    ])
    blocked = spawn_subagent(
        conv_folder=tmp_path, sub_agent=sub_agent, tools_registry={}, llm_client=blocking,
        briefing="set up the server", support_files=[], expected="",
        parent_state=parent_state,
    )
    assert blocked.confidence == "low" and blocked.request_id
    rid = blocked.request_id
    traces_before = sorted(tmp_path.glob("subagent_*.json"))
    assert len(traces_before) == 1

    # 2) Resume the same trace with the human's answer.
    resuming = MockClient(script=[
        assistant_response("", tool_calls=[tool_call(
            "report_back", summary="Bound the server to port 8080.", files_produced=[],
            confidence="high",
        )]),
    ])
    resumed = resume_subagent(
        conv_folder=tmp_path, sub_agent=sub_agent, request_id=rid, human_answer="8080",
        tools_registry={}, llm_client=resuming, parent_state=parent_state,
    )
    assert resumed.confidence == "high"
    assert resumed.request_id == rid  # same trace id, not a new spawn

    # Same single file, now containing the human answer between the two report_backs.
    traces_after = sorted(tmp_path.glob("subagent_*.json"))
    assert traces_after == traces_before  # reused, not multiplied
    trace = json.loads(traces_after[0].read_text(encoding="utf-8"))
    assert any(
        m.get("role") == "user" and "[HUMAN ANSWER" in (m.get("content") or "") and "8080" in m["content"]
        for m in trace
    )
    # The original brief is still there — the agent resumed where it left off.
    assert any("set up the server" in (m.get("content") or "") for m in trace)


def test_resume_subagent_falls_back_to_fresh_when_trace_missing(tmp_path: Path):
    """If the saved trace is gone, resume folds the answer into a fresh brief."""
    from jeanmichel.orchestrator_v2 import resume_subagent

    sub_agent = make_agent("specialist", role="specialist")
    mock = MockClient(script=[
        assistant_response("", tool_calls=[tool_call(
            "report_back", summary="done with 8080", files_produced=[], confidence="high",
        )]),
    ])
    out = resume_subagent(
        conv_folder=tmp_path, sub_agent=sub_agent, request_id="deadbeef", human_answer="8080",
        tools_registry={}, llm_client=mock, parent_state=ConversationState(depth_current=0),
    )
    assert out.confidence == "high"
    # A fresh trace was written (different id), and it carries the answer in the brief.
    traces = list(tmp_path.glob("subagent_*.json"))
    assert len(traces) == 1
    assert "8080" in traces[0].read_text(encoding="utf-8")


def test_blocked_subagent_round_trip_resumes_via_router(tmp_path: Path):
    """End-to-end : specialist blocks (low + HUMAN INPUT NEEDED) -> router ask_human
    -> re-delegate to the SAME agent -> resume on its own trace -> conclude. The
    router's messages.json and the subagent_*.json stay SEPARATE conversations."""
    main_agent = make_agent("code-router", role="router", delegation_targets={"specialist"})

    def resolver(code):
        return make_agent(code, role="specialist") if code == "specialist" else None

    mock = MockClient(script=[
        # router delegates
        assistant_response("", tool_calls=[tool_call(
            "delegate_to", agent_code="specialist", briefing="set up the dev server",
        )]),
        # subagent (fresh) blocks on a human question
        assistant_response("", tool_calls=[tool_call(
            "report_back", summary="need a decision", files_produced=[],
            confidence="low", low_confidence_reason="HUMAN INPUT NEEDED: which port?",
        )]),
        # router asks the human
        assistant_response("", tool_calls=[tool_call(
            "ask_human", question="Quel port doit utiliser le serveur ?", why="binding",
        )]),
        # router re-delegates to the SAME agent (different brief -> no dedup)
        assistant_response("", tool_calls=[tool_call(
            "delegate_to", agent_code="specialist", briefing="apply the chosen port",
        )]),
        # subagent (resumed) concludes
        assistant_response("", tool_calls=[tool_call(
            "report_back", summary="Bound the server to port 8080.", files_produced=[],
            confidence="high",
        )]),
        # router final answer
        assistant_response("Le serveur écoute sur le port 8080."),
    ])

    result = run_main_loop(
        conv_folder=tmp_path, agent=main_agent, tools_registry={}, llm_client=mock,
        user_text="configure le serveur", agent_resolver=resolver,
        ask_human_callback=lambda q, why, choices, multi: "8080",
    )
    assert result == "Le serveur écoute sur le port 8080."

    # Exactly ONE subagent trace : the resume reused it, it did not spawn a fresh one.
    traces = list(tmp_path.glob("subagent_*.json"))
    assert len(traces) == 1, [p.name for p in traces]
    sub_trace = traces[0].read_text(encoding="utf-8")
    # The subagent conversation carries the internal resume marker + its conclusion.
    assert "[HUMAN ANSWER" in sub_trace and "Bound the server to port 8080" in sub_trace

    # The ROUTER conversation (messages.json) is a DISTINCT file : it holds the human
    # answer as a plain user turn, but NOT the subagent-internal resume marker.
    from jeanmichel.persistence import load_messages
    router_msgs = load_messages(tmp_path)
    router_blob = json.dumps(router_msgs, ensure_ascii=False)
    assert "8080" in router_blob                       # the answer reached the router
    assert "[HUMAN ANSWER" not in router_blob          # subagent internals stay in the sub trace


# =============================================================================
# Section 12 : sub-agent output → workspace file + token-stream channels
# =============================================================================


def test_subagent_output_materialized_to_workspace(tmp_path: Path):
    """A sub-agent that reports a summary but writes no file → the orchestrator
    materializes its deliverable to a workspace file and adds it to files_produced
    (handoff + inspectable), instead of leaving it only in messages."""
    main_agent = make_agent("jean-michel", role="router", delegation_targets={"summarizer"})

    def resolver(code):
        return make_agent(code, role="specialist") if code == "summarizer" else None

    mock = MockClient(script=[
        assistant_response("", tool_calls=[tool_call(
            "delegate_to", agent_code="summarizer", briefing="summarize X")]),
        assistant_response("", tool_calls=[tool_call(
            "report_back", summary="The summary of X is Y.", files_produced=[], confidence="high")]),
        assistant_response("Done."),
    ])
    run_main_loop(conv_folder=tmp_path, agent=main_agent, tools_registry={}, llm_client=mock,
                  user_text="x", agent_resolver=resolver)
    files = list((tmp_path / "workspace").glob("summarizer_*.md"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == "The summary of X is Y."
    msgs = mock.calls_v2[-1]["messages"]
    deleg = [json.loads(m["content"]) for m in msgs
             if m.get("role") == "tool" and m.get("tool_name") == "delegate_to"]
    assert deleg and deleg[-1]["files_produced"] == [files[0].name]


def test_token_stream_channels_thinking_all_content_main_only(tmp_path: Path):
    """Live stream channels : thinking is emitted for EVERY agent (you see whoever is
    reasoning) ; content (the answer) ONLY for the main agent — a sub-agent's content is
    internal (→ workspace file), never streamed to the user."""
    from jeanmichel.events import AgentTokenStreamed

    class _StreamingMock(MockClient):
        def chat_messages(self, **kw):
            ot = kw.get("on_token")
            if ot:
                ot("T", "thinking")
                ot("C", "content")
            return super().chat_messages(**kw)

    main_agent = make_agent("jean-michel", role="router", delegation_targets={"summarizer"})

    def resolver(code):
        return make_agent(code, role="specialist") if code == "summarizer" else None

    mock = _StreamingMock(script=[
        assistant_response("", tool_calls=[tool_call(
            "delegate_to", agent_code="summarizer", briefing="b")]),
        assistant_response("", tool_calls=[tool_call(
            "report_back", summary="s", files_produced=[], confidence="high")]),
        assistant_response("final"),
    ])
    seen: list = []
    run_main_loop(conv_folder=tmp_path, agent=main_agent, tools_registry={}, llm_client=mock,
                  user_text="x", agent_resolver=resolver, event_emitter=seen.append)
    toks = [e for e in seen if isinstance(e, AgentTokenStreamed)]
    thinking_agents = {e.agent for e in toks if e.channel == "thinking"}
    content_agents = {e.agent for e in toks if e.channel == "content"}
    assert {"jean-michel", "summarizer"} <= thinking_agents   # thinking : every agent
    assert content_agents == {"jean-michel"}                  # content : main only


# =============================================================================
# Section 12 : user cancellation (Stop button) — cancel_event aborts the loop
# =============================================================================


def test_run_main_loop_cancelled_before_start_returns_stopped(tmp_path: Path):
    """cancel_event already set → the loop aborts at the first iteration checkpoint,
    BEFORE any LLM call, and returns the friendly stopped message."""
    import threading
    agent = make_agent("jean-michel", role="router")
    ev = threading.Event()
    ev.set()
    mock = MockClient(script=[assistant_response("should never be called")])
    result = run_main_loop(
        conv_folder=tmp_path, agent=agent, tools_registry={},
        llm_client=mock, user_text="hi", cancel_event=ev,
    )
    assert result == "⏹ Tour arrêté."
    assert mock.calls_v2 == []  # aborted before any LLM call


def test_run_main_loop_cancelled_mid_turn_aborts_next_iteration(tmp_path: Path):
    """Stop pressed during the turn : the in-flight call finishes, then the loop aborts
    at the next iteration's checkpoint (no further LLM call)."""
    import threading
    ev = threading.Event()
    agent = make_agent("jean-michel", role="router", tool_grants={"echo"})

    class _CancellingMock(MockClient):
        def chat_messages(self, **kw):
            ev.set()  # simulate the user clicking Stop during the first call
            return super().chat_messages(**kw)

    mock = _CancellingMock(script=[
        assistant_response("", tool_calls=[tool_call("echo", text="hi")]),
        assistant_response("should not reach a second LLM call"),
    ])
    result = run_main_loop(
        conv_folder=tmp_path, agent=agent,
        tools_registry={"echo": make_echo_tool()},
        llm_client=mock, user_text="hi", cancel_event=ev,
    )
    assert result == "⏹ Tour arrêté."
    assert len(mock.calls_v2) == 1  # only the first call ran ; iter 2 aborted at the top
