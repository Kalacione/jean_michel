"""Unit tests for the MCP client manager — no network, no `mcp` package needed.

Two layers : (1) ToolSpec building from stub tools (pure, no loop) ; (2) the
sync→async bridge with a fake session on the manager's real background loop
(covers text / non-text / isError / timeout / unavailable). The live smoke test
against a real server lives behind @pytest.mark.mcp_live.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from types import SimpleNamespace

import pytest

from jeanmichel import config, mcp_client
from jeanmichel.mcp_client import MCPManager, _ServerCfg


def _tool(name, schema=None, desc="d"):
    return SimpleNamespace(name=name, description=desc, inputSchema=schema or {"type": "object"})


# ---- Layer 1 : spec building (pure) --------------------------------------


def test_build_specs_prefix_schema_and_namemap():
    mgr = MCPManager({}, {})
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    mgr._build_specs(_ServerCfg(name="vue tify!", url="x", category="docs"),
                     [_tool("get_API/v2", schema)])
    specs = mgr._specs_by_server["vue tify!"]
    assert len(specs) == 1
    spec = specs[0]
    assert spec.name.startswith("mcp__")
    assert all(c.isalnum() or c in "_-" for c in spec.name)  # sanitized
    assert spec.parameters is schema                          # inputSchema passthrough
    assert mgr._name_map[spec.name] == ("vue tify!", "get_API/v2")  # maps to REAL name


def test_build_specs_allowlist():
    mgr = MCPManager({}, {})
    mgr._build_specs(_ServerCfg(name="s", url="x", category="c", tools=("keep",)),
                     [_tool("keep"), _tool("drop")])
    assert {s.name for s in mgr._specs_by_server["s"]} == {"mcp__s__keep"}


def test_build_specs_caps(monkeypatch):
    monkeypatch.setattr(config, "MCP_MAX_TOOLS_PER_SERVER", 2)
    mgr = MCPManager({}, {})
    mgr._build_specs(_ServerCfg(name="s", url="x", category="c"),
                     [_tool(f"t{i}") for i in range(5)])
    assert len(mgr._specs_by_server["s"]) == 2


def test_prefixed_name_length_cap():
    mgr = MCPManager({}, {})
    name = mgr._prefixed_name("srv", "x" * 80)
    assert len(name) <= 64 and name.startswith("mcp__srv__")


def test_category_resolution():
    servers = {
        "vuetify": _ServerCfg(name="vuetify", url="x", category="docs"),
        "github": _ServerCfg(name="github", url="y", category="code"),
    }
    mgr = MCPManager(servers, {"docs": ["jean-michel", "code-fetcher"], "code": ["code-fetcher"]})
    mgr._build_specs(servers["vuetify"], [_tool("a")])
    mgr._build_specs(servers["github"], [_tool("b")])
    assert mgr.granted_tool_names_for("jean-michel") == {"mcp__vuetify__a"}
    assert mgr.granted_tool_names_for("code-fetcher") == {"mcp__vuetify__a", "mcp__github__b"}
    assert mgr.granted_tool_names_for("meta-analyst") == frozenset()   # default-deny
    assert len(mgr.all_tool_specs()) == 2


def test_auth_header_from_env(monkeypatch):
    monkeypatch.setenv("TOK", "secret")
    cfg = _ServerCfg(name="s", url="x", category="c", auth_env="TOK")
    assert cfg.headers() == {"Authorization": "Bearer secret"}
    assert not cfg.auth_missing()
    monkeypatch.delenv("TOK")
    assert cfg.auth_missing() and cfg.headers() is None


# ---- Layer 2 : sync→async bridge with a fake session ---------------------


def _result(text=None, is_error=False, structured=None, blocks=None):
    if blocks is None:
        blocks = [SimpleNamespace(type="text", text=text)] if text is not None else []
    return SimpleNamespace(content=blocks, isError=is_error, structuredContent=structured)


class _FakeSession:
    def __init__(self, result=None, exc=None, delay=0.0):
        self._result, self._exc, self._delay = result, exc, delay

    async def call_tool(self, name, arguments, read_timeout_seconds=None):
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._exc:
            raise self._exc
        return self._result


@pytest.fixture()
def running_manager():
    """A manager with its background loop running but no real servers."""
    mgr = MCPManager({}, {})
    mgr._loop = asyncio.new_event_loop()
    mgr._thread = threading.Thread(target=mgr._run_loop, daemon=True)
    mgr._thread.start()
    mgr._started = True
    yield mgr
    mgr.close()


def test_bridge_text_to_tool_ok(running_manager):
    running_manager._sessions["s"] = _FakeSession(result=_result(text="line1\nline2"))
    out = json.loads(running_manager._make_handler("s", "do", 5)(q=1))
    assert out["summary"] == "line1"
    assert out["content"] == "line1\nline2"


def test_bridge_structured_content(running_manager):
    running_manager._sessions["s"] = _FakeSession(result=_result(text="t", structured={"k": 1}))
    out = json.loads(running_manager._make_handler("s", "do", 5)())
    assert out["structured"] == {"k": 1}


def test_bridge_is_error_to_tool_error(running_manager):
    running_manager._sessions["s"] = _FakeSession(result=_result(text="boom", is_error=True))
    out = json.loads(running_manager._make_handler("s", "do", 5)())
    assert out["error_code"] == "mcp_tool_error"


def test_bridge_non_text_block(running_manager):
    running_manager._sessions["s"] = _FakeSession(result=_result(blocks=[SimpleNamespace(type="image")]))
    out = json.loads(running_manager._make_handler("s", "do", 5)())
    assert "[image content]" in out["content"]


def test_bridge_exception_to_tool_error(running_manager):
    running_manager._sessions["s"] = _FakeSession(exc=ValueError("nope"))
    out = json.loads(running_manager._make_handler("s", "do", 5)())
    assert out["error_code"] == "mcp_error" and "nope" in out["error"]


def test_bridge_timeout(running_manager, monkeypatch):
    monkeypatch.setattr(mcp_client, "_CALL_BACKSTOP_S", 0)  # backstop fires fast
    running_manager._sessions["s"] = _FakeSession(result=_result(text="late"), delay=10)
    out = json.loads(running_manager._make_handler("s", "do", 1)())
    assert out["error_code"] == "mcp_timeout"


def test_bridge_unavailable_server(running_manager):
    out = json.loads(running_manager._make_handler("absent", "do", 5)())
    assert out["error_code"] == "mcp_unavailable"


# ---- Live smoke (opt-in) -------------------------------------------------


@pytest.mark.mcp_live
@pytest.mark.skipif(os.environ.get("JEANMICHEL_MCP_LIVE") != "1", reason="set JEANMICHEL_MCP_LIVE=1")
def test_live_vuetify(monkeypatch, tmp_path):
    cfg = tmp_path / "mcp_servers.toml"
    cfg.write_text(
        '[servers.vuetify]\nurl="https://mcp.vuetifyjs.com/mcp"\ncategory="docs"\n'
        'tools=["get_installation_guide"]\n[categories]\ndocs=["jean-michel"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "MCP_SERVERS_PATH", cfg)
    monkeypatch.setattr(config, "MCP_DISABLED", False)
    monkeypatch.setattr(mcp_client, "_manager", None)
    mcp_client.startup()
    try:
        specs = mcp_client.get_manager().tool_specs_for_agent("jean-michel")
        assert specs, "expected ≥1 vuetify tool"
        out = json.loads(specs[0].handler())
        assert "summary" in out and out.get("content")
    finally:
        mcp_client.shutdown()
