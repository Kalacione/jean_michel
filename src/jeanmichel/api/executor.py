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


_REFLECT_TASKS: set[asyncio.Task] = set()


def _schedule_reflection(conv_id: str, folder: Path, user_id: int | None, llm: Any) -> None:
    """Fire the end-of-turn reflection beat as a background task (best-effort). No-op if
    disabled or no owner. Held in a module set so it isn't GC'd mid-flight."""
    if not config.REFLECTION_ENABLED or user_id is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_reflect_after_turn(conv_id, folder, user_id, llm))
    _REFLECT_TASKS.add(task)
    task.add_done_callback(_REFLECT_TASKS.discard)


async def _reflect_after_turn(conv_id: str, folder: Path, user_id: int, llm: Any) -> None:
    """Propose durable memory/paradigm candidates from the fresh exchange (cheap model),
    serialised on ``turn_lock`` (runs when idle). Surfaces the pending set via the per-user
    notifications WS + a persisted event. Never raises."""
    try:
        async with turn_lock:
            cands = await asyncio.to_thread(
                consolidation_svc.run_shadow, folder, conv_id,
                llm=llm, user_id=user_id, model=config.REFLECTION_MODEL,
            )
        if not cands:
            return
        pending = await asyncio.to_thread(consolidation_svc.load_pending, conv_id)
        persistence.append_event(
            folder, MemoryConsolidationProposed(count=len(pending), candidates=pending)
        )
        notifications.notify(user_id, {
            "type": "notification", "kind": "memory_proposed",
            "conv_id": conv_id, "count": len(pending), "candidates": pending,
        })
    except Exception as exc:  # noqa: BLE001 — best-effort ; the turn already succeeded
        _log.debug("post-turn reflection failed (conv=%s): %s", conv_id, exc)


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

    # NB : the reflection beat (memory/paradigm candidate proposal) fires AFTER the turn,
    # in the finally below — decoupled from 'final' so it never delays the response.

    turn_future = loop.run_in_executor(None, worker)
    recv_task = asyncio.create_task(recv_answers())
    exit_reason = "sentinel"
    client_gone = False
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
            except Exception as exc:  # noqa: BLE001 — client vanished mid-turn (e.g. switched conv)
                # An EXPECTED disconnect, not a server error : stop draining to the dead socket
                # and let the `finally` persist the turn. {final} is lost on this socket → the
                # turn_complete notice below lets the GUI catch up. (Re-raising spammed an ASGI
                # traceback for a perfectly normal mid-turn navigation.)
                exit_reason = f"client_gone({type(exc).__name__})"
                client_gone = True
                _log.info("drain stop : client gone conv=%s (%s) — turn still persists",
                          conv_id, type(exc).__name__)
                break
    finally:
        _log.info("drain EXIT reason=%s conv=%s", exit_reason, conv_id)
        recv_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await recv_task
        # Let the worker finish (persists messages/state/events) even if the
        # client vanished mid-turn.
        await turn_future
        # Client disconnected before {final} (typically : switched conversations mid-turn). Its
        # view is a pre-persist reload that will miss this turn → push a per-user notice so the
        # GUI reloads this conv if it's the one on screen, instead of a blank panel that only a
        # manual page refresh fixes. No-op if the user has no notifications socket open.
        if client_gone and memory_user_id is not None:
            notifications.notify(memory_user_id, {
                "type": "notification", "kind": "turn_complete", "conv_id": conv_id,
            })
        # Reflection beat : end of a DEEP turn (live ; not a timer). Best-effort, serialised
        # on turn_lock → runs when idle. Candidates land in pending_consolidation for review.
        if was_deep["v"]:
            _schedule_reflection(conv_id, folder, memory_user_id, main_llm)
