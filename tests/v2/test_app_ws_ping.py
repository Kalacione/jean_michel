"""WS keepalive config (VRAM/résilience firefight, Fix C).

The turn runs in a worker thread (off the event loop), so the keepalive only has to
tolerate brief loop stalls (GIL-bound bursts : persistence, token estimation) and
throttled background tabs — NOT the full LLM call. uvicorn's default ws_ping_timeout
(20s) is too aggressive and dropped the live stream mid-turn with 1011 ; we raise it
generously and make it env-tunable.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("argon2")
pytest.importorskip("itsdangerous")

from jeanmichel.api.app import DEFAULT_WS_PING_TIMEOUT, ws_ping_setting  # noqa: E402


def test_ws_ping_timeout_is_generous():
    # Well above uvicorn's aggressive 20s default so a GIL burst / throttled tab does
    # not drop the live stream. The LLM call itself runs off-loop, so the ping need NOT
    # exceed LLM_CALL_TIMEOUT_SECONDS.
    assert DEFAULT_WS_PING_TIMEOUT >= 120


def test_ws_ping_setting_parsing():
    assert ws_ping_setting(None, 130.0) == 130.0   # unset → default
    assert ws_ping_setting("45", 130.0) == 45.0    # explicit float
    assert ws_ping_setting("none", 130.0) is None  # disabled
    assert ws_ping_setting("0", 130.0) is None
    assert ws_ping_setting("", 130.0) is None
