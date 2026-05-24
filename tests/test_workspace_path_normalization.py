"""Tests for workspace path normalisation (strip leading workspace/ prefix)."""

from __future__ import annotations

import json
import logging

import pytest

from jeanmichel.tools._workspace import safe_resolve
from jeanmichel.tools.workspace_create_file import make_spec as create_spec


@pytest.fixture()
def ws(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    return root


# ---------------------------------------------------------------------------
# safe_resolve unit tests
# ---------------------------------------------------------------------------

class TestSafeResolve:
    def test_strip_workspace_prefix(self, ws, caplog):
        with caplog.at_level(logging.WARNING, logger="jeanmichel.tools._workspace"):
            result = safe_resolve(ws, "workspace/plan.md")
        assert result == (ws / "plan.md").resolve()
        assert any("normalised" in r.message for r in caplog.records)

    def test_strip_workspace_prefix_case_insensitive(self, ws, caplog):
        with caplog.at_level(logging.WARNING, logger="jeanmichel.tools._workspace"):
            result = safe_resolve(ws, "WORKSPACE/plan.md")
        assert result == (ws / "plan.md").resolve()
        assert any("normalised" in r.message for r in caplog.records)

    def test_no_strip_for_plain_path(self, ws, caplog):
        with caplog.at_level(logging.WARNING, logger="jeanmichel.tools._workspace"):
            result = safe_resolve(ws, "plan.md")
        assert result == (ws / "plan.md").resolve()
        assert not caplog.records

    def test_absolute_path_rejected(self, ws):
        with pytest.raises(ValueError, match="absolute"):
            safe_resolve(ws, "/etc/passwd")

    def test_dotdot_rejected(self, ws):
        with pytest.raises(ValueError, match="escapes|\\.\\."):
            safe_resolve(ws, "../escape.md")

    def test_workspace_only_rejected(self, ws):
        with pytest.raises(ValueError, match="workspace root"):
            safe_resolve(ws, "workspace/")

    def test_strip_dotslash_workspace_prefix(self, ws, caplog):
        with caplog.at_level(logging.WARNING, logger="jeanmichel.tools._workspace"):
            result = safe_resolve(ws, "./workspace/notes.md")
        assert result == (ws / "notes.md").resolve()
        assert any("normalised" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# e2e: workspace_create_file with "workspace/" prefix in relative_path
# ---------------------------------------------------------------------------

class TestCreateFileStripE2E:
    def test_create_strips_prefix_and_creates_at_root(self, tmp_path):
        """LLM passes 'workspace/notes.md' → file created at workspace/notes.md (not nested)."""
        (tmp_path / "workspace").mkdir()
        spec = create_spec(tmp_path, has_write_grant=True)
        result = json.loads(spec.handler("workspace/notes.md", "hello"))
        assert "error" not in result
        assert (tmp_path / "workspace" / "notes.md").exists()
        assert not (tmp_path / "workspace" / "workspace").exists()

    def test_create_strips_uppercase_prefix(self, tmp_path):
        (tmp_path / "workspace").mkdir()
        spec = create_spec(tmp_path, has_write_grant=True)
        result = json.loads(spec.handler("WORKSPACE/notes.md", "hello"))
        assert "error" not in result
        assert (tmp_path / "workspace" / "notes.md").exists()
        assert not (tmp_path / "workspace" / "WORKSPACE").exists()
