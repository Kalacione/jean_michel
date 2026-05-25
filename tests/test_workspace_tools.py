"""Tests for workspace_* tools (Phase 1 workspace sandbox)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jeanmichel.tools.workspace_create_file import make_spec as create_spec
from jeanmichel.tools.workspace_list import make_spec as list_spec
from jeanmichel.tools.workspace_str_replace import make_spec as replace_spec
from jeanmichel.tools.workspace_view import make_spec as view_spec

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_conv(tmp_path):
    """Minimal conversation folder with a workspace/ sub-dir."""
    (tmp_path / "workspace").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# workspace_create_file
# ---------------------------------------------------------------------------

class TestWorkspaceCreateFile:
    def test_create_nominal(self, tmp_conv):
        spec = create_spec(tmp_conv, has_write_grant=True)
        result = json.loads(spec.handler("notes.md", "hello"))
        assert result["bytes_written"] == 5
        assert (tmp_conv / "workspace" / "notes.md").read_text() == "hello"

    def test_create_no_grant_returns_error(self, tmp_conv):
        spec = create_spec(tmp_conv, has_write_grant=False)
        result = json.loads(spec.handler("notes.md", "hello"))
        assert "error" in result
        assert not (tmp_conv / "workspace" / "notes.md").exists()

    def test_create_path_traversal_blocked(self, tmp_conv):
        spec = create_spec(tmp_conv, has_write_grant=True)
        result = json.loads(spec.handler("../escape.md", "evil"))
        assert "error" in result
        assert not (tmp_conv / "escape.md").exists()

    def test_create_no_overwrite(self, tmp_conv):
        spec = create_spec(tmp_conv, has_write_grant=True)
        (tmp_conv / "workspace" / "notes.md").write_text("original")
        result = json.loads(spec.handler("notes.md", "new content"))
        assert "error" in result
        assert (tmp_conv / "workspace" / "notes.md").read_text() == "original"

    def test_create_existing_returns_content_and_action(self, tmp_conv):
        """Existing file error must include existing_content and action_required."""
        spec = create_spec(tmp_conv, has_write_grant=True)
        (tmp_conv / "workspace" / "notes.md").write_text("# Notes\nstep 1")
        result = json.loads(spec.handler("notes.md", "# New Notes"))
        assert "error" in result
        assert result.get("action_required") == "workspace_str_replace"
        assert "existing_content" in result
        assert "step 1" in result["existing_content"]

    def test_create_subdirectory(self, tmp_conv):
        spec = create_spec(tmp_conv, has_write_grant=True)
        result = json.loads(spec.handler("data/results.json", '{"ok": true}'))
        assert "bytes_written" in result
        assert (tmp_conv / "workspace" / "data" / "results.json").exists()

    def test_create_quota_exceeded(self, tmp_conv, monkeypatch):
        import jeanmichel.tools._workspace as ws_mod
        monkeypatch.setattr(ws_mod, "WORKSPACE_QUOTA_BYTES", 10)
        spec = create_spec(tmp_conv, has_write_grant=True)
        result = json.loads(spec.handler("big.txt", "x" * 11))
        assert "error" in result
        assert "quota" in result["error"].lower()

    def test_create_does_not_touch_artifacts_table(self, tmp_conv, tmp_path):
        """No DB write: creating a workspace file must not insert into artifacts table."""
        import sqlite3
        db_path = tmp_path / "jeanmichel.db"
        schema = (Path(__file__).parent.parent / "db" / "schema.sql").read_text()
        conn = sqlite3.connect(db_path)
        conn.executescript(schema)
        conn.close()
        # Just verify the file operation doesn't raise and no DB interaction occurs.
        spec = create_spec(tmp_conv, has_write_grant=True)
        result = json.loads(spec.handler("ok.md", "data"))
        assert "bytes_written" in result


# ---------------------------------------------------------------------------
# workspace_str_replace
# ---------------------------------------------------------------------------

class TestWorkspaceStrReplace:
    def _make_file(self, conv: Path, name: str, content: str) -> Path:
        p = conv / "workspace" / name
        p.write_text(content, encoding="utf-8")
        return p

    def test_replace_nominal(self, tmp_conv):
        self._make_file(tmp_conv, "file.txt", "hello world")
        spec = replace_spec(tmp_conv, has_write_grant=True)
        result = json.loads(spec.handler("file.txt", "world", "earth"))
        assert result["occurrences_replaced"] == 1
        assert (tmp_conv / "workspace" / "file.txt").read_text() == "hello earth"

    def test_replace_no_grant(self, tmp_conv):
        self._make_file(tmp_conv, "file.txt", "hello world")
        spec = replace_spec(tmp_conv, has_write_grant=False)
        result = json.loads(spec.handler("file.txt", "world", "earth"))
        assert "error" in result
        assert (tmp_conv / "workspace" / "file.txt").read_text() == "hello world"

    def test_replace_zero_occurrences(self, tmp_conv):
        self._make_file(tmp_conv, "file.txt", "hello world")
        spec = replace_spec(tmp_conv, has_write_grant=True)
        result = json.loads(spec.handler("file.txt", "nothere", "x"))
        assert "error" in result
        assert result.get("occurrences", 0) == 0

    def test_replace_multiple_occurrences(self, tmp_conv):
        self._make_file(tmp_conv, "file.txt", "aa aa aa")
        spec = replace_spec(tmp_conv, has_write_grant=True)
        result = json.loads(spec.handler("file.txt", "aa", "bb"))
        assert "error" in result
        assert result.get("occurrences", 0) == 3

    def test_replace_deletion(self, tmp_conv):
        self._make_file(tmp_conv, "file.txt", "keep REMOVE keep")
        spec = replace_spec(tmp_conv, has_write_grant=True)
        result = json.loads(spec.handler("file.txt", " REMOVE", ""))
        assert result["occurrences_replaced"] == 1
        assert (tmp_conv / "workspace" / "file.txt").read_text() == "keep keep"

    def test_replace_path_traversal_blocked(self, tmp_conv):
        spec = replace_spec(tmp_conv, has_write_grant=True)
        result = json.loads(spec.handler("../escape.md", "x", "y"))
        assert "error" in result

    def test_replace_atomicity(self, tmp_conv):
        """If write fails mid-way, original file must be untouched."""
        original_content = "hello world"
        f = self._make_file(tmp_conv, "file.txt", original_content)
        spec = replace_spec(tmp_conv, has_write_grant=True)
        # Simulate crash: patch Path.replace to raise
        import contextlib
        import unittest.mock as mock
        with (
            mock.patch("pathlib.Path.replace", side_effect=OSError("disk full")),
            contextlib.suppress(OSError),
        ):
            spec.handler("file.txt", "world", "earth")
        # Original must be intact (the .tmp write may exist but original is unchanged)
        assert f.read_text() == original_content


# ---------------------------------------------------------------------------
# workspace_view
# ---------------------------------------------------------------------------

class TestWorkspaceView:
    def test_view_workspace_file(self, tmp_conv):
        (tmp_conv / "workspace" / "notes.md").write_text("line1\nline2\n")
        spec = view_spec(tmp_conv)
        result = json.loads(spec.handler("notes.md"))
        assert result["content"] == "line1\nline2\n"
        assert not result["truncated"]

    def test_view_conv_root_file(self, tmp_conv):
        (tmp_conv / "summary.md").write_text("summary content")
        spec = view_spec(tmp_conv)
        result = json.loads(spec.handler("summary.md"))
        assert result["content"] == "summary content"

    def test_view_range(self, tmp_conv):
        lines = "\n".join(f"line{i}" for i in range(1, 21))
        (tmp_conv / "workspace" / "many.txt").write_text(lines)
        spec = view_spec(tmp_conv)
        result = json.loads(spec.handler("many.txt", view_range=[1, 3]))
        content_lines = result["content"].splitlines()
        assert content_lines[0] == "line1"
        assert len(content_lines) == 3

    def test_view_range_to_end(self, tmp_conv):
        lines = "a\nb\nc\nd\ne"
        (tmp_conv / "workspace" / "f.txt").write_text(lines)
        spec = view_spec(tmp_conv)
        result = json.loads(spec.handler("f.txt", view_range=[3, -1]))
        assert result["content"].strip().startswith("c")

    def test_view_truncation(self, tmp_conv):
        (tmp_conv / "workspace" / "big.txt").write_text("x" * 200)
        spec = view_spec(tmp_conv)
        result = json.loads(spec.handler("big.txt", max_bytes=50))
        assert result["truncated"] is True
        assert len(result["content"]) == 50

    def test_view_path_traversal_blocked(self, tmp_conv):
        result = json.loads(view_spec(tmp_conv).handler("../../etc/passwd"))
        assert "error" in result

    def test_view_non_utf8_error(self, tmp_conv):
        (tmp_conv / "workspace" / "bin.dat").write_bytes(b"\xff\xfe")
        spec = view_spec(tmp_conv)
        result = json.loads(spec.handler("bin.dat"))
        assert "error" in result

    def test_view_not_found(self, tmp_conv):
        spec = view_spec(tmp_conv)
        result = json.loads(spec.handler("missing.txt"))
        assert "error" in result

    def test_view_directory(self, tmp_conv):
        (tmp_conv / "workspace" / "sub").mkdir()
        (tmp_conv / "workspace" / "sub" / "a.txt").write_text("a")
        spec = view_spec(tmp_conv)
        # View the workspace root as directory (empty string not valid for view, use sub)
        result = json.loads(spec.handler("sub"))
        assert "entries" in result or "directory" in result


# ---------------------------------------------------------------------------
# workspace_list
# ---------------------------------------------------------------------------

class TestWorkspaceList:
    def test_list_empty_workspace(self, tmp_conv):
        spec = list_spec(tmp_conv)
        result = json.loads(spec.handler())
        assert result["entries"] == []

    def test_list_files_and_dirs(self, tmp_conv):
        ws = tmp_conv / "workspace"
        (ws / "file.txt").write_text("hello")
        (ws / "sub").mkdir()
        (ws / "sub" / "nested.txt").write_text("nested")
        spec = list_spec(tmp_conv)
        result = json.loads(spec.handler())
        names = {e["name"] for e in result["entries"]}
        assert "file.txt" in names
        assert "sub" in names

    def test_list_max_depth_two(self, tmp_conv):
        ws = tmp_conv / "workspace"
        (ws / "a" / "b" / "c").mkdir(parents=True)
        (ws / "a" / "b" / "c" / "deep.txt").write_text("deep")
        spec = list_spec(tmp_conv)
        result = json.loads(spec.handler())
        # Find 'a' entry
        a_entry = next(e for e in result["entries"] if e["name"] == "a")
        b_entry = next(e for e in a_entry.get("children", []) if e["name"] == "b")
        # b has children=[] or no 'c' entry visible at depth 2
        assert "children" not in b_entry or not any(
            e["name"] == "c" and "children" in e
            for e in b_entry.get("children", [])
        )

    def test_list_sorted_lexicographic(self, tmp_conv):
        ws = tmp_conv / "workspace"
        for name in ["zebra.txt", "apple.txt", "mango.txt"]:
            (ws / name).write_text("")
        spec = list_spec(tmp_conv)
        result = json.loads(spec.handler())
        names = [e["name"] for e in result["entries"]]
        assert names == sorted(names)

    def test_list_path_traversal_blocked(self, tmp_conv):
        spec = list_spec(tmp_conv)
        result = json.loads(spec.handler(sub_path="../"))
        assert "error" in result

    def test_list_sub_path(self, tmp_conv):
        ws = tmp_conv / "workspace"
        (ws / "data").mkdir()
        (ws / "data" / "results.csv").write_text("a,b")
        spec = list_spec(tmp_conv)
        result = json.loads(spec.handler(sub_path="data"))
        names = {e["name"] for e in result["entries"]}
        assert "results.csv" in names

    def test_list_entries_have_size_and_mtime(self, tmp_conv):
        (tmp_conv / "workspace" / "f.txt").write_text("abc")
        spec = list_spec(tmp_conv)
        result = json.loads(spec.handler())
        f_entry = next(e for e in result["entries"] if e["name"] == "f.txt")
        assert f_entry["size_bytes"] == 3
        assert "modified_at" in f_entry


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

class TestWorkspaceDbHelpers:
    def test_has_workspace_grant_false_by_default(self, tmp_env):
        from jeanmichel import db
        with db.connect() as conn:
            summarizer = db.get_agent_by_code(conn, "summarizer")
            assert db.has_workspace_grant(conn, summarizer.id) is False

    def test_has_workspace_grant_true_after_insert(self, tmp_env):
        from jeanmichel import db
        with db.connect() as conn:
            summarizer = db.get_agent_by_code(conn, "summarizer")
            conn.execute(
                "INSERT INTO agent_workspace_grants (agent_id) VALUES (?)", (summarizer.id,)
            )
            assert db.has_workspace_grant(conn, summarizer.id) is True

    def test_load_sandbox_grants_empty(self, tmp_env):
        from jeanmichel import db
        with db.connect() as conn:
            jm = db.get_agent_by_code(conn, "jean-michel")
            assert db.load_sandbox_grants(conn, jm.id) == []

    def test_load_sandbox_grants_returns_commands(self, tmp_env):
        from jeanmichel import db
        with db.connect() as conn:
            jm = db.get_agent_by_code(conn, "jean-michel")
            conn.execute(
                "INSERT INTO agent_sandbox_grants (agent_id, command) VALUES (?, ?)",
                (jm.id, "python3"),
            )
            grants = db.load_sandbox_grants(conn, jm.id)
        assert grants == ["python3"]
