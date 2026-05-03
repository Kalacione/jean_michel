"""Native Python tools available to agents.

Agentic control tools (delegate_to, ask_human, return_to_user) are NOT here —
they are intercepted directly by the orchestrator.

Public API:
    ToolSpec         — frozen dataclass describing one tool
    build_registry   — build the per-request tool registry (dict[str, ToolSpec])
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from . import bash_sandbox as _bash_sandbox_mod
from . import clock as _clock_mod
from . import conv_read_file as _conv_read_file_mod
from . import weather as _weather_mod
from . import wikipedia as _wikipedia_mod
from . import workspace_create_file as _ws_create_mod
from . import workspace_list as _ws_list_mod
from . import workspace_str_replace as _ws_replace_mod
from . import workspace_view as _ws_view_mod
from ._base import ToolSpec


def build_registry(
    conv_folder: Path,
    has_workspace_write: bool = False,
    conv_id: str = "",
    request_id_provider: Callable[[], str] | None = None,
    sandbox_grants: list[str] | None = None,
) -> dict[str, ToolSpec]:
    """Build the tool registry for a given conversation context."""
    conv_read_file_spec = _conv_read_file_mod.make_spec(conv_folder)
    ws_create_spec = _ws_create_mod.make_spec(conv_folder, has_write_grant=has_workspace_write)
    ws_replace_spec = _ws_replace_mod.make_spec(conv_folder, has_write_grant=has_workspace_write)
    ws_view_spec = _ws_view_mod.make_spec(conv_folder)
    ws_list_spec = _ws_list_mod.make_spec(conv_folder)
    registry = {
        _clock_mod.SPEC.name: _clock_mod.SPEC,
        conv_read_file_spec.name: conv_read_file_spec,
        _weather_mod.SPEC.name: _weather_mod.SPEC,
        _wikipedia_mod.SEARCH_SPEC.name: _wikipedia_mod.SEARCH_SPEC,
        _wikipedia_mod.GET_PAGE_SPEC.name: _wikipedia_mod.GET_PAGE_SPEC,
        ws_create_spec.name: ws_create_spec,
        ws_replace_spec.name: ws_replace_spec,
        ws_view_spec.name: ws_view_spec,
        ws_list_spec.name: ws_list_spec,
    }
    if conv_id and request_id_provider is not None and sandbox_grants is not None:
        sandbox_spec = _bash_sandbox_mod.make_spec(
            conv_folder, conv_id, request_id_provider, sandbox_grants
        )
        registry[sandbox_spec.name] = sandbox_spec
    return registry


__all__ = ["ToolSpec", "build_registry"]
