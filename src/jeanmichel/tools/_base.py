"""Base dataclass for tool specifications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]   # JSON schema (Ollama-compatible)
    handler: Callable[..., str]  # returns a string fed back as tool_response
