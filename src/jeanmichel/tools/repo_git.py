"""Tool: repo_git — READ-ONLY git introspection of the repo worktree.

Answers the whole class of "git history / status / diff" questions that the
repo_read/grep/glob/edit/write/test tools cannot (they operate on file content,
not on git metadata). Runs ``git <subcommand> ...`` with ``cwd=worktree`` on the
HOST — same trust model as repo_test (the worktree is the project's own code,
git-isolated). SAFE by construction: only a fixed allow-list of READ-ONLY
porcelain subcommands (log/show/diff/status/blame); no shell (args are
``shlex``-split and passed after the subcommand, so no global ``-c``/``-C``
injection); it can neither write the repo nor escape it.

This is the deterministic answer to the bug where a worker reached for
``bash_sandbox`` to run ``git log`` — the sandbox is network-less and mounts only
the scratch, so it cannot see the repo at all.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from . import _repo
from ._base import ToolSpec
from ._errors import tool_error, tool_ok

# READ-ONLY porcelain only. Anything that can write a ref, the index, the
# working tree, or the object store is intentionally absent.
_ALLOWED = ("log", "show", "diff", "status", "blame")

# Sane defaults so a bare `repo_git(subcommand="log")` returns something useful.
_DEFAULT_ARGS: dict[str, list[str]] = {
    "log": ["-n", "20", "--date=iso", "--pretty=format:%h  %ad  %an  %s"],
    "status": ["--short", "--branch"],
}

_GIT_TIMEOUT = 20
_MAX_LINES = 120


def make_spec(conv_folder: Path) -> ToolSpec:
    """Return a ToolSpec bound to ``conv_folder`` (reads its git worktree)."""

    def _handler(subcommand: str = "log", args: str = "") -> str:
        root = _repo.worktree_root(conv_folder)
        if root is None:
            return tool_error("no_worktree", "No code worktree for this conversation.")
        sub = (subcommand or "log").strip()
        if sub not in _ALLOWED:
            return tool_error(
                "subcommand_not_allowed",
                f"repo_git is read-only; allowed subcommands: {', '.join(_ALLOWED)}.",
            )
        try:
            extra = shlex.split(args or "")
        except ValueError as e:
            return tool_error("bad_args", f"could not parse args: {e}")
        if not extra and sub in _DEFAULT_ARGS:
            extra = list(_DEFAULT_ARGS[sub])
        cmd = ["git", sub, *extra]
        try:
            proc = subprocess.run(
                cmd, cwd=str(root), capture_output=True, text=True, timeout=_GIT_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return tool_error("git_timeout", f"git {sub} exceeded {_GIT_TIMEOUT}s.")
        except (OSError, subprocess.SubprocessError) as e:
            return tool_error("git_failed_to_run", f"could not run git: {e}")
        out = (proc.stdout or "")
        if proc.returncode != 0:
            return tool_error(
                "git_error",
                (proc.stderr or out or f"git {sub} failed").strip()[:300],
                exit_code=proc.returncode,
            )
        lines = out.splitlines()
        truncated = len(lines) > _MAX_LINES
        body = "\n".join(lines[:_MAX_LINES])
        summary = f"git {sub}: {len(lines)} line(s)" + (" (truncated)" if truncated else "")
        return tool_ok(summary, output=body, truncated=truncated, exit_code=0)

    return ToolSpec(
        name="repo_git",
        description=(
            "SIGNATURE: repo_git(subcommand?, args?). "
            "READ-ONLY git introspection of the attached repo worktree. subcommand is one of "
            "log | show | diff | status | blame (default: log). args is an optional string of extra "
            "git arguments passed after the subcommand (e.g. '-n 5 --oneline', 'HEAD~3..HEAD', "
            "'src/foo.py'). Use THIS for git history / status / diff questions — never bash_sandbox "
            "(it cannot see the repo). Cannot write or escape the repo."
        ),
        parameters={
            "type": "object",
            "properties": {
                "subcommand": {
                    "type": "string",
                    "enum": list(_ALLOWED),
                    "description": "Read-only git subcommand (default: log).",
                },
                "args": {
                    "type": "string",
                    "description": "Optional extra git args after the subcommand (e.g. '-n 5', a path, a ref range).",
                },
            },
            "required": [],
        },
        handler=_handler,
    )
