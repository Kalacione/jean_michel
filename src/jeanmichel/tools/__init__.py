"""Native Python tools available to v2 agents.

Control verbs (delegate_to, ask_human, report_back) are NOT here — they are
intercepted directly by the v2 orchestrator (see ``orchestrator_v2`` and
the schemas in ``tools/delegate_to.py`` / ``tools/report_back.py``).

Public API :
    ToolSpec         — frozen dataclass describing one tool
    build_registry   — build the per-conversation tool registry
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import analyze_image as _analyze_image_mod
from . import bash_sandbox as _bash_sandbox_mod
from . import clock as _clock_mod
from . import conv_history_scan as _conv_history_scan_mod
from . import github as _github_mod
from . import image_fetch as _image_fetch_mod
from . import image_search as _image_search_mod
from . import manage_memory as _manage_memory_mod
from . import news as _news_mod
from . import pypi as _pypi_mod
from . import repo_edit as _repo_edit_mod
from . import repo_glob as _repo_glob_mod
from . import repo_grep as _repo_grep_mod
from . import repo_read as _repo_read_mod
from . import repo_write as _repo_write_mod
from . import self_inspect_activity as _si_activity_mod
from . import self_inspect_architecture as _si_architecture_mod
from . import self_inspect_config as _si_config_mod
from . import stackoverflow as _stackoverflow_mod
from . import todo_write as _todo_write_mod
from . import weather as _weather_mod
from . import web_fetch as _web_fetch_mod
from . import web_search as _web_search_mod
from . import wikipedia as _wikipedia_mod
from . import workspace_append as _ws_append_mod
from . import workspace_create_dir as _ws_mkdir_mod
from . import workspace_create_file as _ws_create_mod
from . import workspace_delete_dir as _ws_deldir_mod
from . import workspace_delete_file as _ws_delfile_mod
from . import workspace_list as _ws_list_mod
from . import workspace_str_replace as _ws_replace_mod
from . import workspace_view as _ws_view_mod
from ._base import ToolSpec
from .. import worktree as _worktree_mod

# Workspace write tools — exposing any of these without an
# agent_workspace_grants row will always fail at runtime with no_write_grant.
WORKSPACE_WRITE_TOOLS: frozenset[str] = frozenset({
    "workspace_create_file",
    "workspace_str_replace",
    "workspace_append",
    "workspace_create_dir",
    "workspace_delete_file",
    "workspace_delete_dir",
})


def build_registry(
    conv_folder: Path,
    has_workspace_write: bool = False,
    conv_id: str = "",
    request_id_provider: Callable[[], str] | None = None,
    sandbox_grants: list[str] | None = None,
    sandbox_image: str | None = None,
    agent_role: str = "",
    memory_user_id: int | None = None,
    memory_project_id: int | None = None,
    vision_client: Any = None,
    extra_tools: list[ToolSpec] | None = None,
) -> dict[str, ToolSpec]:
    """Build the tool registry for a given conversation context.

    The registry is permissive — it contains every tool the agent might
    plausibly use. The orchestrator's ``PreToolUse`` hook filters by the
    agent's ``tool_grants`` (loaded from ``agent_tools``) at call time, so
    even tools present here are denied to agents that lack the grant.
    """
    ws_create_spec = _ws_create_mod.make_spec(conv_folder, has_write_grant=has_workspace_write)
    ws_append_spec = _ws_append_mod.make_spec(conv_folder, has_write_grant=has_workspace_write)
    ws_replace_spec = _ws_replace_mod.make_spec(conv_folder, has_write_grant=has_workspace_write)
    ws_view_spec = _ws_view_mod.make_spec(conv_folder)
    ws_list_spec = _ws_list_mod.make_spec(conv_folder)
    ws_mkdir_spec = _ws_mkdir_mod.make_spec(conv_folder, has_write_grant=has_workspace_write)
    ws_delfile_spec = _ws_delfile_mod.make_spec(conv_folder, has_write_grant=has_workspace_write)
    ws_deldir_spec = _ws_deldir_mod.make_spec(conv_folder, has_write_grant=has_workspace_write)
    todo_write_spec = _todo_write_mod.make_spec(conv_folder)
    # Bind memory to the conversation context : owner (None → reserved cli user)
    # + the conversation's project (None → no project ; project-scope notes denied).
    mum_spec = _manage_memory_mod.make_spec(memory_user_id, memory_project_id)
    # Workspace-bound image tools : analyze_image reads the normalized derivative
    # and talks to a vision client (reuses the turn's main_llm when injected) ;
    # image_fetch downloads a web image into the workspace.
    analyze_image_spec = _analyze_image_mod.make_spec(conv_folder, vision_client)
    image_fetch_spec = _image_fetch_mod.make_spec(conv_folder)

    registry: dict[str, ToolSpec] = {
        _clock_mod.SPEC.name: _clock_mod.SPEC,
        _weather_mod.SPEC.name: _weather_mod.SPEC,
        _web_search_mod.SPEC.name: _web_search_mod.SPEC,
        _image_search_mod.SPEC.name: _image_search_mod.SPEC,
        analyze_image_spec.name: analyze_image_spec,
        image_fetch_spec.name: image_fetch_spec,
        _wikipedia_mod.SEARCH_SPEC.name: _wikipedia_mod.SEARCH_SPEC,
        _wikipedia_mod.GET_PAGE_SPEC.name: _wikipedia_mod.GET_PAGE_SPEC,
        _si_config_mod.SPEC.name: _si_config_mod.SPEC,
        _si_activity_mod.SPEC.name: _si_activity_mod.SPEC,
        _si_architecture_mod.SPEC.name: _si_architecture_mod.SPEC,
        _conv_history_scan_mod.SPEC.name: _conv_history_scan_mod.SPEC,
        ws_create_spec.name: ws_create_spec,
        ws_append_spec.name: ws_append_spec,
        ws_replace_spec.name: ws_replace_spec,
        ws_view_spec.name: ws_view_spec,
        ws_list_spec.name: ws_list_spec,
        ws_mkdir_spec.name: ws_mkdir_spec,
        ws_delfile_spec.name: ws_delfile_spec,
        ws_deldir_spec.name: ws_deldir_spec,
        todo_write_spec.name: todo_write_spec,
        mum_spec.name: mum_spec,
        _news_mod.LATEST_SPEC.name: _news_mod.LATEST_SPEC,
        _news_mod.ARCHIVE_SPEC.name: _news_mod.ARCHIVE_SPEC,
        _web_fetch_mod.SPEC.name: _web_fetch_mod.SPEC,
        _github_mod.SEARCH_CODE_SPEC.name: _github_mod.SEARCH_CODE_SPEC,
        _github_mod.SEARCH_REPOS_SPEC.name: _github_mod.SEARCH_REPOS_SPEC,
        _stackoverflow_mod.SPEC.name: _stackoverflow_mod.SPEC,
        _pypi_mod.SPEC.name: _pypi_mod.SPEC,
    }
    if conv_id and request_id_provider is not None and sandbox_grants is not None:
        sandbox_spec = _bash_sandbox_mod.make_spec(
            conv_folder, conv_id, request_id_provider, sandbox_grants,
            sandbox_image=sandbox_image,
        )
        registry[sandbox_spec.name] = sandbox_spec
    # Repo tools (code mode): only when an isolated git worktree exists for this
    # conversation. Bound to the worktree; the PreToolUse grant check still gates
    # which agents may call them. Not registered outside code mode → zero leakage
    # into other task types.
    if _worktree_mod.worktree_path_for(conv_folder).exists():
        for repo_spec in (
            _repo_read_mod.make_spec(conv_folder),
            _repo_grep_mod.make_spec(conv_folder),
            _repo_glob_mod.make_spec(conv_folder),
            _repo_edit_mod.make_spec(conv_folder),
            _repo_write_mod.make_spec(conv_folder),
        ):
            registry[repo_spec.name] = repo_spec
    # MCP-sourced tools (discovered from hosted servers) — added last; names are
    # namespaced (mcp__server__tool) so they can't collide with native tools.
    for spec in extra_tools or []:
        registry[spec.name] = spec
    return registry


__all__ = ["ToolSpec", "WORKSPACE_WRITE_TOOLS", "build_registry"]
