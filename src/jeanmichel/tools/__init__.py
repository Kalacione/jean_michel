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

from . import bash_sandbox as _bash_sandbox_mod
from . import clock as _clock_mod
from . import conv_history_scan as _conv_history_scan_mod
from . import conv_status as _conv_status_mod
from . import manage_user_memory as _manage_user_memory_mod
from . import self_inspect_activity as _si_activity_mod
from . import self_inspect_architecture as _si_architecture_mod
from . import self_inspect_config as _si_config_mod
from . import weather as _weather_mod
from . import web_search as _web_search_mod
from . import wikipedia as _wikipedia_mod
from . import workspace_append as _ws_append_mod
from . import workspace_create_file as _ws_create_mod
from . import workspace_list as _ws_list_mod
from . import workspace_str_replace as _ws_replace_mod
from . import workspace_view as _ws_view_mod
from ._base import ToolSpec

# Workspace write tools — exposing any of these without an
# agent_workspace_grants row will always fail at runtime with no_write_grant.
WORKSPACE_WRITE_TOOLS: frozenset[str] = frozenset({
    "workspace_create_file",
    "workspace_str_replace",
    "workspace_append",
})


def build_registry(
    conv_folder: Path,
    has_workspace_write: bool = False,
    conv_id: str = "",
    request_id_provider: Callable[[], str] | None = None,
    sandbox_grants: list[str] | None = None,
    sandbox_image: str | None = None,
    agent_role: str = "",
) -> dict[str, ToolSpec]:
    """Build the tool registry for a given conversation context.

    The registry is permissive — it contains every tool the agent might
    plausibly use. The orchestrator's ``PreToolUse`` hook filters by the
    agent's ``tool_grants`` (loaded from ``agent_tools``) at call time, so
    even tools present here are denied to agents that lack the grant.
    """
    conv_status_spec = _conv_status_mod.make_spec(conv_id) if conv_id else None
    ws_create_spec = _ws_create_mod.make_spec(conv_folder, has_write_grant=has_workspace_write)
    ws_append_spec = _ws_append_mod.make_spec(conv_folder, has_write_grant=has_workspace_write)
    ws_replace_spec = _ws_replace_mod.make_spec(conv_folder, has_write_grant=has_workspace_write)
    ws_view_spec = _ws_view_mod.make_spec(conv_folder)
    ws_list_spec = _ws_list_mod.make_spec(conv_folder)

    registry: dict[str, ToolSpec] = {
        _clock_mod.SPEC.name: _clock_mod.SPEC,
        _weather_mod.SPEC.name: _weather_mod.SPEC,
        _web_search_mod.SPEC.name: _web_search_mod.SPEC,
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
        _manage_user_memory_mod.SPEC.name: _manage_user_memory_mod.SPEC,
    }
    if conv_status_spec is not None:
        registry[conv_status_spec.name] = conv_status_spec
    if conv_id and request_id_provider is not None and sandbox_grants is not None:
        sandbox_spec = _bash_sandbox_mod.make_spec(
            conv_folder, conv_id, request_id_provider, sandbox_grants,
            sandbox_image=sandbox_image,
        )
        registry[sandbox_spec.name] = sandbox_spec
    return registry


__all__ = ["ToolSpec", "WORKSPACE_WRITE_TOOLS", "build_registry"]
