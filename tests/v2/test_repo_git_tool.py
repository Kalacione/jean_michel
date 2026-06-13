"""Tests for repo_git — the read-only git introspection tool (Étage A)."""

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
from jeanmichel.tools import build_registry, repo_git  # noqa: E402

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)

    def run(*a):
        subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)

    run("init", "-b", "main")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (path / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    run("add", "-A")
    run("commit", "-m", "seed commit")


@pytest.fixture()
def wt(tmp_path, monkeypatch):
    repo = tmp_path / "proj"
    _init_repo(repo)
    monkeypatch.setattr(config, "PROJECT_ROOT", repo)
    monkeypatch.setattr(config, "CODE_WORKTREE_ENABLED", True)
    conv = tmp_path / "conv"
    conv.mkdir()
    root = worktree.create_worktree(conv, "c1")
    assert root is not None
    return conv, root


@requires_git
def test_repo_git_log_shows_history(wt):
    conv, _ = wt
    out = json.loads(repo_git.make_spec(conv).handler(subcommand="log"))
    assert out["exit_code"] == 0
    assert "seed commit" in out["output"]


@requires_git
def test_repo_git_defaults_to_log(wt):
    conv, _ = wt
    out = json.loads(repo_git.make_spec(conv).handler())  # no subcommand → log
    assert "seed commit" in out["output"]


@requires_git
def test_repo_git_status_works(wt):
    conv, _ = wt
    out = json.loads(repo_git.make_spec(conv).handler(subcommand="status"))
    assert out["exit_code"] == 0  # clean tree, branch line present
    assert "error" not in out


@requires_git
def test_repo_git_rejects_write_subcommands(wt):
    conv, _ = wt
    for bad in ("commit", "checkout", "reset", "push", "rm"):
        out = json.loads(repo_git.make_spec(conv).handler(subcommand=bad))
        assert out["error_code"] == "subcommand_not_allowed", bad


def test_repo_git_no_worktree(conv_folder):
    out = json.loads(repo_git.make_spec(conv_folder).handler())
    assert out["error_code"] == "no_worktree"


@requires_git
def test_registry_includes_repo_git(wt):
    reg = build_registry(wt[0])
    assert "repo_git" in reg
