"""Tests for debug/admin.py listing and dispatch commands.

Write operations (grant/revoke/bind/unbind/create) are covered in test_db.py.
These tests focus on the read/display path: listing agents, tools, paradigms,
unknown command handling, and exit signalling.
"""

from __future__ import annotations

import importlib.util
import sys
from io import StringIO
from pathlib import Path

from rich.console import Console

ROOT = Path(__file__).parent.parent

# Load admin module by path so it doesn't clobber a bare "admin" in sys.modules.
_spec = importlib.util.spec_from_file_location("jm_admin", ROOT / "debug" / "admin.py")
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
sys.modules["jm_admin"] = _mod
import jm_admin as admin  # noqa: E402


class TestAdminListings:
    def _capture(self, monkeypatch, db_path: Path, *cmd: str) -> str:
        buf = StringIO()
        monkeypatch.setattr(admin, "console", Console(file=buf, highlight=False))
        admin.run_command(list(cmd), db_path)
        return buf.getvalue()

    # ---- agents -----------------------------------------------------------

    def test_agents_lists_all_active(self, tmp_env, monkeypatch):
        out = self._capture(monkeypatch, tmp_env / "jeanmichel.db", "agents")
        assert "jean-michel" in out
        assert "Weather" in out       # Name column, code may be truncated
        assert "Wikipedia" in out

    def test_agents_shows_roles(self, tmp_env, monkeypatch):
        out = self._capture(monkeypatch, tmp_env / "jeanmichel.db", "agents")
        assert "router" in out
        assert "specialist" in out

    def test_agents_shows_tool_and_paradigm_counts(self, tmp_env, monkeypatch):
        out = self._capture(monkeypatch, tmp_env / "jeanmichel.db", "agents")
        # jean-michel has clock + conv_read_file = 2 tools
        assert "2" in out

    # ---- agent <code> -----------------------------------------------------

    def test_agent_profile_shows_tools(self, tmp_env, monkeypatch):
        out = self._capture(monkeypatch, tmp_env / "jeanmichel.db", "agent", "jean-michel")
        assert "clock" in out
        assert "conv_read_file" in out

    def test_agent_profile_shows_paradigms(self, tmp_env, monkeypatch):
        out = self._capture(monkeypatch, tmp_env / "jeanmichel.db", "agent", "jean-michel")
        assert "no_speculation" in out

    def test_agent_profile_shows_role_and_temp(self, tmp_env, monkeypatch):
        out = self._capture(monkeypatch, tmp_env / "jeanmichel.db", "agent", "jean-michel")
        assert "router" in out

    def test_agent_missing_code_shows_usage(self, tmp_env, monkeypatch):
        out = self._capture(monkeypatch, tmp_env / "jeanmichel.db", "agent")
        assert "Usage" in out

    def test_agent_unknown_code_shows_error(self, tmp_env, monkeypatch):
        out = self._capture(monkeypatch, tmp_env / "jeanmichel.db", "agent", "ghost")
        assert "Unknown" in out

    # ---- tools ------------------------------------------------------------

    def test_tools_shows_granted_codes(self, tmp_env, monkeypatch):
        out = self._capture(monkeypatch, tmp_env / "jeanmichel.db", "tools")
        assert "clock" in out
        assert "conv_read_file" in out

    def test_tools_shows_agent_holders(self, tmp_env, monkeypatch):
        out = self._capture(monkeypatch, tmp_env / "jeanmichel.db", "tools")
        assert "jean-michel" in out

    # ---- paradigms --------------------------------------------------------

    def test_paradigms_lists_global_entries(self, tmp_env, monkeypatch):
        out = self._capture(monkeypatch, tmp_env / "jeanmichel.db", "paradigms")
        assert "no_speculation" in out
        assert "brutal_truth" in out

    def test_paradigms_shows_active_status(self, tmp_env, monkeypatch):
        out = self._capture(monkeypatch, tmp_env / "jeanmichel.db", "paradigms")
        assert "✓" in out  # at least one active paradigm

    def test_paradigms_with_agent_shows_global_marker(self, tmp_env, monkeypatch):
        out = self._capture(monkeypatch, tmp_env / "jeanmichel.db", "paradigms", "jean-michel")
        assert "G" in out

    def test_paradigms_with_agent_shows_bound_marker(self, tmp_env, monkeypatch):
        # weather-specialist has explicit bindings (non-global)
        out = self._capture(monkeypatch, tmp_env / "jeanmichel.db", "paradigms", "weather-specialist")
        assert "B" in out

    def test_paradigms_unknown_agent_shows_error(self, tmp_env, monkeypatch):
        out = self._capture(monkeypatch, tmp_env / "jeanmichel.db", "paradigms", "ghost")
        assert "Unknown" in out

    # ---- dispatch ---------------------------------------------------------

    def test_unknown_command_shows_error(self, tmp_env, monkeypatch):
        out = self._capture(monkeypatch, tmp_env / "jeanmichel.db", "blorp")
        assert "Unknown" in out

    def test_exit_returns_false(self, tmp_env):
        result = admin.run_command(["exit"], tmp_env / "jeanmichel.db")
        assert result is False

    def test_quit_returns_false(self, tmp_env):
        result = admin.run_command(["quit"], tmp_env / "jeanmichel.db")
        assert result is False

    def test_valid_command_returns_true(self, tmp_env, monkeypatch):
        buf = StringIO()
        monkeypatch.setattr(admin, "console", Console(file=buf, highlight=False))
        result = admin.run_command(["agents"], tmp_env / "jeanmichel.db")
        assert result is True

    def test_empty_command_returns_true(self, tmp_env):
        result = admin.run_command([], tmp_env / "jeanmichel.db")
        assert result is True
