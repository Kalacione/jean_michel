"""WS keepalive config (VRAM/résilience firefight, Fix C).

A turn can keep the connection quiet (or briefly starve the loop with GIL-bound CPU
work) for up to a full LLM call. uvicorn's default ws_ping_timeout (20s) then drops
the live stream mid-turn with 1011. We raise it past the LLM timeout and make it env
-tunable.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("argon2")
pytest.importorskip("itsdangerous")

from jeanmichel.api.app import DEFAULT_WS_PING_TIMEOUT, ws_ping_setting  # noqa: E402
from jeanmichel.config import LLM_CALL_TIMEOUT_SECONDS  # noqa: E402


def test_ws_ping_timeout_exceeds_llm_call_timeout():
    # A single slow LLM call (up to LLM_CALL_TIMEOUT) must not trip the WS keepalive.
    assert DEFAULT_WS_PING_TIMEOUT > LLM_CALL_TIMEOUT_SECONDS


def test_ws_ping_setting_parsing():
    assert ws_ping_setting(None, 130.0) == 130.0   # unset → default
    assert ws_ping_setting("45", 130.0) == 45.0    # explicit float
    assert ws_ping_setting("none", 130.0) is None  # disabled
    assert ws_ping_setting("0", 130.0) is None
    assert ws_ping_setting("", 130.0) is None
