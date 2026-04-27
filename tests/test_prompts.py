"""Unit tests for src/jeanmichel/prompts.py."""

from __future__ import annotations

from jeanmichel.prompts import tools_payload_for_agent
from jeanmichel.tools.clock import SPEC as clock_spec


def test_control_tools_always_present():
    payload = tools_payload_for_agent([], {})
    names = {e["function"]["name"] for e in payload}
    assert {"return_to_user", "delegate_to", "ask_human"} <= names


def test_granted_tool_appears_in_payload():
    registry = {"clock": clock_spec}
    payload = tools_payload_for_agent(["clock"], registry)
    names = {e["function"]["name"] for e in payload}
    assert "clock" in names


def test_unknown_grant_silently_skipped():
    payload = tools_payload_for_agent(["ghost"], {})
    names = {e["function"]["name"] for e in payload}
    assert "ghost" not in names
