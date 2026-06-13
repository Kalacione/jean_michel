"""Tests for repo_exec — the project sandbox tool (Étage B). Docker is mocked."""

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
from jeanmichel.tools import build_registry, repo_exec  # noqa: E402

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
    run("commit", "-m", "seed")


@pytest.fixture()
def wt(tmp_path, monkeypatch):
    repo = tmp_path / "proj"
    _init_repo(repo)
    monkeypatch.setattr(config, "PROJECT_ROOT", repo)
    monkeypatch.setattr(config, "CODE_WORKTREE_ENABLED", True)
    conv = tmp_path / "conv"
    conv.mkdir()
    assert worktree.create_worktree(conv, "c1") is not None
    return conv


class _FakeProc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


@requires_git
def test_repo_exec_runs_via_docker_exec(wt, monkeypatch):
    monkeypatch.setattr(repo_exec, "_container_running", lambda name: True)
    calls = {}

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        return _FakeProc(rc=0, out="hello\n")

    monkeypatch.setattr(repo_exec.subprocess, "run", fake_run)
    out = json.loads(repo_exec.make_spec(wt, conv_id="c1").handler(command="echo hello"))
    assert out["exit_code"] == 0
    assert out["stdout"].strip() == "hello"
    assert calls["cmd"][:2] == ["docker", "exec"]


@requires_git
def test_repo_exec_starts_confined_container_if_down(wt, monkeypatch):
    monkeypatch.setattr(repo_exec, "_container_running", lambda name: False)
    runs = []

    def fake_run(cmd, **kw):
        runs.append(cmd)
        return _FakeProc(rc=0, out="ok\n")

    monkeypatch.setattr(repo_exec.subprocess, "run", fake_run)
    out = json.loads(repo_exec.make_spec(wt, conv_id="c1").handler(command="ls"))
    assert out["exit_code"] == 0
    # First call starts the container, offline, mounting the worktree at /app.
    start = runs[0]
    assert start[:3] == ["docker", "run", "-d"]
    assert "--network=none" in start and "--cap-drop=ALL" in start
    assert any(tok.endswith(":/app:rw") for tok in start)
    # Then the command runs via docker exec.
    assert any(c[:2] == ["docker", "exec"] for c in runs)


def test_repo_exec_no_worktree(conv_folder):
    out = json.loads(repo_exec.make_spec(conv_folder, conv_id="x").handler(command="ls"))
    assert out["error_code"] == "no_worktree"


@requires_git
def test_repo_exec_empty_command(wt):
    out = json.loads(repo_exec.make_spec(wt, conv_id="c1").handler(command="   "))
    assert out["error_code"] == "empty_command"


@requires_git
def test_registry_includes_repo_exec(wt):
    reg = build_registry(wt, conv_id="c1")
    assert "repo_exec" in reg
