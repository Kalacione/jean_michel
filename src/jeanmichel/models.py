"""Runtime dataclasses (DB rows + transient state)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Agent:
    id: int
    code: str
    name: str
    role: str            # 'router' | 'specialist' | 'finalizer'
    mission: str
    thinking_mode: bool
    temperature: float
    sandbox_image: str | None = None   # override Docker image for bash_sandbox


@dataclass
class Paradigm:
    section_code: str
    category_code: str
    category_title: str
    code: str
    title: str
    content: str


@dataclass
class Conversation:
    id: str
    folder_path: str
    user_language: str | None
    title: str | None = None
    mode: str = "analyse"


@dataclass
class Request:
    id: str
    conversation_id: str
    parent_request_id: str | None
    dispatch_group_id: str | None
    depth: int
    agent_id: int
    inbound_briefing: str | None
    expected_outcome: str | None
    status: str = "pending"
    turn_index: int = 0


@dataclass
class ToolCall:
    """Parsed tool call emitted by the model."""
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    thinking: str
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    corrupted: bool = False
