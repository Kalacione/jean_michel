"""Offline tests for the generic OAuth MCP path — no live browser / no network.

Covers FileTokenStorage, the loopback callback server, the holder "no token →
skip (no browser)" guarantee, config parsing, the generic `instructions` nudge,
and authenticate() orchestration (SDK transport mocked). The live OAuth flow is
behind @mcp_live + JEANMICHEL_MCP_LIVE_OAUTH=1.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import stat
import urllib.request
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp.shared.auth")

from mcp.shared.auth import OAuthToken  # noqa: E402

from jeanmichel import config  # noqa: E402
from jeanmichel.mcp_client import (  # noqa: E402
    FileTokenStorage,
    MCPManager,
    _LoopbackCallback,
    _ServerCfg,
)


@pytest.fixture(autouse=True)
def _oauth_dir_tmp(tmp_path, monkeypatch):
    """Never touch the real ~/.jean-michel/mcp during tests."""
    monkeypatch.setattr(config, "MCP_OAUTH_DIR", tmp_path / "mcp")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---- FileTokenStorage ----------------------------------------------------


def test_token_storage_roundtrip_and_perms(tmp_path):
    st = FileTokenStorage(tmp_path / "notion")
    assert asyncio.run(st.get_tokens()) is None  # empty dir
    tok = OAuthToken(access_token="a", token_type="Bearer", refresh_token="r", expires_in=3600)
    asyncio.run(st.set_tokens(tok))
    got = asyncio.run(st.get_tokens())
    assert got.access_token == "a" and got.refresh_token == "r"
    f = tmp_path / "notion" / "tokens.json"
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert not list((tmp_path / "notion").glob(".*.tmp"))  # no temp left


def test_token_storage_corrupt_returns_none(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    (d / "tokens.json").write_text("{not json", encoding="utf-8")
    assert asyncio.run(FileTokenStorage(d).get_tokens()) is None


# ---- _LoopbackCallback ---------------------------------------------------


def test_callback_captures_code_state():
    port = _free_port()
    with _LoopbackCallback(port) as cb:
        body = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/callback?code=XYZ&state=ST", timeout=5
        ).read()
        assert b"close this tab" in body
        assert asyncio.run(cb.wait()) == ("XYZ", "ST")


def test_callback_error_raises():
    port = _free_port()
    with _LoopbackCallback(port) as cb:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?error=access_denied", timeout=5).read()
        with pytest.raises(RuntimeError):
            asyncio.run(cb.wait())


def test_callback_timeout(monkeypatch):
    monkeypatch.setattr(config, "MCP_OAUTH_CALLBACK_TIMEOUT", 0.2)
    with _LoopbackCallback(_free_port()) as cb, pytest.raises(TimeoutError):
        asyncio.run(cb.wait())


def test_callback_port_in_use():
    port = _free_port()
    held = _LoopbackCallback(port)
    held.__enter__()
    try:
        with pytest.raises(OSError):
            _LoopbackCallback(port).__enter__()
    finally:
        held.__exit__()


# ---- _ServerCfg parsing / helpers ----------------------------------------


def test_servercfg_oauth_and_backcompat():
    oauth = _ServerCfg(name="notion", url="https://x/mcp", category="notes", auth="oauth",
                       redirect_port=9001)
    assert oauth.is_oauth()
    assert oauth.redirect_uri() == "http://localhost:9001/callback"
    assert oauth.storage_dir() == config.MCP_OAUTH_DIR / "notion"
    bearer = _ServerCfg(name="gh", url="u", category="code", auth="bearer", auth_env="T")
    assert not bearer.is_oauth()


def test_instructions_appended_to_descriptions():
    mgr = MCPManager({}, {})
    cfg = _ServerCfg(name="s", url="u", category="c", instructions="USE WHEN ASKED")
    mgr._build_specs(cfg, [SimpleNamespace(name="do", description="Base.", inputSchema={"type": "object"})])
    desc = mgr._specs_by_server["s"][0].description
    assert "Base." in desc and "USE WHEN ASKED" in desc
    # No instructions → unchanged.
    mgr._build_specs(_ServerCfg(name="t", url="u", category="c"),
                     [SimpleNamespace(name="do", description="Plain.", inputSchema={})])
    assert mgr._specs_by_server["t"][0].description == "Plain."


# ---- Holder : OAuth + no token → skip (NO browser) -----------------------


def test_holder_oauth_no_token_skips_without_browser(monkeypatch):
    cfg = _ServerCfg(name="notion", url="https://x/mcp", category="notes", auth="oauth")
    mgr = MCPManager({"notion": cfg}, {"notes": ["jean-michel"]})
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: opened.append(a))

    async def _run():
        ready = asyncio.Event()
        await mgr._holder(cfg, ready)
        return ready.is_set()

    assert asyncio.run(_run()) is True          # holder returned (skipped)
    assert "notion" not in mgr._sessions          # never connected
    assert opened == []                            # NO browser


# ---- authenticate() orchestration (SDK transport mocked) -----------------


def test_authenticate_guards():
    mgr = MCPManager({"gh": _ServerCfg(name="gh", url="u", category="code", auth="bearer")}, {})
    assert mgr.authenticate("unknown") is False     # not configured
    assert mgr.authenticate("gh") is False          # not an OAuth server


def test_authenticate_success_persists_token(monkeypatch):
    monkeypatch.setattr(config, "MCP_OAUTH_DEFAULT_PORT", 0)  # ephemeral callback bind
    cfg = _ServerCfg(name="notion", url="https://x/mcp", category="notes", auth="oauth", scopes="s")
    mgr = MCPManager({"notion": cfg}, {"notes": ["jean-michel"]})

    @contextlib.asynccontextmanager
    async def _fake_shc(url, headers=None, auth=None, **kw):
        yield (None, None, lambda: None)

    class _FakeSession:
        def __init__(self, *a):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def initialize(self):
            # Simulate the SDK persisting tokens via our storage after the flow.
            await FileTokenStorage(cfg.storage_dir()).set_tokens(
                OAuthToken(access_token="t", token_type="Bearer")
            )
        async def list_tools(self):
            return SimpleNamespace(tools=[])

    monkeypatch.setattr("mcp.client.streamable_http.streamablehttp_client", _fake_shc)
    monkeypatch.setattr("mcp.ClientSession", _FakeSession)

    assert mgr.authenticate("notion") is True
    assert (config.MCP_OAUTH_DIR / "notion" / "tokens.json").exists()


# ---- Live (opt-in) -------------------------------------------------------


@pytest.mark.mcp_live
@pytest.mark.skipif(
    os.environ.get("JEANMICHEL_MCP_LIVE_OAUTH") != "1",
    reason="set JEANMICHEL_MCP_LIVE_OAUTH=1 (interactive browser) to run",
)
def test_live_oauth_notion():  # pragma: no cover - manual
    cfg = _ServerCfg(name="notion", url="https://mcp.notion.com/mcp", category="notes",
                     auth="oauth", scopes="read:content update:content")
    mgr = MCPManager({"notion": cfg}, {"notes": ["jean-michel"]})
    assert mgr.authenticate("notion") is True
