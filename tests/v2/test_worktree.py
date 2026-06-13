"""Unit + integration tests for `code`-mode git worktrees (worktree.py).

Real git is required for the worktree behaviour; those tests skip if ``git`` is
absent. The guard tests (disabled flag / git absent / non-repo target) always
run — they assert the module no-ops without raising, which is the safety
contract (a worktree failure must never break a turn).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from jeanmichel import config, worktree  # noqa: E402

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _init_repo(path: Path) -> None:
    """Init a minimal git repo with one tracked file + an initial commit."""
    path.mkdir(parents=True, exist_ok=True)
    def run(*a):
        subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)
    run("init", "-b", "main")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (path / "sample.py").write_text("X = 1\n", encoding="utf-8")
    run("add", "-A")
    run("commit", "-m", "init")


def _porcelain(path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain"],
        capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture()
def project_repo(tmp_path, monkeypatch) -> Path:
    """A temp git repo wired as config.PROJECT_ROOT, with worktrees enabled."""
    repo = tmp_path / "proj"
    _init_repo(repo)
    monkeypatch.setattr(config, "PROJECT_ROOT", repo)
    monkeypatch.setattr(config, "CODE_WORKTREE_ENABLED", True)
    return repo


# ---- worktree module --------------------------------------------------------


@requires_git
def test_create_worktree_isolated_branch_live_tree_untouched(project_repo, conv_folder):
    wt = worktree.create_worktree(conv_folder, "deadbeef")
    assert wt == worktree.worktree_path_for(conv_folder)
    # The worktree holds the tracked tree at HEAD.
    assert (wt / "sample.py").read_text(encoding="utf-8") == "X = 1\n"
    # It is checked out on the dedicated branch.
    head = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert head == "jm/conv-deadbeef"
    # The live tree stays clean and on its original branch.
    assert _porcelain(project_repo) == ""
    live_head = subprocess.run(
        ["git", "-C", str(project_repo), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert live_head == "main"


@requires_git
def test_create_worktree_idempotent(project_repo, conv_folder):
    first = worktree.create_worktree(conv_folder, "c1")
    again = worktree.create_worktree(conv_folder, "c1")
    assert first == again
    assert again is not None and again.exists()


@requires_git
def test_remove_worktree_drops_dir_and_branch(project_repo, conv_folder):
    worktree.create_worktree(conv_folder, "c1")
    assert worktree.remove_worktree(conv_folder, "c1") is True
    assert not worktree.worktree_path_for(conv_folder).exists()
    branches = subprocess.run(
        ["git", "-C", str(project_repo), "branch", "--list", "jm/conv-c1"],
        capture_output=True, text=True,
    ).stdout
    assert "jm/conv-c1" not in branches


@requires_git
def test_remove_worktree_noop_without_dir(project_repo, conv_folder):
    # No worktree was created → removal is a no-op, never touches PROJECT_ROOT.
    assert worktree.remove_worktree(conv_folder, "never") is False


# ---- guard tests : safety contract (no-op, never raise) ---------------------


def test_disabled_flag_is_noop(conv_folder, monkeypatch):
    monkeypatch.setattr(config, "CODE_WORKTREE_ENABLED", False)
    assert worktree.create_worktree(conv_folder, "x") is None
    assert not worktree.worktree_path_for(conv_folder).exists()


@requires_git
def test_non_repo_target_is_noop(tmp_path, conv_folder, monkeypatch):
    monkeypatch.setattr(config, "CODE_WORKTREE_ENABLED", True)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path / "not_a_repo")
    (tmp_path / "not_a_repo").mkdir()
    assert worktree.create_worktree(conv_folder, "x") is None


def test_git_absent_is_noop(project_repo, conv_folder, monkeypatch):
    monkeypatch.setattr(worktree, "_git_available", lambda: False)
    assert worktree.create_worktree(conv_folder, "x") is None
    assert not worktree.worktree_path_for(conv_folder).exists()


# ---- protected paths (exclusions enforced by P1 gate) -----------------------


@pytest.mark.parametrize("rel", [
    "jeanmichel.db", ".env", ".api_secret",
    "conversations/2026/x.json", "backups/dump.sql",
    "voice_models/default.onnx", ".venv/lib/site.py",
    ".git/config",
    "./jeanmichel.db", "conversations",
])
def test_is_protected_path_blocks(rel):
    assert worktree.is_protected_path(rel) is True


@pytest.mark.parametrize("rel", [
    "src/jeanmichel/orchestrator_v2.py", "README.md",
    "tests/v2/test_worktree.py", "db/schema.sql",
    "envoy.py",  # not ".env"
])
def test_is_protected_path_allows_source(rel):
    assert worktree.is_protected_path(rel) is False


# ---- integration : conversation lifecycle wiring ----------------------------


@requires_git
def test_create_conversation_code_mode_gets_worktree(tmp_db_v2, project_repo, monkeypatch):
    # tmp_db_v2 redirects REPO_ROOT/CONVERSATIONS_DIR/DB; project_repo is a
    # SEPARATE git repo so the worktree never nests inside conversations/.
    from jeanmichel.service import conversation

    conv_id, folder = conversation.create_conversation("code")
    wt = worktree.worktree_path_for(folder)
    assert wt.exists() and (wt / "sample.py").exists()
    assert _porcelain(project_repo) == ""  # live tree untouched

    conversation.delete_conversation(conv_id)
    assert not folder.exists()
    branches = subprocess.run(
        ["git", "-C", str(project_repo), "branch", "--list", f"jm/conv-{conv_id}"],
        capture_output=True, text=True,
    ).stdout
    assert f"jm/conv-{conv_id}" not in branches


@requires_git
def test_create_conversation_non_code_no_worktree(tmp_db_v2, project_repo):
    from jeanmichel.service import conversation

    _, folder = conversation.create_conversation("analyse")
    assert not worktree.worktree_path_for(folder).exists()


# ---- R2 : explicit source (local / ssh clone) + project wiring -------------


@requires_git
def test_create_worktree_from_explicit_local_source(project_repo, tmp_path, conv_folder):
    # A SECOND repo, distinct from PROJECT_ROOT, passed explicitly.
    other = tmp_path / "other"
    _init_repo(other)
    (other / "OTHER.md").write_text("other repo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(other), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(other), "commit", "-m", "marker"], check=True, capture_output=True)
    wt = worktree.create_worktree(conv_folder, "c2", source=str(other), kind="local")
    assert wt is not None and (wt / "OTHER.md").exists()  # from `other`, not PROJECT_ROOT
    # Removal derives the source repo from the worktree itself (not PROJECT_ROOT).
    assert worktree.remove_worktree(conv_folder, "c2") is True
    assert not wt.exists()


@requires_git
def test_create_worktree_ssh_clones_into_cache(project_repo, tmp_path, conv_folder, monkeypatch):
    # repos-cache lands under REPO_ROOT → redirect to tmp. A local path acts as
    # the clonable "remote" (git clone works on local paths — no network needed).
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    remote = tmp_path / "remote"
    _init_repo(remote)
    wt = worktree.create_worktree(conv_folder, "c3", source=str(remote), kind="ssh")
    assert wt is not None and (wt / "sample.py").exists()
    cache = tmp_path / "repos-cache"
    assert cache.exists() and any(cache.iterdir())  # the clone was cached


@requires_git
def test_create_conversation_uses_project_repo(tmp_db_v2, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CODE_WORKTREE_ENABLED", True)
    from jeanmichel.db import cli_user_id, connect
    from jeanmichel.service import conversation as conv_svc
    from jeanmichel.service import project as project_svc

    repo = tmp_path / "proj_repo"
    _init_repo(repo)
    with connect() as conn:
        proj = project_svc.create(
            conn, user_id=cli_user_id(conn), code="p", name="P",
            code_repo=str(repo), repo_kind="local",
        )
    _, folder = conv_svc.create_conversation("code", project_id=proj["id"])
    wt = worktree.worktree_path_for(folder)
    assert wt.exists() and (wt / "sample.py").exists()  # worktree from the project's repo


@requires_git
def test_source_repo_derives_from_worktree(project_repo, tmp_path, conv_folder):
    other = tmp_path / "other2"
    _init_repo(other)
    worktree.create_worktree(conv_folder, "cs", source=str(other), kind="local")
    src = worktree.source_repo(conv_folder)
    assert src is not None and src.resolve() == other.resolve()  # the real source, not PROJECT_ROOT


@requires_git
def test_source_repo_uses_explicit_global_without_worktree(project_repo, conv_folder):
    # No worktree, but an EXPLICIT PROJECT_ROOT (project_repo) is set → returns it
    # (the CLI-global case). Without an explicit global it would be None.
    assert worktree.source_repo(conv_folder).resolve() == project_repo.resolve()


def test_no_repo_no_worktree(conv_folder, monkeypatch):
    # Security: code worktrees ON, but NO attached repo and NO explicit
    # PROJECT_ROOT → NO worktree. No silent fallback to the jean-michel repo.
    monkeypatch.setattr(config, "CODE_WORKTREE_ENABLED", True)
    monkeypatch.setattr(config, "PROJECT_ROOT", None)
    assert worktree.create_worktree(conv_folder, "x", source=None, kind="local") is None
    assert not worktree.worktree_path_for(conv_folder).exists()


def test_router_repo_notice_when_worktree_exists(conv_folder):
    # The router must be TOLD a repo is attached (so it delegates instead of
    # claiming no access). No worktree → no notice ; worktree → one, idempotent.
    from jeanmichel.hooks import _REPO_RECAP_MARKER, _refresh_repo_recap
    msgs = [{"role": "system", "content": "x"}, {"role": "user", "content": "hi"}]
    _refresh_repo_recap(msgs, conv_folder)
    assert not any(_REPO_RECAP_MARKER in (m.get("content") or "") for m in msgs)
    worktree.worktree_path_for(conv_folder).mkdir(parents=True)
    _refresh_repo_recap(msgs, conv_folder)
    _refresh_repo_recap(msgs, conv_folder)  # idempotent
    notices = [m for m in msgs if _REPO_RECAP_MARKER in (m.get("content") or "")]
    assert len(notices) == 1
    assert "code-runner" in notices[0]["content"]


def test_router_stocktake_nudge(conv_folder):
    """F4 router discipline: after a specialist returns (reeval_pending), a STOCKTAKE
    nudge fires for BOTH routers (chat + code) telling the router to analyse what was
    produced before re-delegating, with an ask_human escalation exit. Code mode folds
    in TODO discipline. Gated on reeval_pending ; idempotent."""
    from jeanmichel import todo as todo_mod
    from jeanmichel.hooks import _PLAN_NUDGE_MARKER, _refresh_plan_nudge
    from jeanmichel.models import ConversationState

    state = ConversationState()

    def delegations(n):
        return [{"role": "tool", "tool_name": "delegate_to", "content": "{}"} for _ in range(n)]

    def nudges(ms):
        return [m for m in ms if (m.get("content") or "").startswith(_PLAN_NUDGE_MARKER)]

    # No specialist pending (reeval_pending False) → never nudges, any mode/count.
    msgs = [{"role": "user", "content": "go"}, *delegations(3)]
    _refresh_plan_nudge(msgs, conv_folder, state)
    assert nudges(msgs) == []

    # Chat router (no worktree), a specialist just returned → stock-take nudge fires
    # (both routers), with the ask_human escalation ; idempotent ; no TODO wording.
    state.reeval_pending = True
    msgs = [*delegations(1)]
    _refresh_plan_nudge(msgs, conv_folder, state)
    _refresh_plan_nudge(msgs, conv_folder, state)
    assert len(nudges(msgs)) == 1
    content = nudges(msgs)[0]["content"]
    assert "ask_human" in content and "workspace_view" in content
    assert "todo_write" not in content  # chat mode: no plan discipline

    worktree.worktree_path_for(conv_folder).mkdir(parents=True)  # → code mode

    # Code mode, no plan yet, ≥2 delegations → stock-take folds in "decompose" todo.
    msgs = [*delegations(2)]
    _refresh_plan_nudge(msgs, conv_folder, state)
    assert len(nudges(msgs)) == 1
    assert "todo_write" in nudges(msgs)[0]["content"]

    # Code mode, a plan exists → stock-take folds in "update" todo wording.
    items, _ = todo_mod.normalize_items([{"text": "step 1", "status": "in_progress"}])
    todo_mod.save_todo(conv_folder, "do the thing", items)
    msgs = [*delegations(2)]
    _refresh_plan_nudge(msgs, conv_folder, state)
    assert len(nudges(msgs)) == 1
    assert "Update your todo_write" in nudges(msgs)[0]["content"]

    # Router acted (todo_write cleared reeval_pending) → no nudge.
    state.reeval_pending = False
    msgs = [*delegations(2)]
    _refresh_plan_nudge(msgs, conv_folder, state)
    assert nudges(msgs) == []
