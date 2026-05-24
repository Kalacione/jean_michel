"""Tests for the plan_update tool (mechanical plan.md management)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jeanmichel.tools.plan_update import make_spec
from jeanmichel.tools.workspace_create_file import make_spec as ws_create_spec


# ---- Helpers ---------------------------------------------------------------

def _spec(tmp_path: Path, write: bool = True):
    return make_spec(tmp_path, has_write_grant=write)


def _handler(tmp_path: Path, write: bool = True):
    return _spec(tmp_path, write).handler


def _init(h, title="Test Plan", steps=None):
    return json.loads(h(action="init", title=title, steps=steps or [
        {"id": "S1", "title": "Gather sources", "agent": "web-search-specialist",
         "deliverable": "gather/sources.md"},
        {"id": "S2", "title": "Critique", "agent": "critical-thinker",
         "deliverable": "critique/analysis.md"},
        {"id": "S3", "title": "Build document", "agent": "document-builder",
         "deliverable": "output/report.md"},
    ]))


# ---- init ------------------------------------------------------------------

class TestInit:
    def test_init_creates_plan(self, tmp_path):
        h = _handler(tmp_path)
        result = _init(h)
        assert result["action"] == "init"
        assert result["steps_created"] == 3
        plan = (tmp_path / "workspace" / "plan.md").read_text(encoding="utf-8")
        assert "# Plan — Test Plan" in plan
        assert "### S1 — Gather sources [⬜ pending]" in plan
        assert "### S2 — Critique [⬜ pending]" in plan
        assert "### S3 — Build document [⬜ pending]" in plan
        assert "## Revision log" in plan

    def test_init_refuses_existing(self, tmp_path):
        h = _handler(tmp_path)
        _init(h)
        result = json.loads(h(action="init", title="Re-init"))
        assert "error" in result
        assert "already exists" in result["error"]

    def test_init_requires_title(self, tmp_path):
        h = _handler(tmp_path)
        result = json.loads(h(action="init"))
        assert "error" in result
        assert "title" in result["error"]

    def test_init_empty_steps_rejected(self, tmp_path):
        """init with no steps is refused (creates an unusable empty plan)."""
        h = _handler(tmp_path)
        result = json.loads(h(action="init", title="Empty", steps=[]))
        assert "error" in result
        assert "at least one" in result["error"].lower()

    def test_init_accepts_new_steps_alias(self, tmp_path):
        """LLMs frequently pass 'new_steps' instead of 'steps' on init — accept it."""
        h = _handler(tmp_path)
        result = json.loads(h(action="init", title="With alias", new_steps=[
            {"id": "S1", "title": "Step one"},
            {"id": "S2", "title": "Step two"},
        ]))
        assert result["action"] == "init"
        assert result["steps_created"] == 2


# ---- mark ------------------------------------------------------------------

class TestMark:
    def test_mark_swaps_status(self, tmp_path):
        h = _handler(tmp_path)
        _init(h)
        result = json.loads(h(action="mark", step_id="S1", status="done"))
        assert result["step_id"] == "S1"
        plan = (tmp_path / "workspace" / "plan.md").read_text(encoding="utf-8")
        assert "[✅ done]" in plan
        assert "S1" in plan

    def test_mark_injects_findings(self, tmp_path):
        h = _handler(tmp_path)
        _init(h)
        h(action="mark", step_id="S1", status="done", findings="Found 5 key sources.")
        plan = (tmp_path / "workspace" / "plan.md").read_text(encoding="utf-8")
        assert "#### Findings (S1)" in plan
        assert "Found 5 key sources." in plan
        assert "[✅ done]" in plan

    def test_mark_replaces_existing_findings(self, tmp_path):
        h = _handler(tmp_path)
        _init(h)
        h(action="mark", step_id="S1", status="in_progress", findings="First pass findings.")
        h(action="mark", step_id="S1", status="done", findings="Updated findings.")
        plan = (tmp_path / "workspace" / "plan.md").read_text(encoding="utf-8")
        assert "Updated findings." in plan
        assert "First pass findings." not in plan

    def test_mark_unknown_step_returns_error(self, tmp_path):
        h = _handler(tmp_path)
        _init(h)
        result = json.loads(h(action="mark", step_id="S99", status="done"))
        assert "error" in result

    def test_mark_without_plan_returns_error(self, tmp_path):
        h = _handler(tmp_path)
        result = json.loads(h(action="mark", step_id="S1", status="done"))
        assert "error" in result


# ---- add_substep -----------------------------------------------------------

class TestAddSubstep:
    def test_add_substep_creates_S1_1(self, tmp_path):
        h = _handler(tmp_path)
        _init(h)
        result = json.loads(h(
            action="add_substep", parent_step_id="S1",
            title="Follow disambiguation link", reason="Wikipedia page was ambiguous",
        ))
        assert result["new_step_id"] == "S1.1"
        plan = (tmp_path / "workspace" / "plan.md").read_text(encoding="utf-8")
        assert "S1.1 — Follow disambiguation link" in plan
        assert "Parent: S1" in plan
        assert "Reason: Wikipedia page was ambiguous" in plan

    def test_add_substep_increments_counter(self, tmp_path):
        h = _handler(tmp_path)
        _init(h)
        h(action="add_substep", parent_step_id="S1", title="Sub A")
        result = json.loads(h(action="add_substep", parent_step_id="S1", title="Sub B"))
        assert result["new_step_id"] == "S1.2"

    def test_add_substep_unknown_parent_returns_error(self, tmp_path):
        h = _handler(tmp_path)
        _init(h)
        result = json.loads(h(action="add_substep", parent_step_id="S99", title="x"))
        assert "error" in result


# ---- reset -----------------------------------------------------------------

class TestReset:
    def test_reset_archives_old_plan(self, tmp_path):
        h = _handler(tmp_path)
        _init(h)
        result = json.loads(h(action="reset", title="New Plan", new_steps=[]))
        assert result["archive"] is not None
        ws = tmp_path / "workspace"
        archives = list(ws.glob("plan.archive.*.md"))
        assert len(archives) == 1

    def test_reset_writes_new_plan(self, tmp_path):
        h = _handler(tmp_path)
        _init(h)
        h(action="reset", title="Refreshed Plan", new_steps=[
            {"id": "T1", "title": "New step", "agent": "web-search-specialist"},
        ])
        plan = (tmp_path / "workspace" / "plan.md").read_text(encoding="utf-8")
        assert "# Plan — Refreshed Plan" in plan
        assert "T1 — New step" in plan

    def test_reset_without_existing_plan_no_archive(self, tmp_path):
        h = _handler(tmp_path)
        result = json.loads(h(action="reset", title="Fresh", new_steps=[]))
        assert result["archive"] is None


# ---- read ------------------------------------------------------------------

class TestRead:
    def test_read_returns_content(self, tmp_path):
        h = _handler(tmp_path)
        _init(h, title="ReadMe")
        result = json.loads(h(action="read"))
        assert result["action"] == "read"
        assert "# Plan — ReadMe" in result["content"]

    def test_read_no_plan_returns_error(self, tmp_path):
        h = _handler(tmp_path)
        result = json.loads(h(action="read"))
        assert "error" in result

    def test_read_allowed_without_write_grant(self, tmp_path):
        h_write = _handler(tmp_path, write=True)
        _init(h_write)
        h_readonly = _handler(tmp_path, write=False)
        result = json.loads(h_readonly(action="read"))
        assert result["action"] == "read"

    def test_mark_denied_without_write_grant(self, tmp_path):
        h_write = _handler(tmp_path, write=True)
        _init(h_write)
        h_readonly = _handler(tmp_path, write=False)
        result = json.loads(h_readonly(action="mark", step_id="S1", status="done"))
        assert "error" in result
        assert "not granted" in result["error"]


# ---- workspace_create_file guard -------------------------------------------

class TestWorkspaceCreateFileGuard:
    def test_refuses_plan_md(self, tmp_path):
        spec = ws_create_spec(tmp_path, has_write_grant=True)
        result = json.loads(spec.handler(relative_path="plan.md", content="# Plan"))
        assert "error" in result
        assert "plan_update" in result["error"]
        assert result.get("action_required") == "plan_update"

    def test_refuses_workspace_prefix_plan_md(self, tmp_path):
        spec = ws_create_spec(tmp_path, has_write_grant=True)
        result = json.loads(spec.handler(relative_path="workspace/plan.md", content="# Plan"))
        assert "error" in result
        assert "plan_update" in result["error"]

    def test_non_plan_files_still_work(self, tmp_path):
        spec = ws_create_spec(tmp_path, has_write_grant=True)
        result = json.loads(spec.handler(relative_path="notes.md", content="# Notes"))
        assert "bytes_written" in result
