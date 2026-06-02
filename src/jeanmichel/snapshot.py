"""Per-conversation git snapshots — rewind a turn / fork from a point.

Each conversation folder is an independent local git repo (never pushed). A
commit is made at the end of every turn (a turn snapshot), enabling:

- "revert to this point" : ``git reset --hard <commit>`` + ``git clean -fd``
- "fork from this point" : ``git archive <commit>`` extracted into a fresh
  conversation folder (no ``.git`` carried over).

Opt-in via ``config.CONVERSATION_SNAPSHOT_ENABLED`` (off by default).

Everything here is BEST-EFFORT : if the flag is off, ``git`` is absent, or any
git command fails, the public functions no-op (or return empty) and NEVER
raise — a snapshot failure must never break a turn.

The conversation folders are gitignored at the project root (``conversations/*``),
so these nested repos never pollute the main project repo.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from . import config

_log = logging.getLogger(__name__)

_GIT_TIMEOUT_S = 30
_UNIT = "\x1f"  # ASCII unit separator — safe delimiter for git log --format

# Commit identity, set LOCALLY per repo so commits work without a global git
# config on the host.
_COMMIT_NAME = "Jean-Michel"
_COMMIT_EMAIL = "jeanmichel@localhost"

# Per-repo ignore : regenerable image thumbnails + atomic-write temp files.
_REPO_GITIGNORE = "workspace/.thumbs/\n.*.tmp\n"


def _enabled() -> bool:
    # Read through the module (not an import-binding) so tests can flip it.
    return config.CONVERSATION_SNAPSHOT_ENABLED


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


def _is_repo(folder: Path) -> bool:
    return (folder / ".git").is_dir()


def init_repo(conv_folder: Path, conv_id: str) -> None:
    """Init a conversation repo (branch = conv_id) with one initial commit."""
    if not _enabled() or not _git_available():
        return
    folder = Path(conv_folder)
    if _is_repo(folder):
        return
    try:
        _git(folder, "init", "-b", conv_id)
        _git(folder, "config", "user.name", _COMMIT_NAME)
        _git(folder, "config", "user.email", _COMMIT_EMAIL)
        (folder / ".gitignore").write_text(_REPO_GITIGNORE, encoding="utf-8")
        _git(folder, "add", "-A")
        _git(folder, "commit", "--allow-empty", "-m", "init")
    except (subprocess.SubprocessError, OSError) as exc:
        _log.debug("snapshot init_repo failed for %s: %s", folder, exc)


def commit_turn(conv_folder: Path, label: str) -> None:
    """Commit the conversation state at end of turn. No-op if nothing changed."""
    if not _enabled() or not _git_available():
        return
    folder = Path(conv_folder)
    try:
        if not _is_repo(folder):
            # Conversation created before the flag was enabled → lazy init so
            # the flag is retroactive. Branch name falls back to the folder name.
            init_repo(folder, folder.name)
            if not _is_repo(folder):
                return
        _git(folder, "add", "-A")
        # Skip empty commits (e.g. ALEXA turns that write nothing to disk).
        staged = subprocess.run(
            ["git", "-C", str(folder), "diff", "--cached", "--quiet"],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
        )
        if staged.returncode == 0:
            return  # nothing staged
        _git(folder, "commit", "-m", label or "turn")
    except (subprocess.SubprocessError, OSError) as exc:
        _log.debug("snapshot commit_turn failed for %s: %s", folder, exc)


def list_snapshots(conv_folder: Path) -> list[dict]:
    """Commits oldest→newest as ``[{commit, subject, date}]``. ``[]`` on failure."""
    if not _enabled() or not _git_available():
        return []
    folder = Path(conv_folder)
    if not _is_repo(folder):
        return []
    try:
        result = _git(folder, "log", "--reverse", f"--format=%H{_UNIT}%s{_UNIT}%cI")
    except (subprocess.SubprocessError, OSError) as exc:
        _log.debug("snapshot list_snapshots failed for %s: %s", folder, exc)
        return []
    out: list[dict] = []
    for line in result.stdout.splitlines():
        parts = line.split(_UNIT)
        if len(parts) != 3:
            continue
        commit, subject, date = parts
        out.append({"commit": commit, "subject": subject, "date": date})
    return out


def revert_to(conv_folder: Path, commit: str) -> bool:
    """Rewind the conversation to ``commit`` (destructive). Returns success.

    Discards later commits AND untracked orphans (``git clean -fd``). The old
    HEAD stays in ``git reflog`` (recoverable).
    """
    if not _enabled() or not _git_available():
        return False
    folder = Path(conv_folder)
    if not _is_repo(folder):
        return False
    try:
        _git(folder, "reset", "--hard", commit)
        _git(folder, "clean", "-fd")
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        _log.warning("snapshot revert_to failed for %s @ %s: %s", folder, commit, exc)
        return False


def fork_at(src_folder: Path, dst_folder: Path, commit: str, new_id: str) -> bool:
    """Materialize the tree at ``commit`` from src into dst (no ``.git``), then
    init dst as its own repo. Returns success."""
    if not _enabled() or not _git_available():
        return False
    src = Path(src_folder)
    dst = Path(dst_folder)
    if not _is_repo(src):
        return False
    tar_path: str | None = None
    try:
        dst.mkdir(parents=True, exist_ok=True)
        fd, tar_path = tempfile.mkstemp(suffix=".tar")
        os.close(fd)
        # git archive yields the tracked tree AT that commit (excludes .git and
        # ignored files like workspace/.thumbs/).
        _git(src, "archive", "--format=tar", "-o", tar_path, commit)
        with tarfile.open(tar_path) as tf:
            tf.extractall(dst, filter="data")  # filter='data' = safe extraction
        init_repo(dst, new_id)
        return True
    except (subprocess.SubprocessError, OSError, tarfile.TarError) as exc:
        _log.warning("snapshot fork_at failed for %s @ %s: %s", src, commit, exc)
        return False
    finally:
        if tar_path:
            Path(tar_path).unlink(missing_ok=True)
