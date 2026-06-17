"""Tests for the plan_write tool — authors the durable rich plan document.

Phase 2 R2.1 : the plan lives in the shared workspace as plan_<id>.md (the orchestrator assigns the
active id before the tool runs ; with no state the tool defaults to p1 → workspace/plan_p1.md)."""

from __future__ import annotations

import json

from jeanmichel import todo as todomod
from jeanmichel.tools import plan_write

_P1 = "workspace/plan_p1.md"  # default target when no active id is assigned (no state.json)


def test_plan_write_saves_plan(tmp_path):
    res = plan_write.make_spec(tmp_path).handler(markdown="# Plan\n\n## Context\nReasoning.\n")
    payload = json.loads(res)
    assert "error" not in payload
    assert "plan saved" in payload["summary"]
    assert todomod.load_plan_file(tmp_path, _P1).startswith("# Plan")


def test_plan_write_rejects_empty(tmp_path):
    payload = json.loads(plan_write.make_spec(tmp_path).handler(markdown="   "))
    assert payload["error_code"] == "invalid_plan"
    assert todomod.load_plan_file(tmp_path, _P1) is None


def test_plan_write_overwrites(tmp_path):
    spec = plan_write.make_spec(tmp_path)
    spec.handler(markdown="# First")
    spec.handler(markdown="# Second")
    assert todomod.load_plan_file(tmp_path, _P1) == "# Second"
