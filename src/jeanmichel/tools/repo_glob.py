"""Tool: repo_glob — list files in the worktree matching a glob (deterministic).

Backed by ``rg --files`` (respects .gitignore), filtered by the glob. Bounded
output. Mirrors Claude Code's Glob contract.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import _repo
from ._base import ToolSpec
from ._errors import tool_error, tool_ok

_RG_TIMEOUT_S = 20
_CAP = 100


def make_spec(conv_folder: Path) -> ToolSpec:
    """Return a ToolSpec bound to ``conv_folder`` (lists its git worktree)."""

    def _handler(pattern: str = "*", path: str = ".") -> str:
        root = _repo.worktree_root(conv_folder)
        if root is None:
            return tool_error("no_worktree", "No code worktree for this conversation.")
        if shutil.which("rg") is None:
            return tool_error("rg_unavailable", "ripgrep (rg) is not installed on the host.")
        search_rel = (path or ".").strip()
        if search_rel not in (".", ""):
            try:
                _repo.safe_resolve(root, search_rel)
            except ValueError as e:
                return tool_error("path_escape", str(e))
        cmd = ["rg", "--files", "-g", pattern or "*"]
        # Omit the path when listing the root so rg doesn't prefix './' to files.
        if search_rel not in (".", ""):
            cmd += ["--", search_rel]
        try:
            proc = subprocess.run(cmd, cwd=str(root), capture_output=True,
                                  text=True, timeout=_RG_TIMEOUT_S)
        except (subprocess.SubprocessError, OSError) as e:
            return tool_error("rg_failed", f"ripgrep failed: {e}")
        if proc.returncode not in (0, 1):
            return tool_error("rg_error", proc.stderr.strip()[:300] or "ripgrep error")
        files = sorted(proc.stdout.splitlines())
        truncated = len(files) > _CAP
        shown = files[:_CAP]
        return tool_ok(
            f"{len(shown)} file(s) matching '{pattern}'" + (" (truncated)" if truncated else ""),
            files=shown,
            file_count=len(shown),
            truncated=truncated,
        )

    return ToolSpec(
        name="repo_glob",
        description=(
            "SIGNATURE: repo_glob(pattern?, path?). "
            "List files in the project repo matching a glob (e.g. '*.py', 'src/**/*.py'). "
            f"Respects .gitignore. Capped at {_CAP} results (a 'truncated' flag signals more). "
            "Use this to discover files instead of shelling out to find/ls."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "File glob, e.g. '*.py'. Default '*'."},
                "path": {"type": "string", "description": "Sub-path to scope the listing (default repo root)."},
            },
            "required": [],
        },
        handler=_handler,
    )
