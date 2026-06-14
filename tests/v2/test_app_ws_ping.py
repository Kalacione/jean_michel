"""WS keepalive config.

The turn runs in a worker THREAD that holds the GIL for long CPU stretches while a
big local model streams for minutes — that starves the asyncio loop, so uvicorn
can't service the keepalive ping and closes the WS with 1011 "keepalive ping
timeout", losing the terminal {final} (frozen spinner). Bumping the timeout only
moved the cliff (a 74s turn still killed a neighbour WS in the logs). So the server
keepalive ping is DISABLED by default ; the turn has its own wall-clock cap and dead
clients surface when send_json fails. Still env-tunable to re-enable.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("argon2")
pytest.importorskip("itsdangerous")

from jeanmichel.api.app import (  # noqa: E402
    DEFAULT_WS_PING_INTERVAL,
    DEFAULT_WS_PING_TIMEOUT,
    ws_ping_setting,
)


def test_ws_keepalive_disabled_by_default():
    # None = no server-initiated pings → a GIL-starved long turn can't be killed by
    # a keepalive timeout (the 1011 hang).
    assert DEFAULT_WS_PING_INTERVAL is None
    assert DEFAULT_WS_PING_TIMEOUT is None


def test_ws_ping_setting_parsing():
    assert ws_ping_setting(None, 130.0) == 130.0   # unset → default
    assert ws_ping_setting(None, None) is None      # unset → default (disabled)
    assert ws_ping_setting("45", None) == 45.0      # explicit float re-enables
    assert ws_ping_setting("none", 130.0) is None  # disabled
    assert ws_ping_setting("0", 130.0) is None
    assert ws_ping_setting("", 130.0) is None
