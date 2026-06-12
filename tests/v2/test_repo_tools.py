"""Tests for the repo_* tools (P1) — in-place intervention on the code worktree.

Real git is required (the tools operate on a git worktree); these skip if git
or ripgrep is absent. The gates (read-before-edit, freshness, protected-path)
are the safety contract for editing real files, so they get explicit coverage.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from jeanmichel import config, worktree  # noqa: E402
from jeanmichel.tools import (  # noqa: E402
    build_registry, repo_edit, repo_glob, repo_grep, repo_read, repo_write,
)

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
requires_rg = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not available")


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)
    run("init", "-b", "main")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (path / "sample.py").write_text("X = 1\ndef f():\n    return X\n", encoding="utf-8")
    (path / "README.md").write_text("# demo\n", encoding="utf-8")
    run("add", "-A")
    run("commit", "-m", "init")


def _parse(s: str) -> dict:
    return json.loads(s)


@pytest.fixture()
def wt(tmp_path, monkeypatch):
    """A code worktree ready to edit: returns (conv_folder, worktree_root)."""
    repo = tmp_path / "proj"
    _init_repo(repo)
    monkeypatch.setattr(config, "PROJECT_ROOT", repo)
    monkeypatch.setattr(config, "CODE_WORKTREE_ENABLED", True)
    conv = tmp_path / "conv"
    conv.mkdir()
    root = worktree.create_worktree(conv, "c1")
    assert root is not None
    return conv, root


# ---- read / grep / glob -----------------------------------------------------


@requires_git
def test_repo_read_cat_n_and_marks_read(wt):
    conv, _ = wt
    out = _parse(repo_read.make_spec(conv).handler("sample.py"))
    assert "content" in out and "\t" in out["content"]  # cat -n uses a tab
    assert out["content"].strip().startswith("1")  # first line numbered 1
    assert out["total_lines"] == 3
    # The read was recorded in the ledger.
    assert (conv / ".repo_reads.json").exists()


@requires_git
def test_repo_read_offset_limit(wt):
    conv, _ = wt
    out = _parse(repo_read.make_spec(conv).handler("sample.py", offset=2, limit=1))
    assert "def f():" in out["content"]
    assert "X = 1" not in out["content"]


@requires_git
@requires_rg
def test_repo_grep_finds_and_modes(wt):
    conv, _ = wt
    out = _parse(repo_grep.make_spec(conv).handler("return X"))
    assert out["match_count"] == 1 and "sample.py" in out["matches"][0]
    files = _parse(repo_grep.make_spec(conv).handler("X", output_mode="files_with_matches"))
    assert any("sample.py" in m for m in files["matches"])
    none = _parse(repo_grep.make_spec(conv).handler("zzz_nomatch_zzz"))
    assert none["match_count"] == 0


@requires_git
@requires_rg
def test_repo_glob_lists_python(wt):
    conv, _ = wt
    out = _parse(repo_glob.make_spec(conv).handler("*.py"))
    assert out["files"] == ["sample.py"]


# ---- edit gates -------------------------------------------------------------


@requires_git
def test_repo_edit_requires_prior_read(wt):
    conv, _ = wt
    out = _parse(repo_edit.make_spec(conv).handler("sample.py", "X = 1", "X = 2"))
    assert out["error_code"] == "read_before_edit"


@requires_git
def test_repo_edit_succeeds_after_read(wt):
    conv, root = wt
    repo_read.make_spec(conv).handler("sample.py")
    out = _parse(repo_edit.make_spec(conv).handler("sample.py", "X = 1", "X = 42"))
    assert out.get("occurrences_replaced") == 1
    assert (root / "sample.py").read_text(encoding="utf-8").startswith("X = 42")


@requires_git
def test_repo_edit_stale_after_external_change(wt):
    conv, root = wt
    repo_read.make_spec(conv).handler("sample.py")
    # Simulate an out-of-band change → bump mtime distinctly.
    target = root / "sample.py"
    st = target.stat()
    target.write_text("X = 999\ndef f():\n    return X\n", encoding="utf-8")
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    out = _parse(repo_edit.make_spec(conv).handler("sample.py", "X = 999", "X = 0"))
    assert out["error_code"] == "stale_read"


@requires_git
def test_repo_edit_uniqueness_and_replace_all(wt):
    conv, root = wt
    # Two occurrences of "X" → not unique without replace_all.
    repo_read.make_spec(conv).handler("sample.py")
    out = _parse(repo_edit.make_spec(conv).handler("sample.py", "X", "Y"))
    assert out["error_code"] == "old_str_not_unique"
    out2 = _parse(repo_edit.make_spec(conv).handler("sample.py", "X", "Y", replace_all=True))
    assert out2["occurrences_replaced"] >= 2


@requires_git
def test_repo_edit_protected_path_denied(wt):
    conv, _ = wt
    out = _parse(repo_edit.make_spec(conv).handler(".env", "a", "b"))
    assert out["error_code"] == "protected_path"


@requires_git
def test_repo_edit_path_escape_denied(wt):
    conv, _ = wt
    out = _parse(repo_edit.make_spec(conv).handler("../escape.py", "a", "b"))
    assert out["error_code"] in ("path_escape", "absolute_path")


# ---- write ------------------------------------------------------------------


@requires_git
def test_repo_write_new_file(wt):
    conv, root = wt
    out = _parse(repo_write.make_spec(conv).handler("pkg/new.py", "print('hi')\n"))
    assert out.get("created") is True
    assert (root / "pkg" / "new.py").exists()


@requires_git
def test_repo_write_overwrite_requires_read(wt):
    conv, _ = wt
    out = _parse(repo_write.make_spec(conv).handler("sample.py", "X = 0\n"))
    assert out["error_code"] == "read_before_edit"


@requires_git
def test_repo_write_overwrite_after_read(wt):
    conv, root = wt
    repo_read.make_spec(conv).handler("sample.py")
    out = _parse(repo_write.make_spec(conv).handler("sample.py", "X = 7\n"))
    assert out.get("created") is False
    assert (root / "sample.py").read_text(encoding="utf-8") == "X = 7\n"


@requires_git
def test_repo_write_protected_denied(wt):
    conv, _ = wt
    out = _parse(repo_write.make_spec(conv).handler("jeanmichel.db", "x"))
    assert out["error_code"] == "protected_path"


# ---- registry gating --------------------------------------------------------


@requires_git
def test_registry_includes_repo_tools_when_worktree_exists(wt):
    conv, _ = wt
    reg = build_registry(conv)
    for name in ("repo_read", "repo_grep", "repo_glob", "repo_edit", "repo_write"):
        assert name in reg


def test_registry_excludes_repo_tools_without_worktree(conv_folder):
    # conv_folder has no worktree → repo tools must not be registered.
    reg = build_registry(conv_folder)
    for name in ("repo_read", "repo_edit", "repo_write"):
        assert name not in reg
