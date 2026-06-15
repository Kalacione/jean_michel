"""Tests for the plan_write tool — authors the durable rich plan document (plan.md)."""

from __future__ import annotations

import json

from jeanmichel import todo as todomod
from jeanmichel.tools import plan_write


def test_plan_write_saves_plan(tmp_path):
    res = plan_write.make_spec(tmp_path).handler(markdown="# Plan\n\n## Context\nReasoning.\n")
    payload = json.loads(res)
    assert "error" not in payload
    assert "plan saved" in payload["summary"]
    assert todomod.load_plan(tmp_path).startswith("# Plan")


def test_plan_write_rejects_empty(tmp_path):
    payload = json.loads(plan_write.make_spec(tmp_path).handler(markdown="   "))
    assert payload["error_code"] == "invalid_plan"
    assert todomod.load_plan(tmp_path) is None


def test_plan_write_overwrites(tmp_path):
    spec = plan_write.make_spec(tmp_path)
    spec.handler(markdown="# First")
    spec.handler(markdown="# Second")
    assert todomod.load_plan(tmp_path) == "# Second"
