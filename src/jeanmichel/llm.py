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

import contextlib
import logging
import queue
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .config import (
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    LLM_CALL_TIMEOUT_SECONDS,
    LLM_STALL_TIMEOUT_SECONDS,
    LLM_STREAM_DIR,
    OLLAMA_KEEP_ALIVE,
    model_context_window,
)
from .models import LLMResponse, ToolCall

_log = logging.getLogger(__name__)


def _maybe_evict_on_switch(client: Any, eff_model: str) -> None:
    """Single-model-in-VRAM policy. We chain different models within one turn
    (dispatch → router → analyst → coder) ; keeping each resident piles up VRAM.
    So when the client switches to a DIFFERENT model than the one it last used,
    unload the previous one first (same model in a row → no churn). Reloads on
    demand — `keep_alive` is short anyway. Used by OllamaClient AND MockClient so
    the behaviour is identical and testable."""
    last = getattr(client, "_last_model", None)
    if last and last != eff_model:
        client.unload(last)
    client._last_model = eff_model

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


_STREAM_DONE = object()  # sentinel : producer finished the stream cleanly


def _chunk_field(chunk: Any, *path: str, default: Any = None) -> Any:
    """Dig `path` out of a streamed chunk that may be a dict OR a pydantic object."""
    cur = chunk
    for key in path:
        if cur is None:
            return default
        cur = cur.get(key) if isinstance(cur, dict) else getattr(cur, key, None)
    return cur if cur is not None else default


def _accumulate_chunk(acc: dict[str, Any], chunk: Any) -> str:
    """Fold one streamed ChatResponse chunk into `acc`. Returns the visible text delta
    (content + thinking) so the caller can tee it to the debug sink and gauge progress."""
    content = _chunk_field(chunk, "message", "content", default="") or ""
    thinking = _chunk_field(chunk, "message", "thinking", default="") or ""
    acc["content"] += content
    acc["thinking"] += thinking
    for tc in (_chunk_field(chunk, "message", "tool_calls", default=[]) or []):
        fn = tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", None)
        if fn is None:
            continue
        name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", "")
        args = fn.get("arguments") if isinstance(fn, dict) else getattr(fn, "arguments", {})
        acc["tool_calls"].append(ToolCall(name=name, arguments=dict(args or {})))
    # token usage lands on the terminal chunk
    pe = _chunk_field(chunk, "prompt_eval_count", default=0) or 0
    ev = _chunk_field(chunk, "eval_count", default=0) or 0
    if pe:
        acc["prompt_eval"] = pe
    if ev:
        acc["eval"] = ev
    return thinking + content


def _resolve_stream_dir(conv_folder: Any) -> Path | None:
    """Where to dump the streamed 'slop'. Prefer the CONVERSATION folder
    (`<conv>/llm_streams/`) so each session keeps its own trace for later debugging ;
    fall back to the global `LLM_STREAM_DIR` for callers without a conv. None = disabled."""
    if conv_folder:
        return Path(conv_folder) / "llm_streams"
    if LLM_STREAM_DIR and str(LLM_STREAM_DIR).strip().lower() not in ("", "none", "off"):
        return Path(LLM_STREAM_DIR)
    return None


