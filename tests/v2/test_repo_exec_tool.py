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
    # Both spaces mounted: the repo at /app (cwd) AND the scratch at /workspace.
    assert any(tok.endswith(":/app:rw") for tok in start)
    assert any(tok.endswith(":/workspace:rw") for tok in start)
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


# ---- B4 / C3: per-project image resolution (from the project Dockerfile) ---

_REPO_DEFAULT = "jeanmichel-sandbox:repo-default"


def test_project_image_tag_deterministic_and_keyed():
    t1 = repo_exec.project_image_tag(5, "FROM alpine\n")
    assert t1 == repo_exec.project_image_tag(5, "FROM alpine\n")       # deterministic
    assert t1 != repo_exec.project_image_tag(6, "FROM alpine\n")       # keyed by project id
    assert t1 != repo_exec.project_image_tag(5, "FROM alpine\nRUN x")  # keyed by content
    assert t1.startswith("jeanmichel-sandbox:project-5-")


def test_build_image_ok_and_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(repo_exec.subprocess, "run", lambda cmd, **kw: _FakeProc(rc=0))
    ok, err = repo_exec.build_image("FROM alpine\n", tmp_path, "t:1")
    assert ok and err == ""
    monkeypatch.setattr(repo_exec.subprocess, "run", lambda cmd, **kw: _FakeProc(rc=1, err="boom\n"))
    ok, err = repo_exec.build_image("FROM alpine\n", tmp_path, "t:1")
    assert not ok and "boom" in err


@requires_git
def test_resolve_image_empty_returns_repo_default(wt):
    assert repo_exec._resolve_image(wt, 7, "") == _REPO_DEFAULT
    assert repo_exec._resolve_image(wt, 7, "   ") == _REPO_DEFAULT


@requires_git
def test_resolve_image_builds_project_image(wt, monkeypatch):
    monkeypatch.setattr(repo_exec, "_image_exists", lambda tag: False)
    built = {}
    monkeypatch.setattr(repo_exec, "build_image",
                        lambda content, ctx, tag: built.update(tag=tag, content=content) or (True, ""))
    tag = repo_exec._resolve_image(wt, 7, "FROM alpine\n")
    assert tag == repo_exec.project_image_tag(7, "FROM alpine\n")
    assert built["tag"] == tag


@requires_git
def test_resolve_image_existing_skips_build(wt, monkeypatch):
    monkeypatch.setattr(repo_exec, "_image_exists", lambda tag: True)
    def _boom(*a):
        raise AssertionError("should not build when the image already exists")
    monkeypatch.setattr(repo_exec, "build_image", _boom)
    assert repo_exec._resolve_image(wt, 3, "FROM alpine\n") == repo_exec.project_image_tag(3, "FROM alpine\n")


@requires_git
def test_resolve_image_build_failure_falls_back(wt, monkeypatch):
    monkeypatch.setattr(repo_exec, "_image_exists", lambda tag: False)
    monkeypatch.setattr(repo_exec, "build_image", lambda content, ctx, tag: (False, "boom"))
    assert repo_exec._resolve_image(wt, 1, "FROM alpine\n") == _REPO_DEFAULT


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
