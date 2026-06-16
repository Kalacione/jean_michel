"""ConversationState — sérialisation round-trip + from_dict tolérant (Phase 0b du référent).

Le state devient le référent organisationnel persistant : asdict→from_dict doit préserver les
champs organisationnels, et from_dict doit tolérer un state.json legacy/partiel (défauts) +
ignorer des clés inconnues (forward-compat) pour pouvoir RELIRE le state en début de tour."""

from __future__ import annotations

import dataclasses

from jeanmichel.models import ConversationState


def test_state_roundtrip_preserves_organizational_fields():
    s = ConversationState(
        phase="executing", active_plan_id="id1", active_todo_id="t1",
        plans={"id1": {"status": "in_progress", "approved": True, "todo_id": "t1"}},
        todos={"t1": {"plan_id": "id1", "owner": "orchestrator", "done": 2, "total": 5}},
        requests=[{"id": "req_1", "mode": "edit", "plan_id": "id1", "outcome": "answered"}],
        subagents=[{"request_id": "sub_1", "agent": "code-runner"}],
        files=[{"path": "a.md", "layer": "workspace"}],
    )
    back = ConversationState.from_dict(dataclasses.asdict(s))
    assert back == s


def test_from_dict_fills_missing_with_defaults():
    # Legacy state.json (no organizational fields) → defaults, no crash.
    s = ConversationState.from_dict({"working_budget": 100, "plan_mode": True})
    assert s.working_budget == 100 and s.plan_mode is True
    assert s.phase == "idle" and s.plans == {} and s.active_plan_id is None
    assert s.subagents == [] and s.files == []


def test_from_dict_ignores_unknown_keys():
    # Forward-compat : an unknown/future field must not break the reload.
    s = ConversationState.from_dict({"plan_mode": False, "some_future_field": 42})
    assert s.plan_mode is False


def test_from_dict_handles_empty_or_none():
    # load_state returns {} when absent → from_dict must give a clean default state.
    assert ConversationState.from_dict({}) == ConversationState()
    assert ConversationState.from_dict(None) == ConversationState()


def test_reset_ephemeral_keeps_organizational_resets_per_turn():
    """Le split : reset_ephemeral garde l'organisationnel, remet à zéro le par-tour."""
    s = ConversationState(
        # organisationnel → DOIT survivre
        phase="executing", active_plan_id="id1", active_todo_id="t1",
        plans={"id1": {"status": "in_progress"}}, todos={"t1": {"done": 2}},
        requests=[{"id": "r1"}],
        # éphémère → DOIT être remis à zéro
        depth_current=3, search_calls_total=9, search_calls_since_last_persist=4,
        stocktake_due=True, active_subagent="code-runner", working_tokens_used=500,
        blocked_subagent_code="x", blocked_subagent_request_id="r", pending_human_answer="yes",
        plan_mode=False,
    )
    s.reset_ephemeral(plan_mode=True)
    # organisationnel préservé
    assert (s.phase, s.active_plan_id, s.active_todo_id) == ("executing", "id1", "t1")
    assert s.plans == {"id1": {"status": "in_progress"}} and s.todos == {"t1": {"done": 2}}
    assert s.requests == [{"id": "r1"}]
    # éphémère remis à zéro
    assert s.depth_current == 0 and s.plan_mode is True and s.working_tokens_used == 0
    assert s.search_calls_total == 0 and s.search_calls_since_last_persist == 0
    assert s.stocktake_due is False and s.active_subagent is None
    assert s.blocked_subagent_code is None and s.blocked_subagent_request_id is None
    assert s.pending_human_answer is None
