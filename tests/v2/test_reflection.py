"""Background reflection daemon (sleep-time consolidation) — the _reflect_cycle logic.

Unit-tests the cycle with the heavy bits injected (turn_lock, get_llm_clients, run_shadow,
notify, the due-list) — no real LLM, no DB, no event loop fixture (asyncio.run)."""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("fastapi")  # api.reflection → api.notifications may pull fastapi

from jeanmichel.api import reflection  # noqa: E402
from jeanmichel.service import consolidation  # noqa: E402


class _FakeLock:
    """Stand-in for executor.turn_lock : controllable .locked() + async context."""

    def __init__(self, locked: bool) -> None:
        self._locked = locked

    def locked(self) -> bool:
        return self._locked

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


def test_reflect_cycle_skips_when_turn_active(monkeypatch):
    """A turn is running → the cycle bails out before enumerating/spending the GPU."""
    monkeypatch.setattr(reflection.executor, "turn_lock", _FakeLock(locked=True))

    def _boom(_limit):
        raise AssertionError("must not enumerate while a turn is active")

    monkeypatch.setattr(reflection, "_due_conversations", _boom)
    asyncio.run(reflection._reflect_cycle())  # returns immediately, no AssertionError


def test_reflect_cycle_studies_due_conv_advances_watermark_and_notifies(monkeypatch, tmp_path):
    folder = tmp_path / "conv"
    folder.mkdir()
    (folder / "messages.json").write_text(
        json.dumps([{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(reflection.executor, "turn_lock", _FakeLock(locked=False))
    monkeypatch.setattr(reflection.executor, "get_llm_clients", lambda: (None, object()))
    monkeypatch.setattr(reflection, "_due_conversations", lambda _limit: [("c1", folder, 7)])

    ran = {}

    def fake_run_shadow(f, c, **_k):
        ran["conv"] = c
        cands = [{"scope": "user", "code": "a", "title": "t", "description": "d", "content": "c"}]
        consolidation.save_pending(f, cands)  # mimic run_shadow's add_pending stash
        return cands

    monkeypatch.setattr(reflection.consolidation_svc, "run_shadow", fake_run_shadow)
    notified: list = []
    monkeypatch.setattr(reflection.notifications, "notify", lambda uid, payload: notified.append((uid, payload)))

    asyncio.run(reflection._reflect_cycle())

    assert ran["conv"] == "c1"                              # the pass ran
    assert consolidation.reflection_due(folder, 2) is False  # watermark advanced to msg count (2)
    assert notified and notified[0][0] == 7                  # owner notified
    assert [c["code"] for c in notified[0][1]["candidates"]] == ["a"]  # merged pending pushed
