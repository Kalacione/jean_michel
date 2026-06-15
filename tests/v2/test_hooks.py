"""Tests for `jeanmichel.hooks` — 4 orchestrator hooks."""

from __future__ import annotations

import pytest

from jeanmichel.config import MAX_DEPTH, MAX_SEARCH_CALLS_PER_TURN
from jeanmichel.hooks import (
    HookRegistry,
    OnDelegateReturn,
    PostToolUse,
    PreLLMCall,
    PreToolUse,
    ToolCallContext,
    build_hook_registry,
)
from jeanmichel.llm import MockClient
from jeanmichel.models import ConversationState, ToolCall

# ---- Helpers --------------------------------------------------------------


def _state(**overrides) -> ConversationState:
    defaults = dict(
        system_reserve_tokens=10,
        output_reserve_tokens=2_000,
        working_budget=10_000,
        depth_current=0,
    )
    defaults.update(overrides)
    return ConversationState(**defaults)


def _ctx(
    tool_name: str,
    args: dict | None = None,
    agent_code: str = "jean-michel",
    grants: set[str] | None = None,
    targets: set[str] | None = None,
) -> ToolCallContext:
    return ToolCallContext(
        agent_code=agent_code,
        call=ToolCall(name=tool_name, arguments=args or {}),
        agent_grants=frozenset(grants or {tool_name}),
        delegation_targets=frozenset(targets or set()),
    )


# ---- PreLLMCall -----------------------------------------------------------


def test_pre_llm_call_noop_when_budget_unused():
    hook = PreLLMCall(llm_client=None)
    s = _state()
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    level = hook(messages, s)
    assert level == 0
    assert len(messages) == 2


def test_pre_llm_call_returns_compaction_level():
    """The hook returns the level triggered by escalate_compaction."""
    hook = PreLLMCall(llm_client=None)
    s = _state(working_budget=1_000)
    # Fill messages above threshold
    messages = [{"role": "system", "content": "s"}] + [
        {"role": "user", "content": "x" * 800} for _ in range(5)
    ]
    level = hook(messages, s)
    # No LLM → capped at level 2
    assert 0 <= level <= 2


# ---- PreToolUse -----------------------------------------------------------


def test_pre_tool_use_allows_granted_tool():
    hook = PreToolUse()
    s = _state()
    ctx = _ctx("clock", grants={"clock"})
    decision = hook(ctx, s, dedup_cache={})
    assert decision.deny is False
    assert decision.reason is None


def test_pre_tool_use_denies_ungranted_tool():
    hook = PreToolUse()
    s = _state()
    ctx = _ctx("dangerous_tool", grants={"clock"})
    decision = hook(ctx, s, dedup_cache={})
    assert decision.deny is True
    assert "not granted" in (decision.reason or "")


def test_pre_tool_use_denies_delegate_at_max_depth():
    hook = PreToolUse()
    s = _state(depth_current=MAX_DEPTH)  # 5 → 6 would exceed
    ctx = _ctx(
        "delegate_to",
        args={"agent_code": "wikipedia-specialist"},
        grants={"delegate_to"},
    )
    decision = hook(ctx, s, dedup_cache={})
    assert decision.deny is True
    assert "MAX_DEPTH" in (decision.reason or "")


def test_pre_tool_use_allows_delegate_below_max_depth():
    hook = PreToolUse()
    s = _state(depth_current=0)
    ctx = _ctx(
        "delegate_to",
        args={"agent_code": "wikipedia-specialist"},
        grants={"delegate_to"},
    )
    decision = hook(ctx, s, dedup_cache={})
    assert decision.deny is False


def test_pre_tool_use_enforces_delegation_whitelist():
    hook = PreToolUse()
    s = _state()
    # Whitelist allows only wikipedia
    ctx = _ctx(
        "delegate_to",
        args={"agent_code": "code-runner"},
        grants={"delegate_to"},
        targets={"wikipedia-specialist"},
    )
    decision = hook(ctx, s, dedup_cache={})
    assert decision.deny is True
    assert "whitelist" in (decision.reason or "")


