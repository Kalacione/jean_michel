"""LLM client. Wraps Ollama; provides a deterministic Mock for tests / demos."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FutureTimeout
from typing import Any, Protocol

from .config import DEFAULT_OLLAMA_HOST, DEFAULT_OLLAMA_MODEL, LLM_CALL_TIMEOUT_SECONDS
from .models import LLMResponse, ToolCall

_log = logging.getLogger(__name__)

_CORRUPTION_MARKERS = (
    "<thought",
    "</thought",
    "<|",
    "|>",
    "<start_of_turn>",
    "<end_of_turn>",
    "</s>",
    "[/INST]",
    "<tool_call>",
)


def _looks_corrupted(text: str) -> bool:
    if not text:
        return False
    return any(marker in text for marker in _CORRUPTION_MARKERS)


class LLMTimeoutError(RuntimeError):
    """Raised when an Ollama chat() call exceeds LLM_CALL_TIMEOUT_SECONDS."""


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
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ollama-call")

    @staticmethod
    def _to_llm_response(raw) -> LLMResponse:
        msg = raw.get("message", {}) if isinstance(raw, dict) else getattr(raw, "message", {})
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
            "keep_alive": "30m",
            "stream": False,
        }
        if tools:
            kwargs["tools"] = tools
        if thinking:
            kwargs["think"] = True

        last_resp: LLMResponse | None = None
        for attempt in (1, 2):
            future = self._executor.submit(self._client.chat, **kwargs)
            try:
                raw = future.result(timeout=LLM_CALL_TIMEOUT_SECONDS)
            except _FutureTimeout:
                future.cancel()
                raise LLMTimeoutError(
                    f"Ollama chat() exceeded {LLM_CALL_TIMEOUT_SECONDS}s. "
                    "Model may be hung or VRAM saturated."
                ) from None
            last_resp = self._to_llm_response(raw)
            if not (_looks_corrupted(last_resp.content) or _looks_corrupted(last_resp.thinking or "")):
                return last_resp
            if attempt == 1:
                _log.warning("LLM output looks corrupted (attempt 1), retrying once")
        assert last_resp is not None
        last_resp.corrupted = True
        return last_resp


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
        last_resp: LLMResponse | None = None
        for attempt in (1, 2):
            if not self.script:
                raise RuntimeError("MockClient script exhausted.")
            last_resp = self.script.pop(0)
            if not (_looks_corrupted(last_resp.content) or _looks_corrupted(last_resp.thinking or "")):
                return last_resp
            if attempt == 1:
                pass  # retry: will pop next item from script
        assert last_resp is not None
        last_resp.corrupted = True
        return last_resp