def _open_stream_sink(target_dir: Path | None, label: str | None, model: str):
    """Open a per-call debug file capturing the raw streamed text (the 'slop'), for
    post-mortem of slow/looping generations. Best-effort — returns None if disabled or
    on any error (never breaks the call)."""
    if target_dir is None:
        return None
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%f")
        raw = label or model
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in raw)[:60]
        return open(target_dir / f"{ts}_{safe}.txt", "w", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        _log.warning("llm stream sink open failed: %s", exc)
        return None


class LLMTimeoutError(RuntimeError):
    """Raised when a streamed Ollama call stalls (no token for LLM_STALL_TIMEOUT_SECONDS)
    or exceeds the hard cap LLM_CALL_TIMEOUT_SECONDS."""


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
        stream_log_dir: Any = None,
        stream_log_label: str | None = None,
        on_token: Any = None,
    ) -> LLMResponse: ...

    def unload(self, model: str) -> None:
        """Free the model from (V)RAM. Best-effort, no-op for backends without one."""
        ...


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
        self._last_model: str | None = None  # last model loaded → evict on switch
        from ollama import Client
        self._client = Client(host=host)

    def unload(self, model: str) -> None:
        """Ask Ollama to evict ``model`` from (V)RAM now (keep_alive=0), instead of
        letting it sit resident for the keep_alive window. Used to free big chained
        models the moment a specialist finishes. Best-effort : never raises."""
        try:
            self._client.generate(model=model, keep_alive=0)
        except Exception as exc:  # noqa: BLE001
            _log.warning("ollama unload(%s) failed: %s", model, exc)

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
        stream_log_dir: Any = None,
        stream_log_label: str | None = None,
        on_token: Any = None,
    ) -> LLMResponse:
        """v2 native multi-turn path — STREAMED.

        Forwards `messages` verbatim to Ollama. We stream so that (a) we can detect a
        STALL (no token for LLM_STALL_TIMEOUT_SECONDS = the model is hung) without
        guessing a total timeout, (b) we tee the raw output to a debug file, and (c) an
        abandoned call frees OUR thread immediately (we stop pulling) even though Ollama
        keeps generating (upstream bug : no cancel on disconnect). Per-call model
        override + Ollama `format="json"` (Tier 0 dispatcher) supported.
        """
        eff_model = model or self.model
        # Single-model-in-VRAM : free the previous model when switching to a different
        # one (before loading the new) so they don't pile up.
        _maybe_evict_on_switch(self, eff_model)
        base: dict[str, Any] = {
            "model": eff_model,
            "messages": messages,
            # Pin num_ctx to the window WE budget against. Ollama 0.24 otherwise
            # defaults context by VRAM (≥48 GiB → 256K), which made qwen3-coder eat
            # ~45 GB of KV cache. Ollama clamps this to the model's own max.
            "options": {"temperature": temperature, "num_ctx": model_context_window(eff_model)},
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "stream": True,
        }
        if tools:
            base["tools"] = tools
        if format:
            base["format"] = format

        sink_dir = _resolve_stream_dir(stream_log_dir)
        think_enabled = thinking
        last_resp: LLMResponse | None = None
        for attempt in (1, 2):
            kwargs = dict(base)
            if think_enabled:
                kwargs["think"] = True
            try:
                last_resp = self._consume_stream(kwargs, eff_model, sink_dir, stream_log_label, on_token)
            except LLMTimeoutError:
                raise
            except Exception as exc:
                # Coder models without a reasoning channel reject Ollama's `think`
                # with HTTP 400 ("does not support thinking"). Drop it + retry once.
                if think_enabled and "does not support thinking" in str(exc).lower():
                    _log.warning("model %r does not support thinking; retrying without it", eff_model)
                    think_enabled = False
                    last_resp = self._consume_stream(dict(base), eff_model, sink_dir, stream_log_label, on_token)
                else:
                    raise
            if not (_looks_corrupted(last_resp.content) or _looks_corrupted(last_resp.thinking or "")):
                return last_resp
            if attempt == 1:
                _log.warning("LLM output looks corrupted (attempt 1), retrying once")
        assert last_resp is not None
        last_resp.corrupted = True
        return last_resp

    def _consume_stream(
        self, kwargs: dict[str, Any], eff_model: str, sink_dir: Path | None,
        label: str | None, on_token: Any = None,
    ) -> LLMResponse:
        """Drive one streamed chat. A producer thread iterates the stream onto a queue ;
        this thread pulls chunks with a STALL timeout (queue.Empty = no token for too
        long → hung). Also enforces a generous hard cap. On either, request an unload
        (best-effort GPU free) and raise LLMTimeoutError — our thread is freed
        immediately whether or not Ollama keeps going. Each chunk's text is teed to the
        debug sink (the 'slop') so loops/stalls are visible after the fact."""
        q: queue.Queue = queue.Queue()

        def _produce() -> None:
            try:
                for chunk in self._client.chat(**kwargs):  # stream=True → iterator
                    q.put(("c", chunk))
                q.put((_STREAM_DONE, None))
            except Exception as exc:  # noqa: BLE001
                q.put(("e", exc))

        threading.Thread(target=_produce, name="ollama-stream", daemon=True).start()

        acc: dict[str, Any] = {"content": "", "thinking": "", "tool_calls": [], "prompt_eval": 0, "eval": 0}
        sink = _open_stream_sink(sink_dir, label, eff_model)
        start = time.monotonic()
        try:
            while True:
                try:
                    kind, payload = q.get(timeout=LLM_STALL_TIMEOUT_SECONDS)
                except queue.Empty:
                    self.unload(eff_model)  # best-effort GPU free (Ollama may keep going)
                    raise LLMTimeoutError(
                        f"Ollama stalled: no token for {LLM_STALL_TIMEOUT_SECONDS}s "
                        f"(model {eff_model}); aborted and requested unload."
                    ) from None
                if kind == "e":
                    raise payload
                if kind is _STREAM_DONE:
                    break
                delta = _accumulate_chunk(acc, payload)
                if delta and on_token is not None:
                    with contextlib.suppress(Exception):
                        on_token(delta)  # live → UI (best-effort, never breaks the call)
                if sink is not None and delta:
                    with contextlib.suppress(Exception):
                        sink.write(delta)
                        sink.flush()
                if time.monotonic() - start > LLM_CALL_TIMEOUT_SECONDS:
                    self.unload(eff_model)
                    raise LLMTimeoutError(
                        f"Ollama exceeded hard cap {LLM_CALL_TIMEOUT_SECONDS}s (model {eff_model})."
                    ) from None
        finally:
            if sink is not None:
                with contextlib.suppress(Exception):
                    sink.close()
        return LLMResponse(
            thinking=acc["thinking"],
            content=acc["content"],
            tool_calls=acc["tool_calls"],
            prompt_eval_count=int(acc["prompt_eval"]),
            eval_count=int(acc["eval"]),
        )


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
        self.unloaded: list[str] = []  # models asked to unload (tests inspect this)
        self._last_model: str | None = None  # mirrors OllamaClient eviction-on-switch

    def unload(self, model: str) -> None:
        """Record an unload request ; no real model to evict in tests."""
        self.unloaded.append(model)

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
        stream_log_dir: Any = None,
        stream_log_label: str | None = None,
        on_token: Any = None,
    ) -> LLMResponse:
        """v2 native multi-turn path."""
        _maybe_evict_on_switch(self, model or self.model)
        self.calls_v2.append({
            "messages": list(messages),
            "tools": list(tools),
            "temperature": temperature,
            "thinking": thinking,
            "model": model,
            "format": format,
        })
        return self._pop_with_corruption_retry()
