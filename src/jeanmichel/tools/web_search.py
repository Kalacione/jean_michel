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
from ._errors import tool_error, tool_ok

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SEARXNG_URL = "http://127.0.0.1:8080"
_COMPOSE_FILE = Path(__file__).resolve().parents[3] / "docker" / "searxng" / "compose.yml"

_STARTUP_TIMEOUT_S = 20   # max wait after docker start
_POLL_INTERVAL_S   = 0.5  # between health probes
_SEARCH_TIMEOUT_S  = 10   # per HTTP search request
_MAX_RESULTS       = 10   # hard cap on results returned to the LLM
_DEFAULT_RESULTS   = 8    # default when caller does not specify
# Over-fetch from SearXNG so that after de-duplication we still have enough
# distinct hits to honour the requested `results` count.
_OVERFETCH_FACTOR  = 3
_JACCARD_THRESHOLD = 0.7  # title similarity above which two hits collapse


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
# De-duplication
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset({
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "with",
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "ou", "à",
    "au", "aux", "en", "sur", "pour", "par", "avec", "dans",
})


def _hostname(url: str) -> str:
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:  # noqa: BLE001
        return ""
    return host.lower().removeprefix("www.")


def _title_tokens(title: str) -> set[str]:
    """Normalise a title into a set of meaningful tokens for Jaccard."""
    cleaned = "".join(c.lower() if c.isalnum() else " " for c in title)
    raw_tokens = [tok for tok in cleaned.split() if tok]
    meaningful = {tok for tok in raw_tokens if len(tok) > 2 and tok not in _STOPWORDS}
    # Fallback: if filtering wiped everything out (very short titles), keep the
    # raw tokens minus stopwords so we don't collapse unrelated short titles.
    if not meaningful:
        meaningful = {tok for tok in raw_tokens if tok not in _STOPWORDS}
    return meaningful


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _dedupe(raw: list[dict]) -> list[dict]:
    """Drop near-duplicates: same hostname OR title Jaccard ≥ threshold."""
    kept: list[dict] = []
    kept_tokens: list[set[str]] = []
    seen_hosts: set[str] = set()
    for r in raw:
        url = r.get("url", "")
        host = _hostname(url)
        if host and host in seen_hosts:
            continue
        title = r.get("title", "")
        tokens = _title_tokens(title)
        if any(_jaccard(tokens, kt) >= _JACCARD_THRESHOLD for kt in kept_tokens):
            continue
        kept.append(r)
        kept_tokens.append(tokens)
        if host:
            seen_hosts.add(host)
    return kept


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def _handler(query: str, language: str = "fr-FR", results: int = _DEFAULT_RESULTS) -> str:
    results = max(1, min(int(results), _MAX_RESULTS))

    err = _ensure_running()
    if err:
        return tool_error("searxng_unavailable", err)

    try:
        raw = _do_search(query, language, results * _OVERFETCH_FACTOR)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:200]
        return tool_error("http_error", f"SearXNG HTTP {e.code}: {body}")
    except Exception as e:  # noqa: BLE001
        return tool_error("search_failed", f"Search failed: {e}")

    deduped = _dedupe(raw)
    dropped = len(raw) - len(deduped)
    hits = [
        {
            "title": r.get("title", ""),
            "url":   r.get("url", ""),
            "snippet": r.get("content", ""),
        }
        for r in deduped[:results]
    ]
    titles = [_truncate_title(h["title"]) for h in hits[:3]]
    summary = f"{len(hits)} distinct hits for {query!r}"
    if dropped:
        summary += f" ({dropped} duplicates dropped)"
    if titles:
        summary += ": " + " | ".join(titles)
    return tool_ok(summary, query=query, results=hits, duplicates_dropped=dropped)


def _truncate_title(t: str, n: int = 40) -> str:
    t = (t or "").strip()
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


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
                "description": (
                    f"Number of distinct results to return (1-{_MAX_RESULTS}). "
                    f"Default {_DEFAULT_RESULTS}. Results are de-duplicated by "
                    "hostname and title similarity before being returned."
                ),
            },
        },
        "required": ["query"],
    },
    handler=_handler,
)
