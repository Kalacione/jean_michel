"""Native Python tools available to agents.

Agentic control tools (delegate_to, ask_human, return_to_user) are NOT defined
here — they are intercepted directly by the orchestrator.

This module hosts only "real work" tools: clock, read_file, etc.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]   # JSON schema (Ollama-compatible)
    handler: Callable[..., str]  # returns a string fed back as tool_response


# ---- Tool implementations -------------------------------------------------

def _clock(timezone: str = "UTC") -> str:
    try:
        tz = ZoneInfo(timezone)
    except Exception:
        return f'{{"error": "Unknown timezone: {timezone}"}}'
    now_utc = datetime.now(UTC)
    now_local = now_utc.astimezone(tz)
    return (
        f'{{"utc": "{now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")}", '
        f'"local": "{now_local.strftime("%Y-%m-%dT%H:%M:%S%z")}", '
        f'"timezone": "{timezone}"}}'
    )


def _read_file_factory(conv_folder: Path) -> Callable[..., str]:
    def _read_file(relative_path: str, max_bytes: int = 100_000) -> str:
        target = (conv_folder / relative_path).resolve()
        # Path traversal guard.
        if not str(target).startswith(str(conv_folder.resolve())):
            return '{"error": "Path escapes conversation folder."}'
        if not target.exists():
            return f'{{"error": "Not found: {relative_path}"}}'
        data = target.read_bytes()[:max_bytes]
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return '{"error": "File is not valid UTF-8."}'
    return _read_file


# ---- Registry -------------------------------------------------------------

def build_registry(conv_folder: Path) -> dict[str, ToolSpec]:
    """Build the tool registry for a given conversation context."""
    return {
        "clock": ToolSpec(
            name="clock",
            description="Return current UTC time and the time in the given IANA timezone.",
            parameters={
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone name, e.g. 'America/Montreal'. Defaults to UTC.",
                    },
                },
                "required": [],
            },
            handler=_clock,
        ),
        "read_file": ToolSpec(
            name="read_file",
            description="Read a file located inside the current conversation folder. "
                        "Use the relative path provided as a support_file in the briefing.",
            parameters={
                "type": "object",
                "properties": {
                    "relative_path": {
                        "type": "string",
                        "description": "Path relative to the conversation folder.",
                    },
                    "max_bytes": {
                        "type": "integer",
                        "description": "Maximum number of bytes to read. Default 100000.",
                    },
                },
                "required": ["relative_path"],
            },
            handler=_read_file_factory(conv_folder),
        ),
    }


# Per-agent tool grants. KISS: hardcoded for MVP, can move to DB later.
AGENT_TOOL_GRANTS: dict[str, list[str]] = {
    "jean-michel": ["clock", "read_file"],
    "summarizer":  ["read_file"],
    "synthesizer": [],
}