def test_pre_tool_use_empty_whitelist_means_no_restriction():
    """Empty targets = legacy v1 behaviour (no whitelist)."""
    hook = PreToolUse()
    s = _state()
    ctx = _ctx(
        "delegate_to",
        args={"agent_code": "any-agent"},
        grants={"delegate_to"},
        targets=set(),  # empty → no whitelist
    )
    decision = hook(ctx, s, dedup_cache={})
    assert decision.deny is False


def test_pre_tool_use_delegate_without_agent_code_is_denied():
    hook = PreToolUse()
    s = _state()
    ctx = _ctx("delegate_to", args={}, grants={"delegate_to"})
    decision = hook(ctx, s, dedup_cache={})
    assert decision.deny is True
    assert "agent_code" in (decision.reason or "")


def test_pre_tool_use_denies_search_when_budget_reached():
    hook = PreToolUse()
    s = _state(search_calls_total=MAX_SEARCH_CALLS_PER_TURN)
    ctx = _ctx("web_search", args={"query": "x"}, grants={"web_search"})
    decision = hook(ctx, s, dedup_cache={})
    assert decision.deny is True
    assert "MAX_SEARCH" in (decision.reason or "")


def test_pre_tool_use_allows_search_under_budget():
    hook = PreToolUse()
    s = _state(search_calls_total=3)
    ctx = _ctx("web_search", args={"query": "x"}, grants={"web_search"})
    decision = hook(ctx, s, dedup_cache={})
    assert decision.deny is False


def test_pre_tool_use_dedup_blocks_repeated_call():
    hook = PreToolUse()
    s = _state()
    ctx = _ctx("web_search", args={"query": "paris"}, grants={"web_search"})

    cache: dict = {}
    # First call → allowed
    d1 = hook(ctx, s, cache)
    assert d1.deny is False
    # Simulate PostToolUse caching the result
    cache.update({
        next(iter([k for k in [
            "web_search(query='paris')"  # the fingerprint we expect
        ]])): {"summary": "5 hits"}
    })
    # Manual cache write to mimic PostToolUse
    cache["web_search(query='paris')"] = {"summary": "5 hits"}

    # Second call with same args → denied
    d2 = hook(ctx, s, cache)
    assert d2.deny is True
    assert "Duplicate" in (d2.reason or "")


def test_pre_tool_use_dedup_delegate_to_escalates():
    """F4 backstop: a verbatim re-delegation is denied with an ESCALATE order
    pointing at ask_human — not the generic 'Duplicate call' message."""
    from jeanmichel.hooks import _fingerprint

    hook = PreToolUse()
    s = _state()
    args = {"agent_code": "web-searcher", "briefing": "find X"}
    ctx = _ctx("delegate_to", args=args, grants={"delegate_to"})
    cache: dict = {}

    assert hook(ctx, s, cache).deny is False
    cache[_fingerprint("delegate_to", args)] = {"summary": "done", "agent": "web-searcher"}

    d2 = hook(ctx, s, cache)
    assert d2.deny is True
    assert "ESCALATE" in (d2.reason or "")
    assert "ask_human" in (d2.reason or "")
    assert "web-searcher" in (d2.reason or "")
    assert "Duplicate call" not in (d2.reason or "")


def test_pre_tool_use_dedup_normalizes_string_args():
    """Args differing only in case/whitespace should hit the cache."""
    hook = PreToolUse()
    s = _state()
    cache: dict = {}

    # First call : lower case
    ctx_low = _ctx("web_search", args={"query": "paris"}, grants={"web_search"})
    hook(ctx_low, s, cache)
    # Manual cache write
    cache["web_search(query='paris')"] = {"summary": "5 hits"}

    # Second call : upper case + extra whitespace
    ctx_upper = _ctx("web_search", args={"query": "  PARIS  "}, grants={"web_search"})
    d2 = hook(ctx_upper, s, cache)
    assert d2.deny is True


