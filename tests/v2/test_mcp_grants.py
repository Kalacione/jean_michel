"""MCP grant integration : a category-granted MCP tool flows into the agent's
tool_grants (the single source that drives BOTH the Ollama payload and the
PreToolUse gate) and into the registry. Uses a fake manager — no network."""

from __future__ import annotations

from types import SimpleNamespace

from jeanmichel import db, mcp_client
from jeanmichel.mcp_client import MCPManager, _ServerCfg
from jeanmichel.orchestrator_v2 import _build_tools_payload, load_agent_spec_v2
from jeanmichel.tools import build_registry


def _fake_manager() -> MCPManager:
    servers = {"fake": _ServerCfg(name="fake", url="x", category="docs")}
    mgr = MCPManager(servers, {"docs": ["jean-michel"]})  # docs → jean-michel only
    mgr._build_specs(servers["fake"],
                     [SimpleNamespace(name="do", description="d", inputSchema={"type": "object"})])
    return mgr


def test_grant_merged_only_for_mapped_agent(tmp_db_v2, monkeypatch):
    monkeypatch.setattr(mcp_client, "_manager", _fake_manager())
    with db.connect() as conn:
        jm = load_agent_spec_v2(conn, "jean-michel", mode="analyse")
        ma = load_agent_spec_v2(conn, "meta-analyst", mode="analyse")
    assert "mcp__fake__do" in jm.tool_grants        # docs → jean-michel
    assert "mcp__fake__do" not in ma.tool_grants     # unmapped → default-deny


def test_registry_includes_and_payload_emits(tmp_db_v2, monkeypatch):
    monkeypatch.setattr(mcp_client, "_manager", _fake_manager())
    extra = mcp_client.get_manager().all_tool_specs()
    assert [s.name for s in extra] == ["mcp__fake__do"]

    with db.connect() as conn:
        jm = load_agent_spec_v2(conn, "jean-michel", mode="analyse")

    registry = build_registry(conv_folder=tmp_db_v2.parent, extra_tools=extra)
    assert "mcp__fake__do" in registry

    payload_names = {p["function"]["name"] for p in _build_tools_payload(jm, registry)}
    assert "mcp__fake__do" in payload_names          # the LLM sees it (granted)


def test_unmapped_agent_payload_excludes_mcp(tmp_db_v2, monkeypatch):
    monkeypatch.setattr(mcp_client, "_manager", _fake_manager())
    extra = mcp_client.get_manager().all_tool_specs()
    with db.connect() as conn:
        ma = load_agent_spec_v2(conn, "meta-analyst", mode="analyse")
    registry = build_registry(conv_folder=tmp_db_v2.parent, extra_tools=extra)
    # The tool exists in the permissive registry but meta-analyst isn't granted
    # it → it never reaches the model's payload.
    assert "mcp__fake__do" in registry
    payload_names = {p["function"]["name"] for p in _build_tools_payload(ma, registry)}
    assert "mcp__fake__do" not in payload_names


# ---- enabled_env gating -----------------------------------------------------


def test_enabled_env_gates_server(monkeypatch):
    cfg = _ServerCfg(name="g", url="x", category="code", enabled_env="JM_GPF_X")
    monkeypatch.delenv("JM_GPF_X", raising=False)
    assert cfg.disabled() is True          # unset → off
    monkeypatch.setenv("JM_GPF_X", "0")
    assert cfg.disabled() is True          # falsy → off
    monkeypatch.setenv("JM_GPF_X", "1")
    assert cfg.disabled() is False         # truthy → on
    monkeypatch.setenv("JM_GPF_X", "true")
    assert cfg.disabled() is False
    # No enabled_env declared → never gated.
    assert _ServerCfg(name="g", url="x", category="code").disabled() is False
