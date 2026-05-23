"""Tool: web_search — search the web via a local SearXNG instance.

Exports one ToolSpec:
  SPEC  (name="web_search")

Auto-starts the SearXNG Docker container if it is not running, then polls
GET / until it responds with 200 (max _STARTUP_TIMEOUT_S seconds).
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ._base import ToolSpec

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SEARXNG_URL = "http://127.0.0.1:8080"
_COMPOSE_FILE = Path(__file__).resolve().parents[3] / "docker" / "searxng" / "compose.yml"

_STARTUP_TIMEOUT_S = 20   # max wait after docker start
_POLL_INTERVAL_S   = 0.5  # between health probes
_SEARCH_TIMEOUT_S  = 10   # per HTTP search request
_MAX_RESULTS       = 8    # results returned to the LLM


# ---------------------------------------------------------------------------
# Internal helpers (replaceable in tests)
# ---------------------------------------------------------------------------

def _is_alive() -> bool:
    """Return True if SearXNG answers GET /."""
    try:
        urllib.request.urlopen(_SEARXNG_URL + "/", timeout=2)
        return True
    except Exception:  # noqa: BLE001
        return False


def _docker_start() -> None:
    """Launch the SearXNG container via docker compose (detached)."""
    subprocess.run(
        ["docker", "compose", "-f", str(_COMPOSE_FILE), "up", "-d"],
        check=True,
        capture_output=True,
    )


def _wait_until_alive() -> bool:
    """Poll GET / until SearXNG is ready or the deadline is reached."""
    deadline = time.monotonic() + _STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if _is_alive():
            return True
        time.sleep(_POLL_INTERVAL_S)
    return False


def _ensure_running() -> str | None:
    """Ensure SearXNG is up. Returns an error string or None."""
    if _is_alive():
        return None
    try:
        _docker_start()
    except subprocess.CalledProcessError as e:
        return f"docker compose up failed: {e.stderr.decode(errors='replace').strip()}"
    if not _wait_until_alive():
        return f"SearXNG did not become ready within {_STARTUP_TIMEOUT_S}s"
    return None


def _do_search(query: str, language: str, results: int) -> list[dict]:
    """Execute the search against SearXNG and return raw result dicts."""
    params = urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "language": language,
        "safesearch": "0",
    })
    req = urllib.request.Request(
        f"{_SEARXNG_URL}/search?{params}",
        headers={"User-Agent": "jean-michel/1.0"},
    )
    with urllib.request.urlopen(req, timeout=_SEARCH_TIMEOUT_S) as resp:
        data = json.loads(resp.read().decode())
    return data.get("results", [])[:results]


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def _handler(query: str, language: str = "fr-FR", results: int = 5) -> str:
    results = max(1, min(int(results), _MAX_RESULTS))

    err = _ensure_running()
    if err:
        return json.dumps({"error": err})

    try:
        raw = _do_search(query, language, results)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:200]
        return json.dumps({"error": f"SearXNG HTTP {e.code}: {body}"})
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"Search failed: {e}"})

    hits = [
        {
            "title": r.get("title", ""),
            "url":   r.get("url", ""),
            "snippet": r.get("content", ""),
        }
        for r in raw[:results]
    ]
    return json.dumps({"query": query, "results": hits})


# ---------------------------------------------------------------------------
# ToolSpec
# ---------------------------------------------------------------------------

SPEC = ToolSpec(
    name="web_search",
    description=(
        "Search the web using a local SearXNG instance (aggregates DuckDuckGo, "
        "Brave, Mojeek and others). "
        "Returns titles, URLs and snippets for the top results. "
        "Use this for current events, facts not covered by Wikipedia, or any "
        "topic requiring a broader web search. "
        "Prefer wikipedia_search for encyclopaedic knowledge."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query.",
            },
            "language": {
                "type": "string",
                "description": "BCP-47 language/region code, e.g. 'fr-FR', 'en-US'. Default 'fr-FR'.",
            },
            "results": {
                "type": "integer",
                "description": f"Number of results to return (1-{_MAX_RESULTS}). Default 5.",
            },
        },
        "required": ["query"],
    },
    handler=_handler,
)