# ---- PLAN mode gate -------------------------------------------------------


def test_plan_mode_denies_mutating_tools():
    """In plan_mode, repo/workspace mutators + repo_exec are denied with a PLAN reason."""
    hook = PreToolUse()
    s = _state(plan_mode=True)
    for tool in ("repo_edit", "repo_write", "repo_exec", "workspace_create_file",
                 "workspace_str_replace", "workspace_delete_dir"):
        ctx = _ctx(tool, args={}, grants={tool})
        d = hook(ctx, s, dedup_cache={})
        assert d.deny is True, tool
        assert "PLAN mode" in (d.reason or ""), tool


def test_plan_mode_allows_reads_search_delegate_and_todo():
    """In plan_mode, read/exploration/plan tools stay available."""
    hook = PreToolUse()
    s = _state(plan_mode=True)
    for tool, grants in (
        ("repo_read", {"repo_read"}),
        ("repo_grep", {"repo_grep"}),
        ("repo_git", {"repo_git"}),
        ("repo_test", {"repo_test"}),
        ("workspace_view", {"workspace_view"}),
        ("web_search", {"web_search"}),
        ("todo_write", {"todo_write"}),
        ("delegate_to", {"delegate_to"}),
    ):
        args = {"agent_code": "code-runner", "briefing": "explore"} if tool == "delegate_to" else {}
        ctx = _ctx(tool, args=args, grants=grants)
        assert hook(ctx, s, dedup_cache={}).deny is False, tool


def test_mutating_tools_allowed_outside_plan_mode():
    """Without plan_mode, the mutating tools are not gated by the plan rule."""
    hook = PreToolUse()
    s = _state(plan_mode=False)
    ctx = _ctx("repo_edit", args={}, grants={"repo_edit"})
    assert hook(ctx, s, dedup_cache={}).deny is False


# ---- PostToolUse ----------------------------------------------------------


def test_post_tool_use_increments_search_counter():
    hook = PostToolUse()
    s = _state(search_calls_total=2, search_calls_since_last_persist=1)
    call = ToolCall(name="web_search", arguments={"query": "x"})
    messages: list = []

    hook(call, {"summary": "ok"}, messages, s, dedup_cache={})

    assert s.search_calls_total == 3
    assert s.search_calls_since_last_persist == 2


def test_post_tool_use_workspace_write_resets_persist_counter():
    hook = PostToolUse()
    s = _state(search_calls_since_last_persist=5)
    call = ToolCall(name="workspace_create_file", arguments={"path": "x.md"})

    hook(call, {"summary": "ok"}, [], s, dedup_cache={})

    assert s.search_calls_since_last_persist == 0


def test_post_tool_use_clears_reeval_pending_on_todo_update():
    # The ACT nudge tells the router to mark a step done with todo_update — clearing
    # reeval_pending on todo_update (not only todo_write) stops the nudge re-firing.
    hook = PostToolUse()
    s = _state()
    s.reeval_pending = True
    hook(ToolCall(name="todo_update", arguments={"item_id": "1", "status": "done"}),
         {"summary": "ok"}, [], s, dedup_cache={})
    assert s.reeval_pending is False


def test_post_tool_use_clears_reeval_pending_on_todo_write():
    hook = PostToolUse()
    s = _state()
    s.reeval_pending = True
    hook(ToolCall(name="todo_write", arguments={"goal": "g", "items": []}),
         {"summary": "ok"}, [], s, dedup_cache={})
    assert s.reeval_pending is False


