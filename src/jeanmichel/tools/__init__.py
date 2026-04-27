"""Native Python tools available to agents.

Agentic control tools (delegate_to, ask_human, return_to_user) are NOT here —
they are intercepted directly by the orchestrator.

Public API:
    ToolSpec         — frozen dataclass describing one tool
    build_registry   — build the per-request tool registry (dict[str, ToolSpec])
"""

from __future__ import annotations

from pathlib import Path

from ._base import ToolSpec
from . import clock as _clock_mod
from . import conv_read_file as _conv_read_file_mod


def build_registry(conv_folder: Path) -> dict[str, ToolSpec]:
    """Build the tool registry for a given conversation context."""
    conv_read_file_spec = _conv_read_file_mod.make_spec(conv_folder)
    return {
        _clock_mod.SPEC.name: _clock_mod.SPEC,
        conv_read_file_spec.name: conv_read_file_spec,
    }


__all__ = ["ToolSpec", "build_registry"]
