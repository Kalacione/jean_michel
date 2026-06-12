"""Tests for the P3 tools: repo_test (structured test runner) + repo_graph_refresh."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from jeanmichel import config, worktree  # noqa: E402
from jeanmichel.tools import build_registry, repo_graph_refresh, repo_test  # noqa: E402

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")

_PASS_TEST = "def test_ok():\n    assert 1 + 1 == 2\n"
_FAIL_TEST = "def test_bad():\n    assert 1 + 1 == 3\n"


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)
    run("init", "-b", "main")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (path / "test_sample.py").write_text(_PASS_TEST, encoding="utf-8")
    run("add", "-A")
    run("commit", "-m", "init")


@pytest.fixture()
def wt(tmp_path, monkeypatch):
    repo = tmp_path / "proj"
    _init_repo(repo)
    monkeypatch.setattr(config, "PROJECT_ROOT", repo)
    monkeypatch.setattr(config, "CODE_WORKTREE_ENABLED", True)
    # Leave REPO_TEST_CMD empty → exercise the AUTO-detect default (the tmp repo
    # has no .venv, so it falls back to sys.executable, which ships pytest).
    monkeypatch.setattr(config, "REPO_TEST_CMD", "")
    conv = tmp_path / "conv"
    conv.mkdir()
    root = worktree.create_worktree(conv, "c1")
    assert root is not None
    return conv, root


# ---- repo_test --------------------------------------------------------------


@requires_git
def test_repo_test_passes(wt):
    conv, _ = wt
    out = json.loads(repo_test.make_spec(conv).handler())
    assert out["passed"] is True
    assert out["exit_code"] == 0
    assert out["counts"].get("passed") == 1


@requires_git
def test_repo_test_reports_failure_structured(wt):
    conv, root = wt
    (root / "test_sample.py").write_text(_FAIL_TEST, encoding="utf-8")
    out = json.loads(repo_test.make_spec(conv).handler())
    assert out["passed"] is False
    assert out["exit_code"] != 0
    assert out["counts"].get("failed") == 1
    assert any("test_bad" in f for f in out["failed"])


def test_repo_test_no_worktree(conv_folder):
    out = json.loads(repo_test.make_spec(conv_folder).handler())
    assert out["error_code"] == "no_worktree"


# ---- repo_graph_refresh -----------------------------------------------------


@requires_git
def test_repo_graph_refresh_unavailable(wt, monkeypatch):
    # Force graphify "absent" so the test never shells out to a real build.
    monkeypatch.setattr(repo_graph_refresh.shutil, "which", lambda name: None)
    out = json.loads(repo_graph_refresh.make_spec(wt[0]).handler())
    assert out["error_code"] == "graphify_unavailable"


def test_repo_graph_refresh_no_worktree(conv_folder):
    out = json.loads(repo_graph_refresh.make_spec(conv_folder).handler())
    assert out["error_code"] == "no_worktree"


# ---- registry ---------------------------------------------------------------


@requires_git
def test_registry_includes_p3_tools(wt):
    reg = build_registry(wt[0])
    assert "repo_test" in reg and "repo_graph_refresh" in reg