def test_post_tool_use_caches_result_for_dedup():
    hook = PostToolUse()
    s = _state()
    call = ToolCall(name="web_search", arguments={"query": "paris"})
    cache: dict = {}

    hook(call, {"summary": "5 hits"}, [], s, dedup_cache=cache)

    assert "web_search(query='paris')" in cache
    assert cache["web_search(query='paris')"]["summary"] == "5 hits"


def test_post_tool_use_force_persist_nudge_after_n_searches():
    hook = PostToolUse()
    # 3 searches already without persist → this is the 4th
    s = _state(search_calls_total=3, search_calls_since_last_persist=3)
    call = ToolCall(name="web_search", arguments={"query": "x"})
    messages: list = []

    hook(call, {"summary": "ok"}, messages, s, dedup_cache={})

    # Threshold (>3) reached → nudge appended
    assert len(messages) == 1
    nudge = messages[0]
    assert nudge["role"] == "user"
    assert nudge["content"].startswith("[ORCHESTRATOR]")
    assert "persist" in nudge["content"].lower() or "workspace" in nudge["content"].lower()
    # Counter is reset to avoid immediate re-nudge
    assert s.search_calls_since_last_persist == 0


def test_post_tool_use_no_nudge_below_threshold():
    hook = PostToolUse()
    s = _state(search_calls_since_last_persist=1)
    call = ToolCall(name="web_search", arguments={"query": "x"})
    messages: list = []

    hook(call, {"summary": "ok"}, messages, s, dedup_cache={})

    # Threshold not reached yet (2 < 3)
    assert messages == []


# ---- OnDelegateReturn -----------------------------------------------------


def test_on_delegate_return_pushes_role_tool_message():
    hook = OnDelegateReturn()
    s = _state(active_subagent="wikipedia-specialist")
    parent_messages: list = [{"role": "user", "content": "hi"}]
    sub_result = {
        "agent": "wikipedia-specialist",
        "summary": "Found 3 facts",
        "files_produced": ["wikipedia-specialist_paris.md"],
        "confidence": "high",
    }

    hook(parent_messages, sub_result, s)

    assert len(parent_messages) == 2
    last = parent_messages[-1]
    assert last["role"] == "tool"
    assert last["tool_name"] == "delegate_to"
    assert "Found 3 facts" in last["content"]
    assert s.active_subagent is None


def test_on_delegate_return_low_confidence_requires_reason():
    hook = OnDelegateReturn()
    s = _state()
    bad_result = {
        "summary": "x",
        "confidence": "low",
        "low_confidence_reason": "",  # empty → invalid
    }
    with pytest.raises(ValueError, match="low_confidence_reason"):
        hook([], bad_result, s)


def test_on_delegate_return_low_confidence_with_reason_passes():
    hook = OnDelegateReturn()
    s = _state()
    good_result = {
        "summary": "partial",
        "confidence": "low",
        "low_confidence_reason": "Wikipedia returned a disambiguation page.",
    }
    parent_messages: list = []
    hook(parent_messages, good_result, s)
    assert len(parent_messages) == 1


def test_on_delegate_return_high_confidence_no_reason_needed():
    hook = OnDelegateReturn()
    s = _state()
    good_result = {"summary": "all good", "confidence": "high"}
    parent_messages: list = []
    hook(parent_messages, good_result, s)
    assert len(parent_messages) == 1


# ---- HookRegistry --------------------------------------------------------


def test_build_hook_registry_returns_all_four_hooks():
    reg = build_hook_registry(llm_client=None)
    assert isinstance(reg, HookRegistry)
    assert isinstance(reg.pre_llm_call, PreLLMCall)
    assert isinstance(reg.pre_tool_use, PreToolUse)
    assert isinstance(reg.post_tool_use, PostToolUse)
    assert isinstance(reg.on_delegate_return, OnDelegateReturn)


def test_build_hook_registry_propagates_llm_client():
    mock = MockClient(script=[])
    reg = build_hook_registry(llm_client=mock)
    assert reg.pre_llm_call.llm_client is mock
