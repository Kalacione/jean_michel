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

import hashlib
import logging
import os
import shutil
import subprocess
from pathlib import Path

from . import config

_log = logging.getLogger(__name__)

_GIT_TIMEOUT_S = 60
_CLONE_TIMEOUT_S = 300
_BRANCH_PREFIX = "jm/conv-"
_WORKTREE_DIRNAME = "worktree"
_CLONE_CACHE_DIRNAME = "repos-cache"  # under REPO_ROOT ; gitignored


def _clone_cache_dir() -> Path:
    return Path(config.REPO_ROOT) / _CLONE_CACHE_DIRNAME


def _ensure_clone_cached(url: str) -> Path | None:
    """Clone an ssh/remote `url` ONCE into ``repos-cache/<hash>/repo`` and return
    the local clone path (idempotent ; lock-free via atomic rename). ``None`` on
    failure. The clone is shared across conversations/projects pointing at the
    same url, and survives conversation deletion."""
    if not _git_available():
        return None
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    dest = _clone_cache_dir() / key / "repo"
    if (dest / ".git").exists():
        return dest
    tmp = dest.parent / f".tmp-clone-{os.getpid()}"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)
        subprocess.run(
            ["git", "clone", "--quiet", url, str(tmp)],
            check=True, capture_output=True, text=True, timeout=_CLONE_TIMEOUT_S,
        )
        try:
            os.rename(tmp, dest)  # atomic ; if a concurrent clone won, this raises
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)
        return dest if (dest / ".git").exists() else None
    except (subprocess.SubprocessError, OSError) as exc:
        _log.warning("worktree clone failed for %s: %s", url, exc)
        shutil.rmtree(tmp, ignore_errors=True)
        return None


def _worktree_source_repo(wt: Path) -> Path | None:
    """The repo a worktree belongs to (so remove/branch ops hit the right repo)."""
    try:
        r = subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return Path(r.stdout.strip()).parent  # <repo>/.git → <repo>
    except (subprocess.SubprocessError, OSError):
        return None


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


def create_worktree(
    conv_folder: Path, conv_id: str, source: str | None = None, kind: str = "local",
) -> Path | None:
    """Add a git worktree on branch ``jm/conv-<id>`` from the target repo.

    ``source``/``kind`` select the repo: a LOCAL path, an SSH/remote url (cloned
    once into the cache), or — when ``source`` is empty/None — the global
    ``config.PROJECT_ROOT`` (dogfood fallback). Returns the worktree path, else
    ``None`` (no-op). Idempotent on the worktree dir ; cleans a half-made dir.
    """
    if not _enabled() or not _git_available():
        return None
    if source and kind == "ssh":
        repo = _ensure_clone_cached(source)
        if repo is None:
            return None
    elif source:
        repo = Path(source)
    else:
        repo = _project_root()  # fallback (dogfood)
    if not _is_git_repo(repo):
        _log.debug("worktree: source %s is not a git repo — skipping", repo)
        return None
    wt = worktree_path_for(conv_folder)
    if wt.exists():
        return wt
    branch = branch_name(conv_id)
    try:
        wt.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Create the branch at current HEAD and check it out into the worktree.
            _git(repo, "worktree", "add", "-b", branch, str(wt), "HEAD")
        except subprocess.CalledProcessError:
            # Branch already exists (e.g. a resume) → attach the worktree to it.
            _git(repo, "worktree", "add", str(wt), branch)
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
    source = _worktree_source_repo(wt) or _project_root()
    removed = False
    try:
        _git(source, "worktree", "remove", "--force", str(wt))
        removed = True
    except (subprocess.SubprocessError, OSError) as exc:
        _log.debug("worktree remove failed for %s: %s", conv_folder, exc)
        shutil.rmtree(wt, ignore_errors=True)
    # Prune stale registrations + delete the branch (best-effort).
    try:
        _git(source, "worktree", "prune")
        _git(source, "branch", "-D", branch_name(conv_id))
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
