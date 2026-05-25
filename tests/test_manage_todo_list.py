"""Tests for manage_todo_list tool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jeanmichel.tools.manage_todo_list import _todo_path, make_spec


# ── Helpers ────────────────────────────────────────────────────────────────


def _call(spec, **kwargs) -> dict:
    raw = spec.handler(**kwargs)
    return json.loads(raw)


def _router_spec(tmp_path: Path):
    return make_spec(tmp_path, agent_role="router", request_id_provider=None)


def _specialist_spec(tmp_path: Path, request_id: str = "abc123"):
    provider = lambda: request_id  # noqa: E731
    return make_spec(tmp_path, agent_role="specialist", request_id_provider=provider)


# ── _todo_path ─────────────────────────────────────────────────────────────


def test_todo_path_router(tmp_path):
    path = _todo_path(tmp_path, "router", None)
    assert path == tmp_path / "todo.json"


def test_todo_path_specialist(tmp_path):
    path = _todo_path(tmp_path, "specialist", lambda: "req999")
    assert path == tmp_path / "todo_req999.json"


def test_todo_path_invalid_role(tmp_path):
    with pytest.raises(RuntimeError, match="unexpected role"):
        _todo_path(tmp_path, "finalizer", None)


def test_todo_path_specialist_no_provider(tmp_path):
    with pytest.raises(RuntimeError, match="request_id_provider required"):
        _todo_path(tmp_path, "specialist", None)


# ── write ──────────────────────────────────────────────────────────────────


def test_write_valid(tmp_path):
    spec = _router_spec(tmp_path)
    todos = [
        {"id": "T1", "title": "First task", "status": "pending"},
        {"id": "T2", "title": "Second task", "status": "in_progress"},
    ]
    result = _call(spec, operation="write", todos=todos)
    assert "summary" in result
    assert result["stats"]["total"] == 2
    assert result["stats"]["in_progress"] == 1
    assert (tmp_path / "todo.json").exists()


def test_write_creates_correct_file_for_specialist(tmp_path):
    spec = _specialist_spec(tmp_path, "reqXYZ")
    todos = [{"id": "S1", "title": "Sub-task", "status": "pending"}]
    _call(spec, operation="write", todos=todos)
    assert (tmp_path / "todo_reqXYZ.json").exists()
    assert not (tmp_path / "todo.json").exists()


def test_write_persists_json(tmp_path):
    spec = _router_spec(tmp_path)
    todos = [{"id": "T1", "title": "Task", "status": "pending"}]
    _call(spec, operation="write", todos=todos)
    data = json.loads((tmp_path / "todo.json").read_text())
    assert "updated_at" in data
    assert data["todos"][0]["id"] == "T1"


def test_write_replaces_existing(tmp_path):
    spec = _router_spec(tmp_path)
    _call(spec, operation="write", todos=[{"id": "T1", "title": "Old", "status": "pending"}])
    _call(spec, operation="write", todos=[{"id": "T2", "title": "New", "status": "completed"}])
    data = json.loads((tmp_path / "todo.json").read_text())
    assert len(data["todos"]) == 1
    assert data["todos"][0]["id"] == "T2"


# ── write — validation errors ──────────────────────────────────────────────


def test_write_missing_id(tmp_path):
    spec = _router_spec(tmp_path)
    result = _call(spec, operation="write", todos=[{"title": "No id", "status": "pending"}])
    assert "error_code" in result


def test_write_invalid_status(tmp_path):
    spec = _router_spec(tmp_path)
    result = _call(spec, operation="write", todos=[{"id": "T1", "title": "X", "status": "oops"}])
    assert "error_code" in result


def test_write_duplicate_ids(tmp_path):
    spec = _router_spec(tmp_path)
    todos = [
        {"id": "T1", "title": "A", "status": "pending"},
        {"id": "T1", "title": "B", "status": "pending"},
    ]
    result = _call(spec, operation="write", todos=todos)
    assert "error_code" in result


def test_write_too_many_items(tmp_path):
    spec = _router_spec(tmp_path)
    todos = [{"id": f"T{i}", "title": f"Task {i}", "status": "pending"} for i in range(21)]
    result = _call(spec, operation="write", todos=todos)
    assert result["error_code"] == "too_many_todos"


def test_write_cycle_in_depends_on(tmp_path):
    spec = _router_spec(tmp_path)
    todos = [
        {"id": "T1", "title": "A", "status": "pending", "depends_on": ["T2"]},
        {"id": "T2", "title": "B", "status": "pending", "depends_on": ["T1"]},
    ]
    result = _call(spec, operation="write", todos=todos)
    assert result["error_code"] == "invalid_dependency_graph"


def test_write_self_cycle(tmp_path):
    spec = _router_spec(tmp_path)
    todos = [{"id": "T1", "title": "A", "status": "pending", "depends_on": ["T1"]}]
    result = _call(spec, operation="write", todos=todos)
    assert result["error_code"] == "invalid_dependency_graph"


def test_write_valid_depends_on(tmp_path):
    spec = _router_spec(tmp_path)
    todos = [
        {"id": "T1", "title": "A", "status": "pending"},
        {"id": "T2", "title": "B", "status": "pending", "depends_on": ["T1"]},
    ]
    result = _call(spec, operation="write", todos=todos)
    assert "error_code" not in result


def test_write_missing_todos_arg(tmp_path):
    spec = _router_spec(tmp_path)
    result = _call(spec, operation="write")
    assert "error_code" in result


# ── read ───────────────────────────────────────────────────────────────────


def test_read_empty_when_no_file(tmp_path):
    spec = _router_spec(tmp_path)
    result = _call(spec, operation="read")
    assert result["todos"] == []
    assert result["stats"]["total"] == 0


def test_read_after_write(tmp_path):
    spec = _router_spec(tmp_path)
    todos = [{"id": "T1", "title": "Task", "status": "completed"}]
    _call(spec, operation="write", todos=todos)
    result = _call(spec, operation="read")
    assert len(result["todos"]) == 1
    assert result["todos"][0]["id"] == "T1"
    assert result["stats"]["completed"] == 1


# ── update_status ──────────────────────────────────────────────────────────


def test_update_status_valid(tmp_path):
    spec = _router_spec(tmp_path)
    _call(spec, operation="write", todos=[
        {"id": "T1", "title": "Task", "status": "pending"},
    ])
    result = _call(spec, operation="update_status", id="T1", status="completed")
    assert "error_code" not in result
    assert result["todos"][0]["status"] == "completed"


def test_update_status_with_note(tmp_path):
    spec = _router_spec(tmp_path)
    _call(spec, operation="write", todos=[{"id": "T1", "title": "T", "status": "pending"}])
    result = _call(spec, operation="update_status", id="T1", status="blocked", note="Network issue")
    assert result["todos"][0]["note"] == "Network issue"


def test_update_status_not_found(tmp_path):
    spec = _router_spec(tmp_path)
    _call(spec, operation="write", todos=[{"id": "T1", "title": "Task", "status": "pending"}])
    result = _call(spec, operation="update_status", id="T99", status="completed")
    assert result["error_code"] == "todo_not_found"


def test_update_status_invalid_status(tmp_path):
    spec = _router_spec(tmp_path)
    _call(spec, operation="write", todos=[{"id": "T1", "title": "T", "status": "pending"}])
    result = _call(spec, operation="update_status", id="T1", status="done")
    assert result["error_code"] == "invalid_status"


def test_update_status_missing_id(tmp_path):
    spec = _router_spec(tmp_path)
    result = _call(spec, operation="update_status", status="completed")
    assert result["error_code"] == "missing_argument"


def test_update_status_persists(tmp_path):
    spec = _router_spec(tmp_path)
    _call(spec, operation="write", todos=[{"id": "T1", "title": "Task", "status": "pending"}])
    _call(spec, operation="update_status", id="T1", status="in_progress")
    data = json.loads((tmp_path / "todo.json").read_text())
    assert data["todos"][0]["status"] == "in_progress"


# ── stats ──────────────────────────────────────────────────────────────────


def test_stats_all_statuses(tmp_path):
    spec = _router_spec(tmp_path)
    todos = [
        {"id": "T1", "title": "A", "status": "pending"},
        {"id": "T2", "title": "B", "status": "in_progress"},
        {"id": "T3", "title": "C", "status": "completed"},
        {"id": "T4", "title": "D", "status": "skipped"},
        {"id": "T5", "title": "E", "status": "blocked"},
    ]
    result = _call(spec, operation="write", todos=todos)
    s = result["stats"]
    assert s["total"] == 5
    assert s["pending"] == 1
    assert s["in_progress"] == 1
    assert s["completed"] == 1
    assert s["skipped"] == 1
    assert s["blocked"] == 1


# ── unknown operation ──────────────────────────────────────────────────────


def test_unknown_operation(tmp_path):
    spec = _router_spec(tmp_path)
    result = _call(spec, operation="delete")
    assert result["error_code"] == "unknown_operation"


# ── isolation: router vs specialist ───────────────────────────────────────


def test_router_and_specialist_independent(tmp_path):
    router = _router_spec(tmp_path)
    specialist = _specialist_spec(tmp_path, "req001")

    _call(router, operation="write", todos=[{"id": "T1", "title": "Conv task", "status": "pending"}])
    _call(specialist, operation="write", todos=[{"id": "S1", "title": "Sub task", "status": "in_progress"}])

    router_todos = _call(router, operation="read")["todos"]
    spec_todos = _call(specialist, operation="read")["todos"]

    assert len(router_todos) == 1 and router_todos[0]["id"] == "T1"
    assert len(spec_todos) == 1 and spec_todos[0]["id"] == "S1"


def test_two_specialists_independent(tmp_path):
    spec_a = _specialist_spec(tmp_path, "reqA")
    spec_b = _specialist_spec(tmp_path, "reqB")

    _call(spec_a, operation="write", todos=[{"id": "A1", "title": "A task", "status": "pending"}])
    _call(spec_b, operation="write", todos=[{"id": "B1", "title": "B task", "status": "pending"}])

    assert _call(spec_a, operation="read")["todos"][0]["id"] == "A1"
    assert _call(spec_b, operation="read")["todos"][0]["id"] == "B1"


# ── summary field always present ───────────────────────────────────────────


def test_summary_always_present_on_success(tmp_path):
    spec = _router_spec(tmp_path)
    for result in [
        _call(spec, operation="write", todos=[{"id": "T1", "title": "T", "status": "pending"}]),
        _call(spec, operation="read"),
        _call(spec, operation="update_status", id="T1", status="completed"),
    ]:
        assert "summary" in result


def test_summary_present_on_error(tmp_path):
    spec = _router_spec(tmp_path)
    result = _call(spec, operation="write", todos=[{"id": "", "title": "X", "status": "pending"}])
    assert "summary" in result
