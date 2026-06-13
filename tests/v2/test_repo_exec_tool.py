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
from jeanmichel.tools import bash_sandbox, build_registry, repo_exec  # noqa: E402

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
    real_run = subprocess.run
    runs = []

    def fake_run(cmd, **kw):
        if cmd and cmd[0] == "docker":
            runs.append(cmd)
            return _FakeProc(rc=0, out="ok\n")
        return real_run(cmd, **kw)  # let git (source_repo) run for real

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
def test_repo_exec_refuses_dangerous_footguns(wt):
    # The container is the real boundary; this tripwire refuses obvious footguns
    # before they waste a sandbox round (no docker call — fires before start).
    for bad in ("rm -rf /", "rm -rf ~", ":(){ :|:& };:", "dd if=/dev/zero of=/dev/sda"):
        out = json.loads(repo_exec.make_spec(wt, conv_id="c1").handler(command=bad))
        assert out["error_code"] == "dangerous_command", bad


@requires_git
def test_registry_includes_repo_exec(wt):
    reg = build_registry(wt, conv_id="c1")
    assert "repo_exec" in reg


# ---- B4: per-project image resolution --------------------------------------


@requires_git
def test_resolve_image_no_dockerfile_returns_default(wt):
    # The source repo has no .jm/Dockerfile → the agent's default image is used.
    assert repo_exec._resolve_image(wt, "default:img") == "default:img"


@requires_git
def test_resolve_image_builds_project_image_from_source_dockerfile(wt, monkeypatch):
    src = worktree.source_repo(wt)
    (src / ".jm").mkdir()
    (src / ".jm" / "Dockerfile").write_text("FROM alpine:3.20\n", encoding="utf-8")
    real_run = subprocess.run
    calls = []

    def fake_run(cmd, **kw):
        if cmd and cmd[0] == "docker":
            calls.append(cmd)
            # docker image inspect → miss (rc 1) ; docker build → ok (rc 0)
            return _FakeProc(rc=1 if cmd[:3] == ["docker", "image", "inspect"] else 0)
        return real_run(cmd, **kw)  # git (source_repo) runs for real

    monkeypatch.setattr(repo_exec.subprocess, "run", fake_run)
    tag = repo_exec._resolve_image(wt, "default:img")
    assert tag.startswith("jeanmichel-sandbox:project-")
    assert any(c[:2] == ["docker", "build"] for c in calls)


@requires_git
def test_resolve_image_build_failure_falls_back(wt, monkeypatch):
    src = worktree.source_repo(wt)
    (src / ".jm").mkdir()
    (src / ".jm" / "Dockerfile").write_text("FROM alpine:3.20\n", encoding="utf-8")
    real_run = subprocess.run

    def fake_run(cmd, **kw):
        if cmd and cmd[0] == "docker":
            return _FakeProc(rc=1)  # inspect miss + build fail
        return real_run(cmd, **kw)

    monkeypatch.setattr(repo_exec.subprocess, "run", fake_run)
    assert repo_exec._resolve_image(wt, "default:img") == "default:img"


# ---- B5: reap covers both prefixes + per-conv filter -----------------------


def test_reap_covers_sandbox_and_repo_containers(monkeypatch):
    def fake_run(cmd, **kw):
        if cmd[:2] == ["docker", "ps"]:
            return _FakeProc(rc=0, out="jm-sandbox-abc\njm-repo-abc\nunrelated\n")
        return _FakeProc(rc=0)  # docker stop

    monkeypatch.setattr(bash_sandbox.subprocess, "run", fake_run)
    stopped = bash_sandbox.reap_sandboxes()
    assert set(stopped) == {"jm-sandbox-abc", "jm-repo-abc"}  # 'unrelated' excluded


def test_reap_conv_id_filter_targets_one_conversation(monkeypatch):
    def fake_run(cmd, **kw):
        if cmd[:2] == ["docker", "ps"]:
            return _FakeProc(rc=0, out="jm-sandbox-aaa\njm-repo-aaa\njm-sandbox-bbb\n")
        return _FakeProc(rc=0)

    monkeypatch.setattr(bash_sandbox.subprocess, "run", fake_run)
    stopped = bash_sandbox.reap_sandboxes(conv_id="aaa")
    assert set(stopped) == {"jm-sandbox-aaa", "jm-repo-aaa"}  # bbb untouched
