"""Single-global-turn executor + sync->async event bridge for the turn WebSocket.

The orchestrator turn (``turn_runner.run_turn``) is synchronous and can run for
minutes. It executes in a worker thread ; its ``event_emitter`` callback pushes
event dicts onto an ``asyncio.Queue`` (via ``call_soon_threadsafe``), which the
async WebSocket handler drains and forwards. A single global lock enforces one
active turn at a time (Ollama = one GPU) — a concurrent request gets ``{queued}``
then waits its turn (the lock is FIFO-fair).

``ask_human`` is NOT wired here yet (that's S4) : turns run with
``ask_human_callback=None``, so the orchestrator declines clarification requests
and proceeds with what it has.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .. import persistence
from ..llm import OllamaClient
from ..service import turn_runner

# One turn at a time across the whole daemon. asyncio.Lock binds to the running
# loop lazily on first use, so a module-level instance is safe.
turn_lock = asyncio.Lock()

_SENTINEL = object()

_llm_clients: tuple[Any, Any] | None = None


def get_llm_clients() -> tuple[Any, Any]:
    """Return cached ``(dispatch_llm, main_llm)``. Monkeypatched in tests."""
    global _llm_clients
    if _llm_clients is None:
        from ..config import DISPATCH_MODEL, MAIN_MODEL

        _llm_clients = (
            OllamaClient(model=DISPATCH_MODEL),
            OllamaClient(model=MAIN_MODEL),
        )
    return _llm_clients


async def run_turn_streaming(
    websocket: Any,
    *,
    conv_id: str,
    folder: Path,
    user_text: str,
    mode: str,
    profile: Any,
    dispatch_llm: Any,
    main_llm: Any,
) -> None:
    """Run one turn in a worker thread, streaming its events to the WebSocket.

    Emits ``{type:dispatch}`` once the Tier-0 decision is known, ``{type:event}``
    per orchestrator event, then a terminal ``{type:final}`` (or ``{type:error}``).
    Events are also persisted to ``events.jsonl`` by the orchestrator, so the
    stream is replayable via the REST events endpoint.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def emit(event: Any) -> None:
        loop.call_soon_threadsafe(
            queue.put_nowait, {"type": "event", "event": event.to_dict()}
        )

    def on_dispatch(decision: Any) -> None:
        loop.call_soon_threadsafe(
            queue.put_nowait,
            {
                "type": "dispatch",
                "intent": decision.intent,
                "tool": decision.tool,
                "confidence": decision.confidence,
            },
        )

    initial_messages = persistence.load_messages(folder)

    def worker() -> None:
        try:
            answer = turn_runner.run_turn(
                user_text=user_text,
                conv_folder=folder,
                conv_id=conv_id,
                mode=mode,
                dispatch_llm=dispatch_llm,
                main_llm=main_llm,
                profile=profile,
                initial_messages=initial_messages,
                event_emitter=emit,
                ask_human_callback=None,  # S4 wires the WS round-trip
                on_dispatch=on_dispatch,
            )
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "final", "answer": answer})
        except Exception as exc:  # noqa: BLE001
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "detail": str(exc)})
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)

    turn_future = loop.run_in_executor(None, worker)
    try:
        while True:
            msg = await queue.get()
            if msg is _SENTINEL:
                break
            await websocket.send_json(msg)
    finally:
        # Let the worker finish (it persists messages/state/events) even if the
        # client vanished mid-turn.
        await turn_future
