"""Shared primitives for the ``repo_*`` tools (P1) — code-mode intervention on
the REAL target repo via its per-conversation git worktree.

Distinct from ``_workspace.py`` (the scratch workspace). These tools operate on
the worktree root (a checkout of ``config.PROJECT_ROOT`` on branch
``jm/conv-<id>``), enforce the protected-path exclusions, and maintain a small
per-conversation **read ledger** so ``repo_edit`` / ``repo_write`` can guarantee
**read-before-edit + content freshness** — the file was read in this
conversation AND has not changed since (mtime). That freshness check is the
load-bearing safety for editing real files in place: an edit against a
file that moved under us fails loudly instead of corrupting silently.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import worktree

_READ_LEDGER = ".repo_reads.json"  # in the conv root — NOT the worktree, NOT the workspace


def worktree_root(conv_folder: Path) -> Path | None:
    """Return the existing worktree root for this conversation, or ``None``."""
    wt = worktree.worktree_path_for(conv_folder)
    return wt if wt.exists() else None


def norm_relpath(relative_path: str) -> str:
    s = relative_path.strip().replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s.lstrip("/")


def safe_resolve(root: Path, relative_path: str) -> Path:
    """Resolve ``relative_path`` inside ``root``. Raise ValueError on escape."""
    raw = relative_path.strip()
    if not raw:
        raise ValueError("empty path")
    if Path(raw).is_absolute():
        raise ValueError(f"Path {raw!r} is absolute. Use a path relative to the repo root.")
    if ".." in Path(raw).parts:
        raise ValueError(f"Path {raw!r} contains '..', which is not allowed.")
    candidate = (root / norm_relpath(raw)).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError(f"Path {raw!r} escapes the repo root.")
    return candidate


def is_protected(relative_path: str) -> bool:
    """True if the path is off-limits to edits (cf. config.REPO_PROTECTED_PATHS)."""
    return worktree.is_protected_path(relative_path)


# ---- read ledger : canonical relpath -> mtime_ns observed at read time ------


def _ledger_path(conv_folder: Path) -> Path:
    return Path(conv_folder) / _READ_LEDGER


def _load_ledger(conv_folder: Path) -> dict[str, int]:
    try:
        data = json.loads(_ledger_path(conv_folder).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_ledger(conv_folder: Path, data: dict[str, int]) -> None:
    p = _ledger_path(conv_folder)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(p)


def mark_read(conv_folder: Path, canonical: str, mtime_ns: int) -> None:
    d = _load_ledger(conv_folder)
    d[canonical] = mtime_ns
    _save_ledger(conv_folder, d)


def read_mtime(conv_folder: Path, canonical: str) -> int | None:
    """mtime_ns recorded at read time for ``canonical``, or ``None`` if unread."""
    return _load_ledger(conv_folder).get(canonical)


def edit_preflight(conv_folder: Path, target: Path, canonical: str) -> str | None:
    """Read-before-edit + freshness gate. Returns an error message or ``None``.

    - never read this conversation → read_before_edit
    - read, but the file changed since (mtime differs) → stale_read
    """
    recorded = read_mtime(conv_folder, canonical)
    if recorded is None:
        return (
            f"read_before_edit: you must repo_read('{canonical}') before editing it, "
            "so you edit against its current content."
        )
    current = target.stat().st_mtime_ns
    if recorded != current:
        return (
            f"stale_read: '{canonical}' changed since you read it. "
            "repo_read it again before editing."
        )
    return None


def cat_n(content: str, start_line: int = 1) -> str:
    """Render ``content`` in ``cat -n`` form (right-aligned line number + tab).

    The repo_edit ``old_string`` must NOT include this line-number prefix — it is
    a display aid only (mirrors Claude Code's Read/Edit contract).
    """
    lines = content.splitlines()
    if not lines:
        return ""
    last = start_line + len(lines) - 1
    width = max(6, len(str(last)))
    return "\n".join(f"{i:>{width}}\t{ln}" for i, ln in enumerate(lines, start=start_line))
