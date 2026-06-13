"""Per-user push notifications over a dedicated WebSocket (`/ws/notifications`).

A small, reusable channel to push asynchronous events to a logged-in user's open
app — distinct from the per-conversation turn socket (`/ws/conversations/{id}`),
which only sends during a streaming turn. First use: telling the GUI a project's
sandbox Docker image finished building (or failed), since that build runs in a
background thread after the project is saved.

In-memory registry (single-worker daemon). Sends are scheduled onto the asyncio
event loop via ``run_coroutine_threadsafe`` so background threads (the image build)
can notify safely. If the user has no open socket, the notification is dropped
(best-effort) — the lazy build in ``repo_exec`` rebuilds the image on next use.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

_log = logging.getLogger(__name__)

_conns: dict[int, set[Any]] = {}          # user_id → set[WebSocket]
_loop: asyncio.AbstractEventLoop | None = None


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Capture the serving event loop (called from the app lifespan startup)."""
    global _loop
    _loop = loop


def register(user_id: int, ws: Any) -> None:
    _conns.setdefault(user_id, set()).add(ws)


def unregister(user_id: int, ws: Any) -> None:
    socks = _conns.get(user_id)
    if socks:
        socks.discard(ws)
        if not socks:
            _conns.pop(user_id, None)


def notify(user_id: int, payload: dict) -> None:
    """Push ``payload`` (JSON-serialisable) to every open socket of ``user_id``.

    Thread-safe: schedules the sends on the captured event loop. No-op when the
    loop isn't set or the user has no open socket. Never raises."""
    loop = _loop
    if loop is None:
        return
    for ws in list(_conns.get(user_id, ())):
        try:
            asyncio.run_coroutine_threadsafe(_safe_send(user_id, ws, payload), loop)
        except RuntimeError as exc:  # loop not running
            _log.debug("notify scheduling failed: %s", exc)


async def _safe_send(user_id: int, ws: Any, payload: dict) -> None:
    try:
        await ws.send_json(payload)
    except Exception as exc:  # noqa: BLE001 — a dead socket must not break a build
        _log.debug("notify send failed (dropping socket): %s", exc)
        unregister(user_id, ws)
