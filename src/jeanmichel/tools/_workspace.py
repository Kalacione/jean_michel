"""Shared workspace primitives used by workspace_* tools.

Centralizes path validation, quota check, and workspace root resolution.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..config import WORKSPACE_QUOTA_BYTES

_log = logging.getLogger(__name__)
_STRIP_PREFIX_RE = re.compile(r"^(?:\./)?workspace/+", re.IGNORECASE)


def workspace_root_for(conv_folder: Path) -> Path:
    """Return the absolute workspace root for a conversation, creating it if missing."""
    root = conv_folder / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_resolve(workspace_root: Path, relative_path: str) -> Path:
    """Resolve `relative_path` inside `workspace_root`. Raise ValueError on escape.

    Accepts both workspace-relative paths and root-relative paths (for read-only
    operations in workspace_view that can also read conversation root files).
    Always validates that the result stays inside workspace_root.
    """
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("relative_path must be a non-empty string.")
    raw = relative_path
    if raw.startswith("/"):
        raise ValueError(
            f"Path {raw!r} is absolute. Use a path relative to the workspace root."
        )
    if ".." in Path(raw).parts:
        raise ValueError(f"Path {raw!r} contains '..', which is not allowed.")
    normalised = _STRIP_PREFIX_RE.sub("", raw, count=1)
    if normalised != raw:
        _log.warning(
            "workspace path normalised: %r → %r (leading 'workspace/' stripped)",
            raw, normalised,
        )
    if not normalised or not normalised.strip("/"):
        raise ValueError(f"Path {raw!r} resolves to the workspace root itself.")
    candidate = (workspace_root / normalised).resolve()
    if not candidate.is_relative_to(workspace_root.resolve()):
        raise ValueError(f"Path {raw!r} escapes the workspace root.")
    return candidate


def workspace_size(workspace_root: Path) -> int:
    """Total bytes used by all files in the workspace."""
    return sum(p.stat().st_size for p in workspace_root.rglob("*") if p.is_file())


def quota_remaining(workspace_root: Path) -> int:
    """Bytes still available before quota."""
    return max(0, WORKSPACE_QUOTA_BYTES - workspace_size(workspace_root))
