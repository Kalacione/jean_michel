"""Tool: repo_grep — ripgrep over the code-mode worktree (deterministic).

Runs ``rg`` on the host (no sandbox needed — read-only search) scoped to the
worktree root. Respects .gitignore by default. Structured, bounded output with
an explicit truncation signal (mirrors Claude Code's Grep contract).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import _repo
from ._base import ToolSpec
from ._errors import tool_error, tool_ok

_RG_TIMEOUT_S = 20
_MODE_FLAGS = {"content": [], "files_with_matches": ["-l"], "count": ["-c"]}


def make_spec(conv_folder: Path) -> ToolSpec:
    """Return a ToolSpec bound to ``conv_folder`` (searches its git worktree)."""

    def _handler(
        pattern: str,
        path: str = ".",
        glob: str = "",
        output_mode: str = "content",
        context: int = 0,
        ignore_case: bool = False,
        head_limit: int = 200,
    ) -> str:
        root = _repo.worktree_root(conv_folder)
        if root is None:
            return tool_error("no_worktree", "No code worktree for this conversation.")
        if shutil.which("rg") is None:
            return tool_error("rg_unavailable", "ripgrep (rg) is not installed on the host.")
        if output_mode not in _MODE_FLAGS:
            return tool_error("bad_output_mode",
                              f"output_mode must be one of {sorted(_MODE_FLAGS)}.")
        # Validate the search path stays inside the worktree.
        search_rel = (path or ".").strip()
        if search_rel not in (".", ""):
            try:
                _repo.safe_resolve(root, search_rel)
            except ValueError as e:
                return tool_error("path_escape", str(e))
        cmd = ["rg", "--color=never", *_MODE_FLAGS[output_mode]]
        if output_mode == "content":
            cmd += ["--line-number", "--no-heading"]
            if context and context > 0:
                cmd += ["-C", str(min(context, 10))]
        if ignore_case:
            cmd.append("-i")
        if glob.strip():
            cmd += ["-g", glob.strip()]
        cmd += ["--", pattern]
        # Omit the path when searching the root so rg doesn't prefix './' to hits.
        if search_rel not in (".", ""):
            cmd.append(search_rel)
        try:
            proc = subprocess.run(cmd, cwd=str(root), capture_output=True,
                                  text=True, timeout=_RG_TIMEOUT_S)
        except (subprocess.SubprocessError, OSError) as e:
            return tool_error("rg_failed", f"ripgrep failed: {e}")
        if proc.returncode == 1:
            return tool_ok("no matches", matches=[], match_count=0, truncated=False)
        if proc.returncode not in (0, 1):
            return tool_error("rg_error", proc.stderr.strip()[:300] or "ripgrep error")
        lines = proc.stdout.splitlines()
        truncated = len(lines) > head_limit
        shown = lines[:head_limit]
        return tool_ok(
            f"{len(shown)} line(s) [{output_mode}]" + (" (truncated)" if truncated else ""),
            matches=shown,
            match_count=len(shown),
            truncated=truncated,
        )

    return ToolSpec(
        name="repo_grep",
        description=(
            "SIGNATURE: repo_grep(pattern, path?, glob?, output_mode?, context?, ignore_case?, head_limit?). "
            "Search the project repo with ripgrep (regex). output_mode: 'content' (default, "
            "file:line:text), 'files_with_matches', or 'count'. `glob` filters files (e.g. '*.py'), "
            "`context` adds N lines around matches, `head_limit` caps results (default 200; a "
            "'truncated' flag signals more). Respects .gitignore. Use this for structural lookups "
            "instead of shelling out."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for."},
                "path": {"type": "string", "description": "Sub-path to scope the search (default repo root)."},
                "glob": {"type": "string", "description": "Optional file glob filter, e.g. '*.py'."},
                "output_mode": {"type": "string", "description": "'content' | 'files_with_matches' | 'count'."},
                "context": {"type": "integer", "description": "Lines of context around each match (content mode)."},
                "ignore_case": {"type": "boolean", "description": "Case-insensitive search."},
                "head_limit": {"type": "integer", "description": "Max output lines (default 200)."},
            },
            "required": ["pattern"],
        },
        handler=_handler,
    )
