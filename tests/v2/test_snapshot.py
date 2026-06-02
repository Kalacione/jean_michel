"""Unit tests for the per-conversation git snapshot module (snapshot.py).

Real git is required for the snapshot behaviour ; those tests skip if ``git``
is absent. The guard tests (disabled flag / git absent) always run — they
assert the module no-ops without raising, which is the safety contract.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from jeanmichel import config, snapshot  # noqa: E402

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


@pytest.fixture()
def enabled(monkeypatch):
    """Turn snapshots ON for this test (conftest pins them off by default)."""
    monkeypatch.setattr(config, "CONVERSATION_SNAPSHOT_ENABLED", True)


def _write(folder: Path, rel: str, text: str) -> None:
    p = folder / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@requires_git
def test_init_repo_creates_branch_and_initial_commit(conv_folder, enabled):
    snapshot.init_repo(conv_folder, "deadbeef")
    assert (conv_folder / ".git").is_dir()
    assert (conv_folder / ".gitignore").exists()
    snaps = snapshot.list_snapshots(conv_folder)
    assert len(snaps) == 1
    assert snaps[0]["subject"] == "init"
    # Branch name == conv_id.
    assert "deadbeef" in (conv_folder / ".git" / "HEAD").read_text(encoding="utf-8")


@requires_git
def test_commit_turn_then_skip_empty(conv_folder, enabled):
    snapshot.init_repo(conv_folder, "c1")
    _write(conv_folder, "messages.json", '{"t":1}')
    snapshot.commit_turn(conv_folder, "turn: one")
    assert len(snapshot.list_snapshots(conv_folder)) == 2
    # ALEXA-style turn : nothing changed on disk → no new commit.
    snapshot.commit_turn(conv_folder, "turn: alexa")
    assert len(snapshot.list_snapshots(conv_folder)) == 2


@requires_git
def test_list_snapshots_ordered_oldest_first(conv_folder, enabled):
    snapshot.init_repo(conv_folder, "c1")
    _write(conv_folder, "a.txt", "1")
    snapshot.commit_turn(conv_folder, "turn: a")
    _write(conv_folder, "b.txt", "2")
    snapshot.commit_turn(conv_folder, "turn: b")
    assert [s["subject"] for s in snapshot.list_snapshots(conv_folder)] == [
        "init", "turn: a", "turn: b",
    ]


@requires_git
def test_fork_at_materializes_tree_without_git(conv_folder, enabled, tmp_path):
    snapshot.init_repo(conv_folder, "src")
    _write(conv_folder, "workspace/tri.py", "x = 1\n")
    snapshot.commit_turn(conv_folder, "turn: one")
    _write(conv_folder, "workspace/late.py", "y = 2\n")
    snapshot.commit_turn(conv_folder, "turn: two")
    turn_one = snapshot.list_snapshots(conv_folder)[1]["commit"]

    dst = tmp_path / "fork"
    assert snapshot.fork_at(conv_folder, dst, turn_one, "forked") is True
    assert (dst / "workspace" / "tri.py").exists()
    # late.py belongs to a later turn → absent from the forked tree.
    assert not (dst / "workspace" / "late.py").exists()
    # The fork has its OWN fresh repo (single init commit), no carried history.
    assert (dst / ".git").is_dir()
    assert [s["subject"] for s in snapshot.list_snapshots(dst)] == ["init"]


@requires_git
def test_revert_rewinds_and_cleans(conv_folder, enabled):
    snapshot.init_repo(conv_folder, "c1")
    _write(conv_folder, "keep.txt", "keep")
    snapshot.commit_turn(conv_folder, "turn: one")
    target = snapshot.list_snapshots(conv_folder)[1]["commit"]
    _write(conv_folder, "later.txt", "later")
    snapshot.commit_turn(conv_folder, "turn: two")
    _write(conv_folder, "orphan.txt", "orphan")  # untracked orphan

    assert snapshot.revert_to(conv_folder, target) is True
    assert (conv_folder / "keep.txt").exists()
    assert not (conv_folder / "later.txt").exists()   # later turn discarded
    assert not (conv_folder / "orphan.txt").exists()  # git clean -fd


# ---- guard tests : the safety contract (no-op, never raise) --------------


def test_disabled_flag_is_noop(conv_folder):
    # conftest pins the flag OFF ; every entry point must no-op without raising.
    snapshot.init_repo(conv_folder, "x")
    snapshot.commit_turn(conv_folder, "turn")
    assert not (conv_folder / ".git").exists()
    assert snapshot.list_snapshots(conv_folder) == []
    assert snapshot.revert_to(conv_folder, "HEAD") is False
    assert snapshot.fork_at(conv_folder, conv_folder / "d", "HEAD", "y") is False


def test_git_absent_is_noop(conv_folder, enabled, monkeypatch):
    monkeypatch.setattr(snapshot, "_git_available", lambda: False)
    snapshot.init_repo(conv_folder, "x")
    snapshot.commit_turn(conv_folder, "turn")
    assert not (conv_folder / ".git").exists()
    assert snapshot.list_snapshots(conv_folder) == []
    assert snapshot.revert_to(conv_folder, "HEAD") is False
