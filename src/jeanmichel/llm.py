"""LLM client. Wraps Ollama; provides a deterministic Mock for tests / demos.

This module exposes two APIs side by side :

- **Legacy `chat(*, system, user, tools, temperature, thinking)`** — v1 path.
  Builds `messages=[{system}, {user}]` and forgets everything between calls.
  Used by the current orchestrator. Kept until Phase 6 of the v2 migration.

- **New `chat_messages(*, messages, tools, temperature, thinking, model?, format?)`**
  — v2 path. Accepts a full Ollama-shape `messages` array (multi-turn natif :
  `system, user, assistant, tool, assistant, tool, …`). Used by the new
  orchestrator. Cf. DevNotes/REVOLUCION/06_proposition_v2.md §1.2 et §4.

Both methods are implemented on `OllamaClient` and `MockClient` so tests and
production can switch independently. The mock's `script` queue feeds both
methods — order of pops is the order of calls.
"""

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
    """v1 protocol. Legacy single-turn signature."""
    def chat(self, *, system: str, user: str, tools: list[dict[str, Any]],
             temperature: float, thinking: bool) -> LLMResponse: ...


class LLMClientV2(Protocol):
    """v2 protocol. Native multi-turn Ollama messages format.

    `model` is optional — if None, the client uses its default (configured
    at construction). `format` accepts Ollama's `"json"` to force a strict
    JSON output (used by the Tier 0 dispatcher).
    """
    def chat_messages(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float,
        thinking: bool,
        model: str | None = None,
        format: str | None = None,
    ) -> LLMResponse: ...


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
        # v2 : capture token usage when Ollama reports it. Older Ollama
        # versions or mock payloads return 0/None silently.
        prompt_eval = (
            raw.get("prompt_eval_count") if isinstance(raw, dict)
            else getattr(raw, "prompt_eval_count", 0)
        ) or 0
        eval_count = (
            raw.get("eval_count") if isinstance(raw, dict)
            else getattr(raw, "eval_count", 0)
        ) or 0
        return LLMResponse(
            thinking=thinking_text,
            content=content,
            tool_calls=tool_calls,
            prompt_eval_count=int(prompt_eval),
            eval_count=int(eval_count),
        )

    def chat(self, *, system: str, user: str, tools: list[dict[str, Any]],
             temperature: float, thinking: bool) -> LLMResponse:
        """v1 legacy path. Builds a 2-message array and forwards to chat_messages."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return self.chat_messages(
            messages=messages,
            tools=tools,
            temperature=temperature,
            thinking=thinking,
        )

    def chat_messages(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float,
        thinking: bool,
        model: str | None = None,
        format: str | None = None,
    ) -> LLMResponse:
        """v2 native multi-turn path.

        Forwards `messages` verbatim to Ollama (system/user/assistant/tool roles).
        Supports per-call model override and Ollama's `format="json"` constraint
        used by the Tier 0 dispatcher.
        """
        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "options": {"temperature": temperature},
            "keep_alive": "30m",
            "stream": False,
        }
        if tools:
            kwargs["tools"] = tools
        if thinking:
            kwargs["think"] = True
        if format:
            kwargs["format"] = format

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

    Supports both the v1 `chat(system, user, ...)` and the v2
    `chat_messages(messages, ...)` APIs. Both methods pop from the same
    script queue. Each call is recorded in `calls` (v1 entries) or `calls_v2`
    (v2 entries) so tests can inspect what was sent.
    """

    def __init__(self, script: list[LLMResponse], model: str = "mock-model") -> None:
        self.script = list(script)
        self.model = model  # parity with OllamaClient.model for the CLI _prewarm code path
        self.calls: list[tuple[str, str]] = []
        # v2 — each call records the full args dict (messages, tools, model, …).
        self.calls_v2: list[dict[str, Any]] = []

    def _pop_with_corruption_retry(self) -> LLMResponse:
        last_resp: LLMResponse | None = None
        for attempt in (1, 2):
            if not self.script:
                raise RuntimeError("MockClient script exhausted.")
            last_resp = self.script.pop(0)
            if not (_looks_corrupted(last_resp.content) or _looks_corrupted(last_resp.thinking or "")):
                return last_resp
            if attempt == 1:
                pass  # retry: pop next scripted item
        assert last_resp is not None
        last_resp.corrupted = True
        return last_resp

    def chat(self, *, system: str, user: str, tools: list[dict[str, Any]],
             temperature: float, thinking: bool) -> LLMResponse:
        """v1 legacy path."""
        self.calls.append((system, user))
        return self._pop_with_corruption_retry()

    def chat_messages(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float,
        thinking: bool,
        model: str | None = None,
        format: str | None = None,
    ) -> LLMResponse:
        """v2 native multi-turn path."""
        self.calls_v2.append({
            "messages": list(messages),
            "tools": list(tools),
            "temperature": temperature,
            "thinking": thinking,
            "model": model,
            "format": format,
        })
        return self._pop_with_corruption_retry()
