"""Background memory-reflection daemon (sleep-time consolidation).

A periodic asyncio task — registered in the API lifespan, NOT in the turn path — that
consolidates memory candidates from conversations OUTSIDE any turn, so it never consumes
a turn nor delays a response. Per cycle it:

- runs only when the system is IDLE : it serialises on ``executor.turn_lock`` (the GPU
  lock), so it never collides with a real turn, and yields the moment a turn becomes
  active (the lock is FIFO-fair → a waiting turn goes first) ;
- studies only conversations with NEW content since their last study (a per-conversation
  watermark, ``consolidation.reflection_due``) — a studied conversation that CONTINUES
  becomes due again, no permanent "done" state ;
- advances the watermark only AFTER a completed pass (``mark_studied``) so a conversation
  deferred because a turn arrived is retried next cycle.

Grounding / subjectivity are unchanged — this only changes WHEN ``run_shadow`` runs, not
WHAT it extracts (still grounded in user/tool content, still allowed to find nothing).

Pattern : "sleep-time agent" (cf. Letta). Assumes a single daemon process (one event
loop) ; multi-worker would need leader election (out of scope).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from .. import config, db, persistence
from ..events import MemoryConsolidationProposed
from ..service import consolidation as consolidation_svc
from . import executor, notifications

_log = logging.getLogger(__name__)


async def reflection_loop() -> None:
    """Periodic driver : every ``REFLECTION_INTERVAL_SECONDS``, run one cycle. Best-effort
    (a cycle failure is logged, the loop survives). Cancelled cleanly at app shutdown."""
    _log.info(
        "reflection daemon started (interval=%ds, max=%d conv/cycle)",
        config.REFLECTION_INTERVAL_SECONDS, config.REFLECTION_MAX_CONVS_PER_CYCLE,
    )
    try:
        while True:
            await asyncio.sleep(config.REFLECTION_INTERVAL_SECONDS)
            try:
                await _reflect_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — keep the daemon alive across failures
                _log.exception("reflection cycle failed: %s", exc)
    except asyncio.CancelledError:
        _log.info("reflection daemon stopped")
        raise


def _due_conversations(limit: int) -> list[tuple[str, Path, int]]:
    """``(conv_id, folder, owner_user_id)`` for conversations with NEW content since their
    last study, newest first, capped at ``limit``. Pure DB/disk reads (run off-loop)."""
    out: list[tuple[str, Path, int]] = []
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, folder_path FROM conversations ORDER BY modified_at DESC"
        ).fetchall()
        for row in rows:
            folder = Path(row["folder_path"])
            count = len(persistence.load_messages(folder))
            if count == 0 or not consolidation_svc.reflection_due(folder, count):
                continue
            owner = conn.execute(
                "SELECT user_id FROM conversation_users WHERE conversation_id=? LIMIT 1",
                (row["id"],),
            ).fetchone()
            if owner is None:
                continue
            out.append((row["id"], folder, int(owner["user_id"])))
            if len(out) >= limit:
                break
    return out


async def _reflect_cycle() -> None:
    """One pass over due conversations, while idle. Serialised on ``turn_lock`` ; stops
    the cycle as soon as a real turn becomes active (yields the GPU)."""
    if executor.turn_lock.locked():
        _log.debug("reflection cycle skipped : a turn is active")
        return
    due = await asyncio.to_thread(_due_conversations, config.REFLECTION_MAX_CONVS_PER_CYCLE)
    if not due:
        return
    _, main_llm = executor.get_llm_clients()
    loop = asyncio.get_running_loop()
    studied = 0
    for conv_id, folder, owner in due:
        if executor.turn_lock.locked():  # a real turn arrived → yield, retry the rest next cycle
            _log.info("reflection deferring (turn active) after %d conv(s)", studied)
            break
        async with executor.turn_lock:
            count = len(persistence.load_messages(folder))  # authoritative under the lock
            t0 = time.monotonic()
            cands = await loop.run_in_executor(
                None,
                lambda f=folder, c=conv_id, o=owner: consolidation_svc.run_shadow(
                    f, c, llm=main_llm, user_id=o
                ),
            )
            dur = time.monotonic() - t0
            consolidation_svc.mark_studied(folder, count)  # transactional : the pass completed
        studied += 1  # noqa: SIM113 — counts PROCESSED convs (post-lock), not the loop index
        _log.info("reflection pass conv=%s dur=%.1fs candidates=%d", conv_id, dur, len(cands or []))
        if cands:
            # Surface the FULL accumulated awaiting-review set (matches GET /pending-memory).
            pending = consolidation_svc.load_pending(folder)
            persistence.append_event(
                folder, MemoryConsolidationProposed(count=len(pending), candidates=pending)
            )
            notifications.notify(owner, {
                "type": "notification", "kind": "memory_proposed",
                "conv_id": conv_id, "count": len(pending), "candidates": pending,
            })
