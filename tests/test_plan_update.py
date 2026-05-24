"""Tests for the plan_update tool (mechanical plan.md management)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jeanmichel.tools.plan_update import make_spec
from jeanmichel.tools.workspace_create_file import make_spec as ws_create_spec


# ---- Helpers ---------------------------------------------------------------

def _spec(tmp_path: Path, write: bool = True, role: str = "router"):
    return make_spec(tmp_path, has_write_grant=write, agent_role=role)


def _handler(tmp_path: Path, write: bool = True, role: str = "router"):
    return _spec(tmp_path, write, role).handler


def _specialist_handler(tmp_path: Path):
    """Handler that behaves like a specialist (read-only on plan)."""
    return make_spec(tmp_path, has_write_grant=True, agent_role="specialist").handler


def _init(h, title="Test Plan", steps=None):
    return json.loads(h(action="init", title=title, steps=steps or [
        {"title": "Gather sources", "agent": "web-search-specialist",
         "deliverable": "gather/sources.md"},
        {"title": "Critique", "agent": "critical-thinker",
         "deliverable": "critique/analysis.md"},
        {"title": "Build document", "agent": "document-builder",
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

    def test_init_returns_step_ids(self, tmp_path):
        h = _handler(tmp_path)
        result = _init(h)
        assert result["step_ids"] == ["S1", "S2", "S3"]

    def test_init_ignores_caller_supplied_ids(self, tmp_path):
        h = _handler(tmp_path)
        result = json.loads(h(action="init", title="T", steps=[
            {"id": "step_1", "title": "First"},
            {"id": "root", "title": "Second"},
        ]))
        assert result["step_ids"] == ["S1", "S2"]
        plan = (tmp_path / "workspace" / "plan.md").read_text(encoding="utf-8")
        assert "step_1" not in plan
        assert "root" not in plan
        assert "S1" in plan
        assert "S2" in plan

    def test_init_idempotent_returns_existing(self, tmp_path):
        h = _handler(tmp_path)
        _init(h)
        # Second init on the same plan: must NOT error — returns existing state.
        result = json.loads(h(action="init", title="Re-init"))
        assert result.get("already_exists") is True
        assert "S1" in result["step_ids"]
        assert "# Plan" in result["content"]

    def test_init_uses_default_title_when_missing(self, tmp_path):
        """LLM omet parfois title — le tool doit créer le plan avec 'Research Plan'."""
        h = _handler(tmp_path)
        result = json.loads(h(action="init", steps=[{"title": "Step one"}]))
        assert result["action"] == "init"
        assert result["steps_created"] == 1
        content = (tmp_path / "workspace" / "plan.md").read_text()
        assert "Research Plan" in content

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
        # Error must list the available ids so the agent can self-correct.
        assert "S1" in result["error"]
        assert "S2" in result["error"]


# ---- reset -----------------------------------------------------------------

class TestReset:
    def test_reset_archives_old_plan(self, tmp_path):
        h = _handler(tmp_path)
        _init(h)
        result = json.loads(h(action="reset", title="New Plan", new_steps=[
            {"title": "Replacement step"},
        ]))
        assert result["archive"] is not None
        ws = tmp_path / "workspace"
        archives = list(ws.glob("plan.archive.*.md"))
        assert len(archives) == 1

    def test_reset_writes_new_plan(self, tmp_path):
        h = _handler(tmp_path)
        _init(h)
        result = json.loads(h(action="reset", title="Refreshed Plan", new_steps=[
            {"title": "New step", "agent": "web-search-specialist"},
        ]))
        plan = (tmp_path / "workspace" / "plan.md").read_text(encoding="utf-8")
        assert "# Plan — Refreshed Plan" in plan
        # ids are auto-assigned starting at S1, not from the caller
        assert "S1 — New step" in plan
        assert result["step_ids"] == ["S1"]

    def test_reset_empty_steps_rejected(self, tmp_path):
        h = _handler(tmp_path)
        _init(h)
        result = json.loads(h(action="reset", title="Fresh", new_steps=[]))
        assert "error" in result
        assert "new_steps" in result["error"]

    def test_reset_without_existing_plan_no_archive(self, tmp_path):
        h = _handler(tmp_path)
        result = json.loads(h(action="reset", title="Fresh", new_steps=[
            {"title": "First step"},
        ]))
        assert result["archive"] is None
        assert result["step_ids"] == ["S1"]


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


# ---- role restriction ------------------------------------------------------

class TestRoleRestriction:
    """Specialists may only call action='read'. Write actions are router-only."""

    def test_specialist_cannot_init(self, tmp_path):
        h = _specialist_handler(tmp_path)
        result = json.loads(h(action="init", title="T", steps=[{"title": "s"}]))
        assert "error" in result
        assert result.get("error_code") == "plan_write_forbidden_for_specialist"

    def test_specialist_cannot_mark(self, tmp_path):
        h_router = _handler(tmp_path)
        _init(h_router)
        h = _specialist_handler(tmp_path)
        result = json.loads(h(action="mark", step_id="S1", status="done"))
        assert "error" in result
        assert result.get("error_code") == "plan_write_forbidden_for_specialist"

    def test_specialist_cannot_add_substep(self, tmp_path):
        h_router = _handler(tmp_path)
        _init(h_router)
        h = _specialist_handler(tmp_path)
        result = json.loads(h(action="add_substep", parent_step_id="S1", title="x"))
        assert "error" in result
        assert result.get("error_code") == "plan_write_forbidden_for_specialist"

    def test_specialist_cannot_reset(self, tmp_path):
        h_router = _handler(tmp_path)
        _init(h_router)
        h = _specialist_handler(tmp_path)
        result = json.loads(h(action="reset", title="T", new_steps=[{"title": "s"}]))
        assert "error" in result
        assert result.get("error_code") == "plan_write_forbidden_for_specialist"

    def test_specialist_can_read(self, tmp_path):
        h_router = _handler(tmp_path)
        _init(h_router)
        h = _specialist_handler(tmp_path)
        result = json.loads(h(action="read"))
        assert result["action"] == "read"
        assert "# Plan" in result["content"]

    def test_router_can_all_write_actions(self, tmp_path):
        h = _handler(tmp_path, role="router")
        result = json.loads(h(action="init", title="T", steps=[{"title": "s"}]))
        assert result["action"] == "init"
        result = json.loads(h(action="mark", step_id="S1", status="done"))
        assert result["step_id"] == "S1"
        result = json.loads(h(action="add_substep", parent_step_id="S1", title="sub"))
        assert "new_step_id" in result


# ---- findings validation ---------------------------------------------------

class TestFindingsValidation:
    """findings= must be a non-empty string or absent."""

    def test_mark_rejects_bool_findings(self, tmp_path):
        h = _handler(tmp_path)
        _init(h)
        result = json.loads(h(action="mark", step_id="S1", status="done",
                               findings=False))
        assert "error" in result
        assert "findings" in result["error"]

    def test_mark_rejects_empty_string_findings(self, tmp_path):
        h = _handler(tmp_path)
        _init(h)
        result = json.loads(h(action="mark", step_id="S1", status="done",
                               findings=""))
        assert "error" in result
        assert "findings" in result["error"]

    def test_mark_rejects_int_findings(self, tmp_path):
        h = _handler(tmp_path)
        _init(h)
        result = json.loads(h(action="mark", step_id="S1", status="done",
                               findings=123))
        assert "error" in result
        assert "findings" in result["error"]

    def test_mark_accepts_none_findings(self, tmp_path):
        """findings=None (absent) is perfectly fine — not an error."""
        h = _handler(tmp_path)
        _init(h)
        result = json.loads(h(action="mark", step_id="S1", status="done"))
        assert "error" not in result
