"""Helpers for the orchestrator_v2 tests.

Builds minimal AgentSpec / ToolSpec / LLMResponse instances so each test
stays focused on the behaviour under test.
"""

from __future__ import annotations

import json
from typing import Any

from jeanmichel.models import LLMResponse, ToolCall
from jeanmichel.orchestrator_v2 import AgentSpec
from jeanmichel.tools._base import ToolSpec


def make_agent(
    code: str,
    role: str = "router",
    *,
    tool_grants: set[str] | None = None,
    delegation_targets: set[str] | None = None,
    system_prompt: str | None = None,
    model: str = "mock-model",
) -> AgentSpec:
    """Build a minimal AgentSpec for tests."""
    return AgentSpec(
        code=code,
        role=role,
        system_prompt=system_prompt or f"You are {code}.",
        tool_grants=frozenset(tool_grants or set()),
        delegation_targets=frozenset(delegation_targets or set()),
        model=model,
        thinking=False,
        temperature=0.0,
    )


def make_echo_tool(name: str = "echo") -> ToolSpec:
    """Build a trivial tool that echoes its 'text' argument."""

    def handler(text: str = "") -> str:
        return json.dumps({"summary": f"echoed: {text}", "echo": text})

    return ToolSpec(
        name=name,
        description=f"Echo the {name} input.",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        handler=handler,
    )


def make_tool_spec(name: str, handler) -> ToolSpec:
    """Build a ToolSpec with a custom handler. The handler must return a JSON string."""
    return ToolSpec(
        name=name,
        description=f"Test tool {name}.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=handler,
    )


def assistant_response(
    content: str = "", tool_calls: list[ToolCall] | None = None
) -> LLMResponse:
    """Build a scripted LLM response for MockClient."""
    return LLMResponse(
        thinking="",
        content=content,
        tool_calls=tool_calls or [],
    )


def tool_call(name: str, **arguments) -> ToolCall:
    """Build a ToolCall for scripted assistant_response."""
    return ToolCall(name=name, arguments=arguments)
