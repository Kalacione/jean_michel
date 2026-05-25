"""Unit tests for src/jeanmichel/prompts.py and jeanmichel.config.UserProfile."""

from __future__ import annotations

from jeanmichel.config import UserProfile
from jeanmichel.models import Agent
from jeanmichel.prompts import PromptContext, render_system_prompt, tools_payload_for_agent
from jeanmichel.tools.clock import SPEC as CLOCK_SPEC


def _minimal_ctx(**overrides) -> PromptContext:
    defaults = dict(
        agent=Agent(id=1, code="jean-michel", name="Jean-Michel", role="router",
                    mission="Route requests.", temperature=0.5, thinking_mode=False),
        paradigms=[],
        user_profile=UserProfile(),
        detected_language="fr",
        conversation_id="abc123",
        conversation_folder="/tmp/conv",
        request_id="req1",
        parent_request_id=None,
        depth=0,
        mode="analyse",
        turn_index=0,
        sender="human",
        expected_outcome=None,
        support_files=[],
        inbound_text="hello",
        tool_registry={},
        available_agents=[],
        turn_clarifications=[],
    )
    defaults.update(overrides)
    return PromptContext(**defaults)


class TestConvBudgetInjection:
    def test_no_budget_section_when_none(self):
        ctx = _minimal_ctx(conv_budget=None)
        prompt = render_system_prompt(ctx)
        assert "## Budget" not in prompt

    def test_budget_section_present_when_provided(self):
        ctx = _minimal_ctx(conv_budget="- total_tool_calls: 7\n- SIGNAL: WARNING: agent has 7 calls")
        prompt = render_system_prompt(ctx)
        assert "## Budget" in prompt
        assert "total_tool_calls: 7" in prompt
        assert "SIGNAL:" in prompt

    def test_budget_appears_before_machine(self):
        ctx = _minimal_ctx(conv_budget="- total_tool_calls: 3")
        prompt = render_system_prompt(ctx)
        assert prompt.index("## Budget") < prompt.index("## Machine")


def test_control_tools_router_has_all_three():
    payload = tools_payload_for_agent("router", [], {})
    names = {e["function"]["name"] for e in payload}
    assert {"return_to_user", "delegate_to", "ask_human"} <= names


def test_control_tools_finalizer_has_only_return():
    payload = tools_payload_for_agent("finalizer", [], {})
    names = {e["function"]["name"] for e in payload}
    assert "return_to_user" in names
    assert "delegate_to" not in names
    assert "ask_human" not in names


def test_granted_tool_appears_in_payload():
    registry = {"clock": CLOCK_SPEC}
    payload = tools_payload_for_agent("router", ["clock"], registry)
    names = {e["function"]["name"] for e in payload}
    assert "clock" in names


def test_unknown_grant_silently_skipped():
    payload = tools_payload_for_agent("router", ["ghost"], {})
    names = {e["function"]["name"] for e in payload}
    assert "ghost" not in names


# ---- UserProfile -----------------------------------------------------------

class TestUserProfile:
    def test_render_structured_fields(self):
        p = UserProfile(name="Jeremy", city="Montreal", language="french")
        out = p.render()
        assert "name: Jeremy" in out
        assert "city: Montreal" in out
        assert "language: french" in out

    def test_render_skips_empty_fields(self):
        p = UserProfile(name="Alice")
        out = p.render()
        assert "city:" not in out
        assert "birthdate:" not in out

    def test_render_notes_appended(self):
        p = UserProfile(city="Paris", notes="Some extra context.")
        out = p.render()
        assert "city: Paris" in out
        assert "Some extra context." in out

    def test_render_notes_only(self):
        p = UserProfile(notes="Just a note.")
        assert p.render() == "Just a note."

    def test_render_empty_profile(self):
        assert UserProfile().render() == "No user profile provided."

    def test_load_from_toml(self, tmp_path):
        toml = tmp_path / "user_profile.toml"
        toml.write_text(
            'name = "Jeremy"\ncity = "Montreal"\nlanguage = "french"\n',
            encoding="utf-8",
        )
        p = UserProfile.load(toml)
        assert p.name == "Jeremy"
        assert p.city == "Montreal"
        assert p.language == "french"

    def test_load_missing_file_returns_empty(self, tmp_path):
        p = UserProfile.load(tmp_path / "nonexistent.toml")
        assert p.render() == "No user profile provided."

    def test_load_partial_fields(self, tmp_path):
        toml = tmp_path / "user_profile.toml"
        toml.write_text('city = "Tokyo"\n', encoding="utf-8")
        p = UserProfile.load(toml)
        assert p.city == "Tokyo"
        assert p.name == ""
