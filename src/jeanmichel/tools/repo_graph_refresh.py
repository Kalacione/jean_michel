"""Tool: repo_graph_refresh — rebuild the code graph (graphify update).

Best-effort: runs ``graphify update`` on PROJECT_ROOT so structural queries
(graphify MCP tools + the CRP structural slice) reflect the latest committed
code. Deterministic (tree-sitter, no LLM), ~seconds. No-op error if graphify is
not installed or no graph exists yet. Within a conversation, uncommitted
worktree edits are covered by the CRP's recent-diff slice — this keeps the one
canonical graph current as work lands.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .. import worktree
from . import _repo
from ._base import ToolSpec
from ._errors import tool_error, tool_ok

_TIMEOUT_S = 120


def make_spec(conv_folder: Path) -> ToolSpec:
    """Return a ToolSpec bound to ``conv_folder`` (refreshes PROJECT_ROOT's graph)."""

    def _handler() -> str:
        if _repo.worktree_root(conv_folder) is None:
            return tool_error("no_worktree", "No code worktree for this conversation.")
        if shutil.which("graphify") is None:
            return tool_error("graphify_unavailable", "graphify is not installed on the host.")
        proj = worktree.source_repo(conv_folder)
        if proj is None:
            return tool_error("no_source_repo", "No resolvable source repo for this conversation.")
        try:
            proc = subprocess.run(
                ["graphify", "update", "."], cwd=str(proj),
                capture_output=True, text=True, timeout=_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return tool_error("graphify_timeout", f"graphify update exceeded {_TIMEOUT_S}s.")
        except (OSError, subprocess.SubprocessError) as e:
            return tool_error("graphify_failed", f"graphify update failed: {e}")
        if proc.returncode != 0:
            return tool_error("graphify_error", (proc.stderr or "graphify error").strip()[:300])
        tail = "\n".join(((proc.stdout or "") + (proc.stderr or "")).splitlines()[-10:])
        return tool_ok("code graph refreshed", output_tail=tail)

    return ToolSpec(
        name="repo_graph_refresh",
        description=(
            "SIGNATURE: repo_graph_refresh(). "
            "Rebuild the project's code graph (graphify update) so structural queries "
            "reflect the latest committed code. Deterministic, no LLM. Call after a "
            "structural change (new/renamed/moved functions or modules) so later "
            "graph lookups are accurate."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_handler,
    )
