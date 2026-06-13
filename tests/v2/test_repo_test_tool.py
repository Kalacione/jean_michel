"""Tests for repo_test (structured test runner)."""

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
from jeanmichel.tools import build_registry, repo_exec, repo_test  # noqa: E402

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")

_PASS_TEST = "def test_ok():\n    assert 1 + 1 == 2\n"
_FAIL_TEST = "def test_bad():\n    assert 1 + 1 == 3\n"


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    def run(*a):
        subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)
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


# ---- project container branch (B7) ------------------------------------------


@requires_git
def test_repo_test_runs_in_project_container(wt, monkeypatch):
    """Custom project image → tests run via `docker exec` in the shared sandbox,
    NOT on the host."""
    conv, _ = wt
    calls = {}

    def fake_resolve(_conv, _pid, _df):
        return "jeanmichel-sandbox:project-7-deadbeef"  # ≠ repo-default

    def fake_running(_name):
        return True  # pretend the container is already up

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="1 passed in 0.01s\n", stderr="")

    monkeypatch.setattr(repo_exec, "_resolve_image", fake_resolve)
    monkeypatch.setattr(repo_exec, "_container_running", fake_running)
    monkeypatch.setattr(repo_test.subprocess, "run", fake_run)

    out = json.loads(
        repo_test.make_spec(conv, conv_id="c1", project_id=7, dockerfile="FROM x").handler()
    )
    assert out["passed"] is True
    assert out["counts"].get("passed") == 1
    # Went through docker exec in the project's container, not the host.
    assert calls["cmd"][:3] == ["docker", "exec", repo_exec._container_name("c1")]
    assert calls["cmd"][3:5] == ["bash", "-lc"]


@requires_git
def test_repo_test_default_image_stays_on_host(wt, monkeypatch):
    """repo-default image (no project Dockerfile) → host path, no docker exec."""
    conv, _ = wt
    seen = {}

    real_run = subprocess.run

    def spy_run(cmd, **kwargs):
        seen.setdefault("first", cmd)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(
        repo_exec, "_resolve_image", lambda *a: repo_exec._REPO_DEFAULT_IMAGE
    )
    monkeypatch.setattr(repo_test.subprocess, "run", spy_run)

    out = json.loads(
        repo_test.make_spec(conv, conv_id="c1", project_id=7, dockerfile="").handler()
    )
    assert out["passed"] is True
    assert seen["first"][0] != "docker"  # host interpreter, not a container


# ---- registry ---------------------------------------------------------------


@requires_git
def test_registry_includes_repo_test(wt):
    reg = build_registry(wt[0])
    assert "repo_test" in reg
