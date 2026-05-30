"""Single-global-turn executor + sync<->async bridge for the turn WebSocket.

The orchestrator turn (``turn_runner.run_turn``) is synchronous and can run for
minutes. It executes in a worker thread ; its ``event_emitter`` callback pushes
event dicts onto an ``asyncio.Queue`` (via ``call_soon_threadsafe``), which the
async handler drains and forwards. A single global lock enforces one active turn
at a time (Ollama = one GPU) — a concurrent request gets ``{queued}`` then waits
(the lock is FIFO-fair).

``ask_human`` round-trip (S4) : the worker-thread callback pushes
``{type:ask_human}`` to the client, then BLOCKS on a thread-safe ``answer_box``
until a ``{type:answer}`` frame arrives (received concurrently by ``recv_answers``)
or ``ASK_HUMAN_TIMEOUT_SECONDS`` elapses — in which case the orchestrator proceeds
with what it has.
"""

from __future__ import annotations

import asyncio
import contextlib
import queue
from pathlib import Path
from typing import Any

from .. import config, persistence
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
    """Run one turn in a worker thread, streaming events ; handle ask_human.

    Emits ``{dispatch}``, then ``{event}`` per orchestrator event (incl.
    ``AgentThinking``) and ``{ask_human}`` when the agent needs input, then a
    terminal ``{final}`` (or ``{error}``). Events are also persisted to
    ``events.jsonl`` (replayable via REST).
    """
    loop = asyncio.get_running_loop()
    event_queue: asyncio.Queue = asyncio.Queue()
    answer_box: queue.Queue = queue.Queue()

    def emit(event: Any) -> None:
        loop.call_soon_threadsafe(
            event_queue.put_nowait, {"type": "event", "event": event.to_dict()}
        )

    def on_dispatch(decision: Any) -> None:
        loop.call_soon_threadsafe(
            event_queue.put_nowait,
            {
                "type": "dispatch",
                "intent": decision.intent,
                "tool": decision.tool,
                "confidence": decision.confidence,
            },
        )

    def ask_human(question: str, why: str) -> str:
        # Runs in the worker thread : push the prompt, then block until the
        # client answers (received by recv_answers) or the timeout fires.
        loop.call_soon_threadsafe(
            event_queue.put_nowait,
            {"type": "ask_human", "question": question, "why": why},
        )
        try:
            return answer_box.get(timeout=config.ASK_HUMAN_TIMEOUT_SECONDS)
        except queue.Empty:
            return "(no answer received from the user within the time limit)"

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
                ask_human_callback=ask_human,
                on_dispatch=on_dispatch,
            )
            loop.call_soon_threadsafe(event_queue.put_nowait, {"type": "final", "answer": answer})
        except Exception as exc:  # noqa: BLE001
            loop.call_soon_threadsafe(event_queue.put_nowait, {"type": "error", "detail": str(exc)})
        finally:
            loop.call_soon_threadsafe(event_queue.put_nowait, _SENTINEL)

    async def recv_answers() -> None:
        # Concurrently consume client frames while the turn runs, routing
        # {answer} to the blocked ask_human callback.
        try:
            while True:
                data = await websocket.receive_json()
                if data.get("type") == "answer":
                    answer_box.put(data.get("text", ""))
                # other messages are ignored mid-turn
        except Exception:  # noqa: BLE001 — disconnect / bad frame ends receiving
            answer_box.put("")  # unblock a pending ask_human so the turn finishes

    turn_future = loop.run_in_executor(None, worker)
    recv_task = asyncio.create_task(recv_answers())
    try:
        while True:
            msg = await event_queue.get()
            if msg is _SENTINEL:
                break
            await websocket.send_json(msg)
    finally:
        recv_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await recv_task
        # Let the worker finish (persists messages/state/events) even if the
        # client vanished mid-turn.
        await turn_future
