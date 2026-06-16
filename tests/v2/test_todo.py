"""Tests for the persistent TODO (S1): `jeanmichel.todo`, the `todo_write`
tool, the `PreLLMCall` recap re-injection, and the `report_back` extension."""

from __future__ import annotations

import json

from jeanmichel import todo as todomod
from jeanmichel.hooks import PreLLMCall
from jeanmichel.models import ConversationState
from jeanmichel.tools import build_registry, todo_update, todo_write
from jeanmichel.tools.report_back import validate_report_back_args

# ---- Helpers --------------------------------------------------------------


def _state() -> ConversationState:
    return ConversationState(
        system_reserve_tokens=10,
        output_reserve_tokens=2_000,
        working_budget=10_000,
        depth_current=0,
    )


def _items(*statuses: str) -> list[dict]:
    return [{"text": f"step {i + 1}", "status": s} for i, s in enumerate(statuses)]


def _msgs() -> list[dict]:
    return [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


# ---- todo_write tool ------------------------------------------------------


def test_todo_write_in_registry(tmp_path):
    assert "todo_write" in build_registry(tmp_path)


def test_todo_write_persists_and_loads(tmp_path):
    spec = todo_write.make_spec(tmp_path)
    res = json.loads(spec.handler(goal="ship feature", items=_items("in_progress", "pending")))
    assert "error" not in res
    todo = todomod.load_todo(tmp_path)
    assert todo is not None
    assert todo["goal"] == "ship feature"
    assert [it["status"] for it in todo["items"]] == ["in_progress", "pending"]
    assert [it["id"] for it in todo["items"]] == ["1", "2"]  # ids assigned by position


def test_todo_write_whole_list_replace(tmp_path):
    spec = todo_write.make_spec(tmp_path)
    spec.handler(goal="g", items=_items("in_progress", "pending", "pending"))
    spec.handler(goal="g", items=_items("done", "in_progress"))  # replaces wholesale
    todo = todomod.load_todo(tmp_path)
    assert len(todo["items"]) == 2
    assert [it["status"] for it in todo["items"]] == ["done", "in_progress"]


def test_todo_write_refuses_two_in_progress(tmp_path):
    spec = todo_write.make_spec(tmp_path)
    res = json.loads(spec.handler(goal="g", items=_items("in_progress", "in_progress")))
    assert res.get("error_code") == "invalid_items"
    assert todomod.load_todo(tmp_path) is None  # nothing written on rejection


def test_todo_write_rejects_empty_goal_or_items(tmp_path):
    spec = todo_write.make_spec(tmp_path)
    assert json.loads(spec.handler(goal="", items=_items("pending")))["error_code"] == "invalid_goal"
    assert json.loads(spec.handler(goal="g", items=[]))["error_code"] == "invalid_items"


def test_todo_write_rejects_bad_status(tmp_path):
    spec = todo_write.make_spec(tmp_path)
    res = json.loads(spec.handler(goal="g", items=[{"text": "x", "status": "doing"}]))
    assert res["error_code"] == "invalid_items"


def test_todo_write_clears_when_all_done(tmp_path):
    spec = todo_write.make_spec(tmp_path)
    spec.handler(goal="g", items=_items("in_progress"))
    res = json.loads(spec.handler(goal="g", items=_items("done", "done")))
    assert res.get("all_done") is True
    assert todomod.load_todo(tmp_path) is None  # cleared → no stale recap


def test_todo_write_tolerates_extra_keys(tmp_path):
    spec = todo_write.make_spec(tmp_path)
    res = json.loads(spec.handler(goal="g", items=_items("in_progress"), notes="ignored"))
    assert "error" not in res


# ---- todo_update tool (granular status flip) ------------------------------


def test_todo_update_in_registry(tmp_path):
    assert "todo_update" in build_registry(tmp_path)


def test_todo_update_marks_done(tmp_path):
    todo_write.make_spec(tmp_path).handler(goal="g", items=_items("in_progress", "pending"))
    res = json.loads(todo_update.make_spec(tmp_path).handler(item_id="1", status="done"))
    assert "error" not in res
    todo = todomod.load_todo(tmp_path)
    assert [it["status"] for it in todo["items"]] == ["done", "pending"]


def test_todo_update_clears_when_last_done(tmp_path):
    todo_write.make_spec(tmp_path).handler(goal="g", items=_items("done", "in_progress"))
    res = json.loads(todo_update.make_spec(tmp_path).handler(item_id="2", status="done"))
    assert res.get("all_done") is True
    assert todomod.load_todo(tmp_path) is None


def test_todo_update_rejects_second_in_progress(tmp_path):
    todo_write.make_spec(tmp_path).handler(goal="g", items=_items("in_progress", "pending"))
    res = json.loads(todo_update.make_spec(tmp_path).handler(item_id="2", status="in_progress"))
    assert res["error_code"] == "todo_update_rejected"
    assert "already in_progress" in res["summary"]
    # unchanged on rejection
    assert [it["status"] for it in todomod.load_todo(tmp_path)["items"]] == ["in_progress", "pending"]


def test_todo_update_unknown_item(tmp_path):
    todo_write.make_spec(tmp_path).handler(goal="g", items=_items("pending"))
    res = json.loads(todo_update.make_spec(tmp_path).handler(item_id="99", status="done"))
    assert res["error_code"] == "todo_update_rejected" and "unknown item" in res["summary"]


def test_todo_update_no_plan(tmp_path):
    res = json.loads(todo_update.make_spec(tmp_path).handler(item_id="1", status="done"))
    assert res["error_code"] == "todo_update_rejected" and "no plan" in res["summary"]


# ---- render_recap ---------------------------------------------------------


def test_render_recap_format():
    todo = {
        "goal": "build X",
        "items": [
            {"id": "1", "text": "inspect", "status": "done"},
            {"id": "2", "text": "implement", "status": "in_progress"},
            {"id": "3", "text": "test", "status": "pending"},
        ],
    }
    recap = todomod.render_recap(todo)
    assert recap.startswith(todomod.RECAP_MARKER)
    # Framed as orchestrator control so the model can't mistake it for the user (P3).
    assert "not the human user" in recap
    assert "(1/3 done)" in recap
    assert "[x] 1. inspect" in recap
    assert "[>] 2. implement" in recap
    assert "[ ] 3. test" in recap
    assert "Next action: implement" in recap


def test_render_recap_next_action_falls_back_to_pending():
    todo = {"goal": "g", "items": [
        {"id": "1", "text": "a", "status": "done"},
        {"id": "2", "text": "b", "status": "pending"},
    ]}
    assert "Next action: b" in todomod.render_recap(todo)


# ---- rich plan document + acceptance status (decoupled from the todo) ----


def test_save_plan_load_plan_roundtrip(tmp_path):
    assert todomod.load_plan(tmp_path) is None  # absent → None
    todomod.save_plan(tmp_path, "# Plan\n\n## Context\nReasoning here.\n")
    assert todomod.load_plan(tmp_path).startswith("# Plan")
    # Whitespace-only → None (not a real plan).
    todomod.save_plan(tmp_path, "   \n  ")
    assert todomod.load_plan(tmp_path) is None


def test_todo_carries_no_status(tmp_path):
    # The todo is a pure tracker — acceptance is a plan-level concept, not on the todo.
    todomod.save_todo(tmp_path, "g", [{"id": "1", "text": "do", "status": "in_progress"}])
    assert "status" not in todomod.load_todo(tmp_path)


def test_clear_todo_leaves_plan(tmp_path):
    # Decoupled : clearing the tracker does NOT remove the plan document.
    todomod.save_todo(tmp_path, "g", [{"id": "1", "text": "do", "status": "in_progress"}])
    todomod.save_plan(tmp_path, "# Plan\n")
    todomod.clear_todo(tmp_path)
    assert todomod.load_todo(tmp_path) is None
    assert todomod.load_plan(tmp_path) is not None


def test_clear_plan_removes_doc_leaves_todo(tmp_path):
    todomod.save_todo(tmp_path, "g", [{"id": "1", "text": "do", "status": "in_progress"}])
    todomod.save_plan(tmp_path, "# Plan\n")
    todomod.clear_plan(tmp_path)
    assert todomod.load_plan(tmp_path) is None
    assert todomod.load_todo(tmp_path) is not None  # tracker untouched


def test_todo_write_all_done_clears_only_todo(tmp_path):
    spec = todo_write.make_spec(tmp_path)
    spec.handler(goal="g", items=_items("in_progress"))
    todomod.save_plan(tmp_path, "# Plan\n")
    spec.handler(goal="g", items=_items("done", "done"))  # all done → tracker cleared
    assert todomod.load_todo(tmp_path) is None
    assert todomod.load_plan(tmp_path) is not None  # plan stays


# ---- PreLLMCall recap injection ------------------------------------------


def test_recap_injected_for_main_agent(tmp_path):
    todomod.save_todo(tmp_path, "g", [{"id": "1", "text": "do", "status": "in_progress"}])
    hook = PreLLMCall(llm_client=None, conv_folder=tmp_path, is_main_agent=True)
    msgs = _msgs()
    hook(msgs, _state())
    recaps = [m for m in msgs if m["content"].startswith(todomod.RECAP_MARKER)]
    assert len(recaps) == 1  # recap injected (the EDIT-mode banner is appended after it)


def test_recap_refreshed_not_accumulated(tmp_path):
    todomod.save_todo(tmp_path, "g", [{"id": "1", "text": "do", "status": "in_progress"}])
    hook = PreLLMCall(llm_client=None, conv_folder=tmp_path, is_main_agent=True)
    msgs = _msgs()
    for _ in range(3):
        hook(msgs, _state())
    recaps = [m for m in msgs if m["content"].startswith(todomod.RECAP_MARKER)]
    assert len(recaps) == 1  # refreshed each turn, never accumulates


def test_recap_noop_without_todo(tmp_path):
    hook = PreLLMCall(llm_client=None, conv_folder=tmp_path, is_main_agent=True)
    msgs = _msgs()
    hook(msgs, _state())
    # No TODO-RECAP without a todo.json (the EDIT-mode banner is a separate nudge).
    assert not any(m["content"].startswith(todomod.RECAP_MARKER) for m in msgs)


def test_recap_not_injected_for_subagent(tmp_path):
    todomod.save_todo(tmp_path, "g", [{"id": "1", "text": "do", "status": "in_progress"}])
    hook = PreLLMCall(llm_client=None, conv_folder=tmp_path, is_main_agent=False)
    msgs = _msgs()
    hook(msgs, _state())
    assert not any(m["content"].startswith(todomod.RECAP_MARKER) for m in msgs)


# ---- PreLLMCall rich-plan ([PLAN]) injection -----------------------------


def test_plan_doc_injected_for_main_agent(tmp_path):
    todomod.save_plan(tmp_path, "# Plan\n\n## Context\nWhy this approach.\n")
    hook = PreLLMCall(llm_client=None, conv_folder=tmp_path, is_main_agent=True)
    msgs = _msgs()
    hook(msgs, _state())
    plans = [m for m in msgs if m["content"].startswith("[PLAN]")]
    assert len(plans) == 1 and "## Context" in plans[0]["content"]


def test_plan_doc_refreshed_not_accumulated(tmp_path):
    todomod.save_plan(tmp_path, "# Plan\n")
    hook = PreLLMCall(llm_client=None, conv_folder=tmp_path, is_main_agent=True)
    msgs = _msgs()
    for _ in range(3):
        hook(msgs, _state())
    assert sum(1 for m in msgs if m["content"].startswith("[PLAN]")) == 1


def test_plan_doc_noop_without_plan(tmp_path):
    hook = PreLLMCall(llm_client=None, conv_folder=tmp_path, is_main_agent=True)
    msgs = _msgs()
    hook(msgs, _state())
    assert not any(m["content"].startswith("[PLAN]") for m in msgs)


def test_plan_doc_not_injected_for_subagent(tmp_path):
    todomod.save_plan(tmp_path, "# Plan\n")
    hook = PreLLMCall(llm_client=None, conv_folder=tmp_path, is_main_agent=False)
    msgs = _msgs()
    hook(msgs, _state())
    assert not any(m["content"].startswith("[PLAN]") for m in msgs)


# ---- report_back extension (D11) -----------------------------------------


def test_report_back_accepts_suggested_todo_updates():
    err = validate_report_back_args({
        "summary": "done",
        "confidence": "high",
        "suggested_todo_updates": ["add a test step", "mount the router first"],
    })
    assert err is None


def test_report_back_omitting_suggestions_is_fine():
    assert validate_report_back_args({"summary": "s", "confidence": "high"}) is None


def test_report_back_rejects_bad_suggestions():
    assert validate_report_back_args(
        {"summary": "s", "confidence": "high", "suggested_todo_updates": "nope"}
    ) is not None
    assert validate_report_back_args(
        {"summary": "s", "confidence": "high", "suggested_todo_updates": [1, 2]}
    ) is not None
