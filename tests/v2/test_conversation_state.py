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
        lineage={"parent_conv_id": "c0", "parent_commit": "abc123"},
    )
    back = ConversationState.from_dict(dataclasses.asdict(s))
    assert back == s


def test_from_dict_fills_missing_with_defaults():
    # Legacy state.json (no organizational fields) → defaults, no crash.
    s = ConversationState.from_dict({"working_budget": 100, "plan_mode": True})
    assert s.working_budget == 100 and s.plan_mode is True
    assert s.phase == "idle" and s.plans == {} and s.active_plan_id is None
    assert s.lineage == {"parent_conv_id": None, "parent_commit": None}


def test_from_dict_ignores_unknown_keys():
    # Forward-compat : an unknown/future field must not break the reload.
    s = ConversationState.from_dict({"plan_mode": False, "some_future_field": 42})
    assert s.plan_mode is False


def test_from_dict_handles_empty_or_none():
    # load_state returns {} when absent → from_dict must give a clean default state.
    assert ConversationState.from_dict({}) == ConversationState()
    assert ConversationState.from_dict(None) == ConversationState()
