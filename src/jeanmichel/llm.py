"""LLM client. Wraps Ollama; provides a deterministic Mock for tests / demos."""

from __future__ import annotations

from typing import Any, Protocol

from .config import DEFAULT_OLLAMA_HOST, DEFAULT_OLLAMA_MODEL
from .models import LLMResponse, ToolCall


class LLMClient(Protocol):
    def chat(self, *, system: str, user: str, tools: list[dict[str, Any]],
             temperature: float, thinking: bool) -> LLMResponse: ...


# ---- Ollama implementation ------------------------------------------------

class OllamaClient:
    def __init__(self, model: str = DEFAULT_OLLAMA_MODEL,
                 host: str = DEFAULT_OLLAMA_HOST) -> None:
        try:
            import ollama  # noqa: F401
        except ImportError as e:
            raise RuntimeError("Install `ollama` to use OllamaClient.") from e
        self.model = model
        self.host = host
        from ollama import Client
        self._client = Client(host=host)

    def chat(self, *, system: str, user: str, tools: list[dict[str, Any]],
             temperature: float, thinking: bool) -> LLMResponse:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "options": {"temperature": temperature},
            "stream": False,
        }
        if tools:
            kwargs["tools"] = tools
        if thinking:
            kwargs["think"] = True

        resp = self._client.chat(**kwargs)
        msg = resp.get("message", {}) if isinstance(resp, dict) else getattr(resp, "message", {})
        thinking_text = (msg.get("thinking") if isinstance(msg, dict) else getattr(msg, "thinking", "")) or ""
        content = (msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")) or ""
        raw_calls = (msg.get("tool_calls") if isinstance(msg, dict) else getattr(msg, "tool_calls", [])) or []

        tool_calls: list[ToolCall] = []
        for c in raw_calls:
            fn = c.get("function") if isinstance(c, dict) else getattr(c, "function", None)
            if fn is None:
                continue
            name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", "")
            args = fn.get("arguments") if isinstance(fn, dict) else getattr(fn, "arguments", {})
            tool_calls.append(ToolCall(name=name, arguments=dict(args or {})))

        return LLMResponse(thinking=thinking_text, content=content, tool_calls=tool_calls)


# ---- Mock implementation --------------------------------------------------

class MockClient:
    """Scriptable mock. Each call pops the next response from `script`.

    Useful to demo / test the orchestrator end-to-end without Ollama.
    """

    def __init__(self, script: list[LLMResponse]) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, str]] = []

    def chat(self, *, system: str, user: str, tools: list[dict[str, Any]],
             temperature: float, thinking: bool) -> LLMResponse:
        self.calls.append((system, user))
        if not self.script:
            raise RuntimeError("MockClient script exhausted.")
        return self.script.pop(0)
