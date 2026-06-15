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
import logging
import queue
import threading
from pathlib import Path
from typing import Any

from .. import config, persistence, voice
from ..events import MemoryConsolidationProposed
from ..llm import OllamaClient
from ..service import consolidation as consolidation_svc
from ..service import turn_runner
from . import notifications

_log = logging.getLogger(__name__)

# One turn at a time across the whole daemon. asyncio.Lock binds to the running
# loop lazily on first use, so a module-level instance is safe.
turn_lock = asyncio.Lock()

_SENTINEL = object()

# Strong refs to fire-and-forget background tasks (post-turn shadow consolidation),
# so the event loop doesn't GC them mid-flight. Discarded on completion.
_bg_tasks: set[Any] = set()

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
    memory_user_id: int | None = None,
    attachments: list[str] | None = None,
    plan_mode: bool = False,
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
    cancel_event = threading.Event()  # set by a {type:"stop"} frame → aborts the turn

    def emit(event: Any) -> None:
        msg: dict[str, Any] = {"type": "event", "event": event.to_dict()}
        # Vocal mode : annotate with the announcement phrase (reusing the CLI's
        # event->phrase map) so the browser can voice progress without
        # duplicating it client-side. It fetches the audio via GET /api/tts.
        if mode == "vocal":
            phrase = voice.phrase_for_event(event)
            if phrase:
                msg["speak"] = phrase
        loop.call_soon_threadsafe(event_queue.put_nowait, msg)

    was_deep = {"v": False}

    def on_dispatch(decision: Any) -> None:
        was_deep["v"] = decision.intent != "alexa"
        loop.call_soon_threadsafe(
            event_queue.put_nowait,
            {
                "type": "dispatch",
                "intent": decision.intent,
                "tool": decision.tool,
                "confidence": decision.confidence,
            },
        )

    def ask_human(question: str, why: str, choices: list[str], multi: bool) -> str:
        # Runs in the worker thread : push the prompt, then block until the
        # client answers (received by recv_answers) or the timeout fires.
        # `choices`/`multi` are an INPUT affordance — the answer still rides back
        # as plain text via {type:"answer", text}.
        loop.call_soon_threadsafe(
            event_queue.put_nowait,
            {"type": "ask_human", "question": question, "why": why,
             "choices": choices, "multi": multi},
        )
        try:
            return answer_box.get(timeout=config.ASK_HUMAN_TIMEOUT_SECONDS)
        except queue.Empty:
            return "(no answer received from the user within the time limit)"

    initial_messages = persistence.load_messages(folder)

    def worker() -> None:
        _log.info("turn worker START conv=%s mode=%s plan_mode=%s", conv_id, mode, plan_mode)
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
                memory_user_id=memory_user_id,
                attachments=attachments,
                plan_mode=plan_mode,
                cancel_event=cancel_event,
            )
        except Exception as exc:  # noqa: BLE001
            _log.exception("turn worker EXCEPTION conv=%s : %s", conv_id, exc)
            loop.call_soon_threadsafe(event_queue.put_nowait, {"type": "error", "detail": str(exc)})
        else:
            _log.info("turn worker run_turn RETURNED conv=%s answer_len=%d → queue final",
                      conv_id, len(answer or ""))
            # 'final' is emitted as soon as the answer is ready — the turn is now truly
            # done (drain → sentinel → lock released). Shadow consolidation is DECOUPLED
            # to a background task AFTER the turn (see below) so it never delays 'final'
            # nor holds the turn (which would drop an Approve/next-turn frame).
            loop.call_soon_threadsafe(event_queue.put_nowait, {"type": "final", "answer": answer})
        finally:
            _log.info("turn worker END conv=%s → queue sentinel", conv_id)
            loop.call_soon_threadsafe(event_queue.put_nowait, _SENTINEL)

    async def recv_answers() -> None:
        # Concurrently consume client frames while the turn runs, routing
        # {answer} to the blocked ask_human callback.
        try:
            while True:
                data = await websocket.receive_json()
                kind = data.get("type")
                if kind == "answer":
                    answer_box.put(data.get("text", ""))
                elif kind == "stop":
                    # User hit Stop : signal the worker thread to abort at its next
                    # checkpoint / mid-stream, and unblock a pending ask_human so the
                    # loop can reach that checkpoint. The turn then concludes via {final}.
                    _log.info("recv_answers STOP requested conv=%s", conv_id)
                    cancel_event.set()
                    answer_box.put("")
                else:
                    # NB : non-answer frames (e.g. a {turn} sent mid-turn) are dropped here.
                    _log.warning("recv_answers DROPPED frame type=%r mid-turn conv=%s", kind, conv_id)
        except Exception as exc:  # noqa: BLE001 — disconnect / bad frame ends receiving
            _log.info("recv_answers ended conv=%s : %s", conv_id, type(exc).__name__)
            answer_box.put("")  # unblock a pending ask_human so the turn finishes

    async def _consolidate_bg() -> None:
        # Shadow consolidation DECOUPLED from the turn : runs after the turn released
        # the WS + lock, so it never delays 'final' nor holds the receiver (which would
        # drop an Approve/next-turn frame). Serialized via turn_lock (GPU) ; best-effort.
        # run_shadow stashes to pending_memory.json ; we also push the candidates live
        # over the per-user notifications WS (the turn WS is already closed).
        try:
            async with turn_lock:
                cands = await loop.run_in_executor(
                    None,
                    lambda: consolidation_svc.run_shadow(
                        folder, conv_id, llm=main_llm, user_id=memory_user_id
                    ),
                )
            _log.info("shadow consolidation (bg) DONE conv=%s candidates=%d", conv_id, len(cands or []))
            if cands:
                persistence.append_event(
                    folder, MemoryConsolidationProposed(count=len(cands), candidates=cands)
                )
                if memory_user_id is not None:
                    notifications.notify(memory_user_id, {
                        "type": "notification", "kind": "memory_proposed",
                        "conv_id": conv_id, "count": len(cands), "candidates": cands,
                    })
        except Exception as exc:  # noqa: BLE001 — best-effort ; the turn already succeeded
            _log.exception("shadow consolidation (bg) FAILED conv=%s : %s", conv_id, exc)

    turn_future = loop.run_in_executor(None, worker)
    recv_task = asyncio.create_task(recv_answers())
    exit_reason = "sentinel"
    try:
        while True:
            msg = await event_queue.get()
            if msg is _SENTINEL:
                break
            mtype = msg.get("type")
            if mtype != "event":
                _log.info("drain SEND type=%s conv=%s", mtype, conv_id)
            try:
                await websocket.send_json(msg)
            except Exception as exc:  # noqa: BLE001
                exit_reason = f"send_failed({type(exc).__name__})"
                _log.warning("drain send_json FAILED type=%s conv=%s : %s — final may be lost",
                             mtype, conv_id, exc)
                raise
    finally:
        _log.info("drain EXIT reason=%s conv=%s", exit_reason, conv_id)
        recv_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await recv_task
        # Let the worker finish (persists messages/state/events) even if the
        # client vanished mid-turn.
        await turn_future
        # Turn fully done (final + sentinel sent, lock about to release) → kick off
        # shadow consolidation in the background (deep turns only). It acquires turn_lock
        # itself once released, so the next user turn is never blocked/dropped by it.
        if was_deep["v"]:
            task = asyncio.create_task(_consolidate_bg())
            _bg_tasks.add(task)
            task.add_done_callback(_bg_tasks.discard)
