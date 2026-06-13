"""`code`-mode checkouts — an isolated CLONE of the target project repo.

In `code` interaction mode the system intervenes on a REAL codebase
(``config.PROJECT_ROOT``) by editing files in place. To keep the live working
tree untouched, each such conversation gets its own **standalone clone**
(``git clone --local`` → hardlinked objects, cheap) on a dedicated branch
(``jm/conv-<id>``). git is the safety net — the branch holds every edit, the
live tree never changes. A clone (rather than a linked git worktree) has a
self-contained ``.git`` directory, so the checkout works as a normal repo when
mounted into the project sandbox container (``git`` runs there). The original
repo is recoverable via ``remote.origin.url`` (see ``source_repo``).

Distinct from ``snapshot.py`` (which turns each *conversation folder* into its
own repo to snapshot workspace/state). Here we clone the target repo.

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
    """The ORIGINAL repo a checkout came from.

    For a standalone clone (the current model) that's ``remote.origin.url`` —
    the original path, where ``.venv`` and the graphify graph live. Falls back to
    the git-common-dir parent for a legacy LINKED worktree.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(wt), "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
        )
        if r.returncode == 0 and r.stdout.strip():
            p = Path(r.stdout.strip())
            if p.exists():
                return p
    except (subprocess.SubprocessError, OSError):
        pass
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


def _project_root() -> Path | None:
    # Explicit global target ONLY (CLI). None unless JEANMICHEL_PROJECT_ROOT is
    # set — no silent fallback to the jean-michel repo (security). Read through
    # the module so tests can redirect config.PROJECT_ROOT.
    pr = config.PROJECT_ROOT
    return Path(pr) if pr else None


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


def source_repo(conv_folder: Path) -> Path | None:
    """The canonical repo this conversation's worktree was cut from.

    Per-conversation (a local project = its code_repo ; an ssh project = its
    cached clone ; an explicit CLI global = JEANMICHEL_PROJECT_ROOT). The graph +
    the test interpreter belong to THIS repo (not the ephemeral worktree).
    ``None`` when there is no worktree and no explicit PROJECT_ROOT (no silent
    fallback to the jean-michel repo).
    """
    wt = worktree_path_for(conv_folder)
    if wt.exists():
        src = _worktree_source_repo(wt)
        if src is not None and _is_git_repo(src):
            return src
    root = _project_root()
    return root if (root is not None and _is_git_repo(root)) else None


def create_worktree(
    conv_folder: Path, conv_id: str, source: str | None = None, kind: str = "local",
) -> Path | None:
    """Create an isolated CLONE of the target repo on branch ``jm/conv-<id>``.

    A standalone clone (``git clone --local`` → hardlinked objects, cheap) rather
    than a linked git worktree: its ``.git`` is a real, self-contained directory,
    so the checkout behaves as a normal repo when mounted into the project sandbox
    container (``git`` works there — a linked worktree's ``.git`` is a pointer file
    to the source, which is not mounted). ``source``/``kind`` select the repo: a
    LOCAL path, an SSH/remote url (cloned once into the cache), or — when ``source``
    is empty/None — an EXPLICIT ``JEANMICHEL_PROJECT_ROOT`` (CLI global). No attached
    repo and no explicit global ⇒ ``None`` (no silent fallback to the jean-michel
    repo). Returns the clone path, else ``None``. Idempotent ; cleans a half-made dir.
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
        # No attached repo. No silent fallback to the jean-michel repo — an
        # explicit JEANMICHEL_PROJECT_ROOT (CLI global) is the only non-None case.
        repo = _project_root()
        if repo is None:
            return None  # no repo = no repo = no worktree
    repo = Path(repo).resolve()  # absolute → clone's remote.origin.url is resolvable
    if not _is_git_repo(repo):
        _log.debug("worktree: source %s is not a git repo — skipping", repo)
        return None
    wt = worktree_path_for(conv_folder)
    if wt.exists():
        return wt
    branch = branch_name(conv_id)
    try:
        wt.parent.mkdir(parents=True, exist_ok=True)
        # Standalone clone (hardlinked objects → cheap) → self-contained .git ;
        # origin = repo. Hardlinks need the same filesystem (the common case: the
        # checkout lives under conversations/, inside the repo) ; on a cross-device
        # target git --local fails, so fall back to copying objects.
        try:
            subprocess.run(
                ["git", "clone", "--local", "--quiet", str(repo), str(wt)],
                check=True, capture_output=True, text=True, timeout=_CLONE_TIMEOUT_S,
            )
        except subprocess.CalledProcessError:
            shutil.rmtree(wt, ignore_errors=True)
            subprocess.run(
                ["git", "clone", "--no-hardlinks", "--quiet", str(repo), str(wt)],
                check=True, capture_output=True, text=True, timeout=_CLONE_TIMEOUT_S,
            )
        # Dedicated branch for this conversation's work (resume → just switch).
        try:
            _git(wt, "checkout", "-q", "-b", branch)
        except subprocess.CalledProcessError:
            _git(wt, "checkout", "-q", branch)
        return wt
    except (subprocess.SubprocessError, OSError) as exc:
        _log.warning("worktree create failed for %s: %s", conv_folder, exc)
        shutil.rmtree(wt, ignore_errors=True)
        return None


def remove_worktree(conv_folder: Path, conv_id: str) -> bool:
    """Remove the conversation's checkout. Best-effort.

    No-op (returns ``False``) when there is no checkout for this conversation — so
    deleting a non-code conversation never touches PROJECT_ROOT. The clone is a
    standalone dir (its branch lives inside it), so removing the dir is enough; the
    prune/branch-D below only matter for LEGACY linked worktrees (no-op for clones).
    """
    if not _git_available():
        return False
    wt = worktree_path_for(conv_folder)
    if not wt.exists():
        return False
    source = _worktree_source_repo(wt) or _project_root()
    shutil.rmtree(wt, ignore_errors=True)
    if source is not None:
        try:
            _git(source, "worktree", "prune")
            _git(source, "branch", "-D", branch_name(conv_id))
        except (subprocess.SubprocessError, OSError):
            pass
    return not wt.exists()


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
