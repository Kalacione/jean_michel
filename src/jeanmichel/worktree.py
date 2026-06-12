"""`code`-mode git worktrees — an isolated checkout of the target project repo.

In `code` interaction mode the system intervenes on a REAL codebase
(``config.PROJECT_ROOT``) by editing files in place. To keep the live working
tree untouched, each such conversation gets its own **git worktree** on a
dedicated branch (``jm/conv-<id>``): a second checkout that shares
PROJECT_ROOT's ``.git`` but has independent working files. git is the safety net
— the branch holds every edit, the live tree never changes.

Distinct from ``snapshot.py`` (which turns each *conversation folder* into its
own repo to snapshot workspace/state). Here we add a worktree OF the target repo.

Opt-in via ``config.CODE_WORKTREE_ENABLED`` (off by default). BEST-EFFORT: if the
flag is off, git is absent, PROJECT_ROOT is not a git repo, or any git command
fails, the public functions no-op (return ``None`` / ``False``) and NEVER raise —
a worktree failure must never break a turn (the system falls back to the scratch
workspace).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from . import config

_log = logging.getLogger(__name__)

_GIT_TIMEOUT_S = 60
_BRANCH_PREFIX = "jm/conv-"
_WORKTREE_DIRNAME = "worktree"


def _enabled() -> bool:
    # Read through the module (not an import-binding) so tests can flip it.
    return config.CODE_WORKTREE_ENABLED


def _git_available() -> bool:
    return shutil.which("git") is not None


def _git(folder: Path, *args: str, timeout: int = _GIT_TIMEOUT_S) -> subprocess.CompletedProcess:
    """Run a git command in ``folder``. Raises CalledProcessError on failure."""
    return subprocess.run(
        ["git", "-C", str(folder), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _project_root() -> Path:
    # Read through the module so tests can redirect config.PROJECT_ROOT.
    return Path(config.PROJECT_ROOT)


def _is_git_repo(folder: Path) -> bool:
    try:
        r = subprocess.run(
            ["git", "-C", str(folder), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
        )
        return r.returncode == 0 and r.stdout.strip() == "true"
    except (subprocess.SubprocessError, OSError):
        return False


def branch_name(conv_id: str) -> str:
    return f"{_BRANCH_PREFIX}{conv_id}"


def worktree_path_for(conv_folder: Path) -> Path:
    """Where the worktree lives for a conversation (``conv_folder/worktree``)."""
    return Path(conv_folder) / _WORKTREE_DIRNAME


def create_worktree(conv_folder: Path, conv_id: str) -> Path | None:
    """Add a git worktree of PROJECT_ROOT on branch ``jm/conv-<id>``.

    Returns the worktree path on success, else ``None`` (no-op). Idempotent: if
    the worktree dir already exists, returns it. On a fresh creation failure the
    half-made dir is cleaned so we never leave junk behind.
    """
    if not _enabled() or not _git_available():
        return None
    root = _project_root()
    if not _is_git_repo(root):
        _log.debug("worktree: PROJECT_ROOT %s is not a git repo — skipping", root)
        return None
    wt = worktree_path_for(conv_folder)
    if wt.exists():
        return wt
    branch = branch_name(conv_id)
    try:
        wt.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Create the branch at current HEAD and check it out into the worktree.
            _git(root, "worktree", "add", "-b", branch, str(wt), "HEAD")
        except subprocess.CalledProcessError:
            # Branch already exists (e.g. a resume) → attach the worktree to it.
            _git(root, "worktree", "add", str(wt), branch)
        return wt
    except (subprocess.SubprocessError, OSError) as exc:
        _log.warning("worktree create failed for %s: %s", conv_folder, exc)
        shutil.rmtree(wt, ignore_errors=True)
        return None


def remove_worktree(conv_folder: Path, conv_id: str) -> bool:
    """Remove the worktree dir and delete its branch. Best-effort.

    No-op (returns ``False``) when there is no worktree dir for this conversation
    — so deleting a non-code conversation never touches PROJECT_ROOT.
    """
    if not _git_available():
        return False
    wt = worktree_path_for(conv_folder)
    if not wt.exists():
        return False
    root = _project_root()
    removed = False
    try:
        _git(root, "worktree", "remove", "--force", str(wt))
        removed = True
    except (subprocess.SubprocessError, OSError) as exc:
        _log.debug("worktree remove failed for %s: %s", conv_folder, exc)
        shutil.rmtree(wt, ignore_errors=True)
    # Prune stale registrations + delete the branch (best-effort).
    try:
        _git(root, "worktree", "prune")
        _git(root, "branch", "-D", branch_name(conv_id))
    except (subprocess.SubprocessError, OSError):
        pass
    return removed


def is_protected_path(relative_path: str) -> bool:
    """True if a worktree-relative path is off-limits to edits.

    Matches against ``config.REPO_PROTECTED_PATHS``: an entry ending in ``/`` is a
    directory prefix (the dir itself and anything under it), otherwise an exact
    file match. Used by the repo edit tools / PreToolUse gate (P1).
    """
    norm = relative_path.strip().replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    norm = norm.lstrip("/")
    for p in config.REPO_PROTECTED_PATHS:
        if p.endswith("/"):
            base = p.rstrip("/")
            if norm == base or norm.startswith(p):
                return True
        elif norm == p:
            return True
    return False
