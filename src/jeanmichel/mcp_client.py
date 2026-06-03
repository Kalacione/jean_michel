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
from pathlib import Path
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
    auth: str = "none"            # "none" | "bearer" | "oauth"
    auth_env: str | None = None   # bearer: env var holding the token
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    scopes: str | None = None     # oauth: space-separated scopes
    redirect_port: int = 0        # oauth: 0 → config.MCP_OAUTH_DEFAULT_PORT
    redirect_path: str = "/callback"
    client_name: str = "Jean-Michel"
    instructions: str | None = None  # appended to this server's tool descriptions
    tools: tuple[str, ...] | None = None
    timeout_seconds: int | None = None

    def is_oauth(self) -> bool:
        return self.auth == "oauth"

    def port(self) -> int:
        return self.redirect_port or config.MCP_OAUTH_DEFAULT_PORT

    def redirect_uri(self) -> str:
        return f"http://localhost:{self.port()}{self.redirect_path}"

    def storage_dir(self) -> Path:
        return config.MCP_OAUTH_DIR / self.name

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
            auth=s.get("auth") or ("bearer" if s.get("auth_env") else "none"),
            auth_env=s.get("auth_env"),
            auth_header=s.get("auth_header", "Authorization"),
            auth_scheme=s.get("auth_scheme", "Bearer"),
            scopes=s.get("scopes") or s.get("scope"),
            redirect_port=int(s.get("redirect_port", 0)),
            redirect_path=s.get("redirect_path", "/callback"),
            client_name=s.get("client_name", "Jean-Michel"),
            instructions=s.get("instructions"),
            tools=tuple(tools) if tools else None,
            timeout_seconds=s.get("timeout_seconds"),
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

        # Resolve transport auth : OAuth provider, bearer header, or none.
        headers: dict[str, str] | None = None
        auth = None
        if cfg.is_oauth():
            storage = FileTokenStorage(cfg.storage_dir())
            if await storage.get_tokens() is None:
                _log.warning("mcp: %s is OAuth but not authenticated — run "
                             "./jm.sh --mcp-auth %s", cfg.name, cfg.name)
                ready.set()
                return
            # interactive=False : redirect/callback raise, so the daemon can never
            # pop a browser (only a stored-token/refresh path is reachable here).
            auth = self._build_oauth_provider(cfg, storage, interactive=False)
        else:
            if cfg.auth_missing():
                _log.warning("mcp: %s needs env %s (unset); skipping.", cfg.name, cfg.auth_env)
                ready.set()
                return
            headers = cfg.headers()
        try:
            # Both contexts are entered AND exited on this one holder task — the
            # anyio cancel scopes stay task-pinned (see module docstring).
            async with (
                streamablehttp_client(cfg.url, headers=headers, auth=auth) as (read, write, _sid),
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
            desc = tool.description or f"MCP tool {tool.name} ({cfg.name})"
            if cfg.instructions:  # generic, config-driven nudge
                desc = f"{desc}\n\n{cfg.instructions}"
            specs.append(ToolSpec(
                name=prefixed,
                description=desc[:1024],
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

    # ---- OAuth (generic) -------------------------------------------------

    def _build_oauth_provider(self, cfg: _ServerCfg, storage, *, interactive, callback=None):
        """Build an OAuthClientProvider (httpx.Auth). interactive=False wires
        raising handlers so a non-interactive run never opens a browser."""
        from mcp.client.auth import OAuthClientProvider
        from mcp.shared.auth import OAuthClientMetadata

        metadata = OAuthClientMetadata(
            redirect_uris=[cfg.redirect_uri()],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=cfg.scopes,
            client_name=cfg.client_name,
        )
        if interactive and callback is not None:
            redirect_handler, callback_handler = _open_browser, callback.wait
        else:
            redirect_handler = callback_handler = _no_interaction(cfg.name)
        return OAuthClientProvider(
            server_url=cfg.url,
            client_metadata=metadata,
            storage=storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
            timeout=config.MCP_OAUTH_CALLBACK_TIMEOUT,
        )

    def authenticate(self, server: str) -> bool:
        """Run the one-time interactive OAuth consent for `server` (opens a
        browser, persists tokens). Self-contained — no daemon, no Ollama."""
        cfg = self._servers.get(server)
        if cfg is None:
            _log.error("mcp: unknown server %r (check mcp_servers.toml).", server)
            return False
        if not cfg.is_oauth():
            _log.error("mcp: server %r is not an OAuth server (auth=%r).", server, cfg.auth)
            return False
        try:
            import mcp  # noqa: F401
        except ImportError:
            _log.error("mcp: the `mcp` extra isn't installed (pip install -e .[mcp]).")
            return False
        try:
            return asyncio.run(self._authenticate_async(cfg))
        except OSError as exc:
            _log.error("mcp: callback port %d unavailable (%s) — free it or set "
                       "redirect_port in mcp_servers.toml.", cfg.port(), exc)
            return False
        except Exception as exc:  # noqa: BLE001
            _log.error("mcp: authentication for %s failed: %s", server, exc)
            return False

    async def _authenticate_async(self, cfg: _ServerCfg) -> bool:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        storage = FileTokenStorage(cfg.storage_dir())
        with _LoopbackCallback(cfg.port(), cfg.redirect_path) as cb:
            auth = self._build_oauth_provider(cfg, storage, interactive=True, callback=cb)
            async with (
                streamablehttp_client(cfg.url, auth=auth) as (read, write, _sid),
                ClientSession(read, write) as session,
            ):
                await session.initialize()  # first request 401s → DCR → browser → token
                await session.list_tools()  # prove the token works
        return await storage.get_tokens() is not None


# ---- OAuth helpers (used only on the auth=oauth path) ----------------------


class FileTokenStorage:
    """File-backed `TokenStorage` (tokens.json + client.json under a per-server
    dir). Plaintext, chmod 600 — same trust level as `.env`. Atomic writes."""

    def __init__(self, directory: Path):
        self._dir = Path(directory)

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        return self._read("tokens.json", OAuthToken)

    async def set_tokens(self, tokens) -> None:
        self._write("tokens.json", tokens)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        return self._read("client.json", OAuthClientInformationFull)

    async def set_client_info(self, client_info) -> None:
        self._write("client.json", client_info)

    def _read(self, name: str, model: Any):
        path = self._dir / name
        if not path.exists():
            return None
        try:
            return model.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            _log.debug("mcp: unreadable token file %s: %s", path, exc)
            return None

    def _write(self, name: str, model: Any) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self._dir, 0o700)
        tmp = self._dir / f".{name}.tmp"
        tmp.write_text(model.model_dump_json(), encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        os.replace(tmp, self._dir / name)  # atomic : readers never see a partial file


class _LoopbackCallback:
    """Context-managed loopback HTTP server that captures the OAuth redirect
    (?code&state) on a FIXED port. The port must match the DCR redirect_uri, so
    we never auto-increment — a busy port raises OSError to the caller."""

    def __init__(self, port: int, path: str = "/callback"):
        self._port, self._path = port, path
        self.code: str | None = None
        self.state: str | None = None
        self.error: str | None = None
        self._event = threading.Event()
        self._httpd = None
        self._thread: threading.Thread | None = None

    def __enter__(self):
        import http.server

        cb = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_a):  # silence the default stderr logging
                pass

            def do_GET(self):  # noqa: N802
                from urllib.parse import parse_qs, urlparse
                q = parse_qs(urlparse(self.path).query)
                cb.code = (q.get("code") or [None])[0]
                cb.state = (q.get("state") or [None])[0]
                cb.error = (q.get("error") or [None])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h2>Authentication complete - you can close this tab.</h2>"
                    b"</body></html>"
                )
                cb._event.set()

        self._httpd = http.server.HTTPServer(("127.0.0.1", self._port), _Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()

    async def wait(self) -> tuple[str, str | None]:
        loop = asyncio.get_running_loop()
        ok = await loop.run_in_executor(None, self._event.wait, config.MCP_OAUTH_CALLBACK_TIMEOUT)
        if not ok:
            raise TimeoutError("OAuth callback timed out")
        if self.error:
            raise RuntimeError(f"OAuth error: {self.error}")
        if not self.code:
            raise RuntimeError("OAuth callback received no authorization code")
        return self.code, self.state


async def _open_browser(url: str) -> None:
    """redirect_handler : open the consent URL ; log it if no browser is available."""
    import webbrowser
    try:
        opened = await asyncio.to_thread(webbrowser.open, url)
    except Exception:  # noqa: BLE001
        opened = False
    if not opened:
        _log.warning("mcp: could not open a browser — authorize manually:\n%s", url)
        print(f"\nOpen this URL to authorize:\n{url}\n")


def _no_interaction(server_name: str):
    """Build a redirect/callback handler that refuses interaction (daemon path)."""
    async def _handler(*_args):
        raise RuntimeError(
            f"OAuth interaction required for {server_name}; run ./jm.sh --mcp-auth {server_name}"
        )
    return _handler


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


def authenticate(server: str) -> bool:
    """Run the one-time OAuth consent for `server`. Returns success."""
    if config.MCP_DISABLED:
        _log.error("mcp: disabled (JEANMICHEL_MCP_DISABLED).")
        return False
    return get_manager().authenticate(server)
