"""Unit tests for src/jeanmichel/prompts.py and jeanmichel.config.UserProfile."""

from __future__ import annotations

from jeanmichel.config import UserProfile
from jeanmichel.prompts import tools_payload_for_agent
from jeanmichel.tools.clock import SPEC as CLOCK_SPEC


def test_control_tools_always_present():
    payload = tools_payload_for_agent([], {})
    names = {e["function"]["name"] for e in payload}
    assert {"return_to_user", "delegate_to", "ask_human"} <= names


def test_granted_tool_appears_in_payload():
    registry = {"clock": CLOCK_SPEC}
    payload = tools_payload_for_agent(["clock"], registry)
    names = {e["function"]["name"] for e in payload}
    assert "clock" in names


def test_unknown_grant_silently_skipped():
    payload = tools_payload_for_agent(["ghost"], {})
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
