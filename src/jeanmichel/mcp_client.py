"""MCP (Model Context Protocol) client — expose hosted MCP servers' tools.

Jean-Michel acts as an MCP *client*: it connects to remote (Streamable HTTP)
MCP servers declared in ``mcp_servers.toml`` and exposes their tools to agents
as native ``ToolSpec`` entries. Servers are tagged with a ``category`` and
categories map to agent codes, so a server is granted to the right agents
automatically.

Opt-in: enabled only when ``mcp_servers.toml`` lists ≥1 server (and the ``mcp``
extra is installed). ``JEANMICHEL_MCP_DISABLED=1`` force-offs. Everything here
is BEST-EFFORT — disabled / ``mcp`` missing / a server unreachable → no-op (or
``[]``), and a tool failure becomes a ``tool_error`` ; a turn is never broken.

Async↔sync bridge: the MCP SDK is async, the orchestrator is sync. We run ONE
asyncio loop in a daemon thread, owned by the manager. Sync tool handlers call
``run_coroutine_threadsafe(...).result(timeout=)``.

CRITICAL (anyio): ``streamablehttp_client(...)`` / ``ClientSession(...)`` open
anyio cancel scopes pinned to the task that entered them. We therefore run ONE
long-lived "holder" coroutine PER server that enters both ``async with`` blocks,
stores the session, then awaits a stop event. Tool calls are scheduled on the
SAME loop (so they're sibling tasks, never exiting the scope), and teardown sets
the stop event so the holder exits the scopes on its own task. Do NOT open the
session in one submitted coroutine and close it in another — anyio will raise.

v1 simplifications: no mid-session reconnect (a dropped server stays down until
restart); on timeout we abandon the in-flight future (no hard cancel) and rely
on the MCP-level ``read_timeout_seconds`` to end the call.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import re
import threading
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from . import config
from .tools._base import ToolSpec
from .tools._errors import tool_error, tool_ok

_log = logging.getLogger(__name__)

_SAFE = re.compile(r"[^A-Za-z0-9_-]")
_MAX_NAME = 64
_CONNECT_TIMEOUT_S = 15  # bounded wait so a slow server never delays startup
_CALL_BACKSTOP_S = 5  # extra margin above the MCP-level read timeout (hung coro)


@dataclass(frozen=True)
class _ServerCfg:
    name: str
    url: str
    category: str
    auth_env: str | None = None
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    tools: tuple[str, ...] | None = None
    timeout_seconds: int | None = None
    enabled_env: str | None = None  # if set, connect ONLY when this env var is truthy

    def disabled(self) -> bool:
        """A gated server is off unless its ``enabled_env`` is truthy (opt-in)."""
        if not self.enabled_env:
            return False
        val = (os.environ.get(self.enabled_env) or "").strip().lower()
        return val in ("", "0", "false", "no", "off")

    def auth_missing(self) -> bool:
        return bool(self.auth_env) and not os.environ.get(self.auth_env)

    def headers(self) -> dict[str, str] | None:
        if not self.auth_env:
            return None
        token = os.environ.get(self.auth_env)
        if not token:
            return None
        value = f"{self.auth_scheme} {token}".strip() if self.auth_scheme else token
        return {self.auth_header: value}


def _load_config() -> tuple[dict[str, _ServerCfg], dict[str, list[str]]]:
    """Parse mcp_servers.toml → (servers, category→agents). Empty when off."""
    if config.MCP_DISABLED:
        return {}, {}
    path = config.MCP_SERVERS_PATH
    if not path.exists():
        return {}, {}
    try:
        import tomllib

        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except Exception as exc:  # noqa: BLE001
        _log.warning("mcp: failed to read %s: %s", path, exc)
        return {}, {}

    servers: dict[str, _ServerCfg] = {}
    for name, s in (data.get("servers") or {}).items():
        url, category = s.get("url"), s.get("category")
        if not url or not category:
            _log.warning("mcp: server %r missing url/category; skipping", name)
            continue
        tools = s.get("tools")
        servers[name] = _ServerCfg(
            name=name,
            url=url,
            category=category,
            auth_env=s.get("auth_env"),
            auth_header=s.get("auth_header", "Authorization"),
            auth_scheme=s.get("auth_scheme", "Bearer"),
            tools=tuple(tools) if tools else None,
            timeout_seconds=s.get("timeout_seconds"),
            enabled_env=s.get("enabled_env"),
        )
    categories = {k: list(v) for k, v in (data.get("categories") or {}).items()}
    return servers, categories


class MCPManager:
    """Owns the background loop, per-server sessions, and the built ToolSpecs."""

    def __init__(self, servers: dict[str, _ServerCfg], categories: dict[str, list[str]]):
        self._servers = servers
        self._categories = categories
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event: asyncio.Event | None = None
        self._sessions: dict[str, Any] = {}
        self._specs_by_server: dict[str, list[ToolSpec]] = {}
        self._name_map: dict[str, tuple[str, str]] = {}
        self._started = False

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> None:
        if self._started or not self._servers:
            return
        try:
            import mcp  # noqa: F401
        except ImportError:
            _log.warning(
                "mcp: %d server(s) configured but the `mcp` extra isn't installed "
                "(pip install -e .[mcp]); skipping.", len(self._servers)
            )
            return
        self._started = True
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="mcp-loop", daemon=True)
        self._thread.start()
        fut = asyncio.run_coroutine_threadsafe(self._connect_all(), self._loop)
        try:
            fut.result(timeout=_CONNECT_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            _log.warning("mcp: startup connect incomplete: %s", exc)
        total = sum(len(v) for v in self._specs_by_server.values())
        if total:
            _log.info("mcp: %d tool(s) ready across %d server(s)",
                      total, len(self._specs_by_server))

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _connect_all(self) -> None:
        self._stop_event = asyncio.Event()
        ready: list[asyncio.Event] = []
        for cfg in self._servers.values():
            if cfg.disabled():
                _log.debug("mcp: %s gated off (%s not truthy); skipping.", cfg.name, cfg.enabled_env)
                continue
            ev = asyncio.Event()
            ready.append(ev)
            self._loop.create_task(self._holder(cfg, ev))  # type: ignore[union-attr]
        for ev in ready:
            # Slow server: its tools just won't be ready this session.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(ev.wait(), timeout=_CONNECT_TIMEOUT_S)

    async def _holder(self, cfg: _ServerCfg, ready: asyncio.Event) -> None:
        """Long-lived per-server task: owns the session's anyio scopes."""
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        if cfg.auth_missing():
            _log.warning("mcp: %s needs env %s (unset); skipping.", cfg.name, cfg.auth_env)
            ready.set()
            return
        try:
            # Both contexts are entered AND exited on this one holder task — the
            # anyio cancel scopes stay task-pinned (see module docstring).
            async with (
                streamablehttp_client(cfg.url, headers=cfg.headers()) as (read, write, _sid),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                tools = (await session.list_tools()).tools
                self._sessions[cfg.name] = session
                self._build_specs(cfg, tools)
                _log.info("mcp: %s connected (%d tools)",
                          cfg.name, len(self._specs_by_server.get(cfg.name, [])))
                ready.set()
                assert self._stop_event is not None
                await self._stop_event.wait()  # keep the scopes/session alive
        except Exception as exc:  # noqa: BLE001
            _log.warning("mcp: %s connection failed: %s", cfg.name, exc)
            ready.set()

    def close(self) -> None:
        if not self._started or self._loop is None:
            return
        loop = self._loop
        try:
            asyncio.run_coroutine_threadsafe(self._ashutdown(), loop).result(timeout=5)
        except Exception as exc:  # noqa: BLE001
            _log.debug("mcp: shutdown drain incomplete: %s", exc)
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._started = False

    async def _ashutdown(self) -> None:
        """Signal holders, cancel any in-flight tasks, await them — clean exit
        of the anyio scopes on their own tasks (no 'task destroyed' warnings)."""
        if self._stop_event is not None:
            self._stop_event.set()
        pending = [t for t in asyncio.all_tasks(self._loop) if t is not asyncio.current_task()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    # ---- spec building ---------------------------------------------------

    def _prefixed_name(self, server: str, tool: str) -> str:
        s, t = _SAFE.sub("_", server), _SAFE.sub("_", tool)
        name = f"mcp__{s}__{t}"
        if len(name) > _MAX_NAME:
            digest = hashlib.sha1(f"{server}/{tool}".encode()).hexdigest()[:4]
            keep = max(1, _MAX_NAME - len(f"mcp__{s}__") - 5)
            name = f"mcp__{s}__{t[:keep]}_{digest}"
        return name

    def _build_specs(self, cfg: _ServerCfg, tools: list[Any]) -> None:
        allow = set(cfg.tools) if cfg.tools else None
        timeout = cfg.timeout_seconds or config.MCP_CALL_TIMEOUT_SECONDS
        specs: list[ToolSpec] = []
        for tool in tools:
            if allow is not None and tool.name not in allow:
                continue
            if len(specs) >= config.MCP_MAX_TOOLS_PER_SERVER:
                _log.warning("mcp: %s exposes more than %d tools; capping.",
                             cfg.name, config.MCP_MAX_TOOLS_PER_SERVER)
                break
            prefixed = self._prefixed_name(cfg.name, tool.name)
            self._name_map[prefixed] = (cfg.name, tool.name)
            specs.append(ToolSpec(
                name=prefixed,
                description=(tool.description or f"MCP tool {tool.name} ({cfg.name})")[:1024],
                parameters=tool.inputSchema or {"type": "object", "properties": {}},
                handler=self._make_handler(cfg.name, tool.name, timeout),
            ))
        self._specs_by_server[cfg.name] = specs

    def _make_handler(self, server: str, real_name: str, timeout: int):
        def _handler(**kwargs: Any) -> str:
            return self._call_tool_sync(server, real_name, kwargs, timeout)
        return _handler

    def _call_tool_sync(self, server: str, real_name: str, args: dict, timeout: int) -> str:
        session = self._sessions.get(server)
        if session is None or self._loop is None:
            return tool_error("mcp_unavailable", f"MCP server '{server}' is not connected")
        coro = session.call_tool(real_name, args or {},
                                 read_timeout_seconds=timedelta(seconds=timeout))
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            result = fut.result(timeout=timeout + _CALL_BACKSTOP_S)  # backstop for a hung coro
        except FuturesTimeout:
            return tool_error("mcp_timeout", f"{server}.{real_name} timed out after {timeout}s")
        except Exception as exc:  # noqa: BLE001
            return tool_error("mcp_error", f"{server}.{real_name}: {exc}")
        return _format_result(result)

    # ---- category → agent resolution ------------------------------------

    def _servers_for_agent(self, agent_code: str) -> list[str]:
        out, seen = [], set()
        for category, agents in self._categories.items():
            if agent_code not in agents:
                continue
            for name, cfg in self._servers.items():
                if cfg.category == category and name not in seen and name in self._specs_by_server:
                    seen.add(name)
                    out.append(name)
        return out

    def tool_specs_for_agent(self, agent_code: str) -> list[ToolSpec]:
        specs: list[ToolSpec] = []
        for server in self._servers_for_agent(agent_code):
            specs.extend(self._specs_by_server.get(server, []))
        return specs

    def granted_tool_names_for(self, agent_code: str) -> frozenset[str]:
        return frozenset(spec.name for spec in self.tool_specs_for_agent(agent_code))

    def all_tool_specs(self) -> list[ToolSpec]:
        """Every discovered MCP ToolSpec (across all servers). The registry is
        permissive ; per-agent grants decide visibility/permission."""
        specs: list[ToolSpec] = []
        for server_specs in self._specs_by_server.values():
            specs.extend(server_specs)
        return specs


def _format_result(result: Any) -> str:
    text = _extract_text(result)
    if getattr(result, "isError", False):
        return tool_error("mcp_tool_error", (text or "tool returned an error")[:500])
    fields: dict[str, Any] = {}
    structured = getattr(result, "structuredContent", None)
    if structured:
        fields["structured"] = structured
    summary = next((ln for ln in (text or "").splitlines() if ln.strip()), "")[:200]
    return tool_ok(summary or "(no text content)", content=text or "", **fields)


def _extract_text(result: Any) -> str:
    parts: list[str] = []
    for block in (getattr(result, "content", None) or []):
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
        else:
            parts.append(f"[{getattr(block, 'type', 'non-text')} content]")
    return "\n".join(parts)


# ---- module singleton --------------------------------------------------------

_manager: MCPManager | None = None


def get_manager() -> MCPManager:
    """Return the process-wide manager (inert when MCP is off/unconfigured)."""
    global _manager
    if _manager is None:
        _manager = MCPManager(*_load_config())
    return _manager


def startup() -> None:
    """Best-effort connect to configured servers. No-op when off/unconfigured."""
    if config.MCP_DISABLED:
        return
    get_manager().start()


def shutdown() -> None:
    if _manager is not None:
        _manager.close()
