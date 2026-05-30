"""Tool: image_search — search the web for IMAGES via the local SearXNG instance.

Thin variant of ``web_search`` using SearXNG's ``categories=images``. Returns
image + thumbnail URLs and the source page for each hit ; it does NOT download
the files (display is browser-side). SearXNG startup/health is reused from
``web_search``.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import web_search as _ws
from ._base import ToolSpec
from ._errors import tool_error, tool_ok

# Cap image results at 5 (max AND default) : "montre-moi des images" should
# return up to 5, not a flood. Dedup/relevance may yield fewer — that's fine.
_MAX_RESULTS = 5
_DEFAULT_RESULTS = 5


def _do_image_search(query: str, language: str, results: int) -> list[dict]:
    """Query SearXNG's image category. Returns raw result dicts."""
    params = urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "language": language,
        "safesearch": "1",
        "categories": "images",
    })
    req = urllib.request.Request(
        f"{_ws._SEARXNG_URL}/search?{params}",
        headers={"User-Agent": "jean-michel/1.0"},
    )
    with urllib.request.urlopen(req, timeout=_ws._SEARCH_TIMEOUT_S) as resp:
        data = json.loads(resp.read().decode())
    return data.get("results", [])[:results]


def _stems(text: str) -> set[str]:
    """4-char prefixes of the ≥4-char words in `text` — a crude, language-agnostic
    stem set used for relevance matching."""
    return {w[:4] for w in re.split(r"\W+", text.lower()) if len(w) >= 4}


def _relevant(query: str, r: dict) -> bool:
    """True if a result shares a word-stem with the query. Drops the obviously
    off-topic hits SearXNG's image engines sometimes mix in (e.g. an art
    self-portrait for 'capybara')."""
    q = _stems(query)
    if not q:
        return True
    hay = f"{r.get('title', '')} {r.get('url', '')} {r.get('source', '')}"
    return bool(q & _stems(hay))


def _handler(query: str, language: str = "fr-FR", results: int = _DEFAULT_RESULTS) -> str:
    results = max(1, min(int(results), _MAX_RESULTS))

    err = _ws._ensure_running()
    if err:
        return tool_error("searxng_unavailable", err)

    try:
        raw = _do_image_search(query, language, results * 2)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:200]
        return tool_error("http_error", f"SearXNG HTTP {e.code}: {body}")
    except Exception as e:  # noqa: BLE001
        return tool_error("search_failed", f"Image search failed: {e}")

    def _pick(apply_relevance: bool) -> list[dict]:
        seen: set[str] = set()
        out: list[dict] = []
        for r in raw:
            if apply_relevance and not _relevant(query, r):
                continue
            src = r.get("img_src") or r.get("thumbnail_src") or ""
            if not src or src in seen:
                continue
            seen.add(src)
            out.append({
                "title": r.get("title", ""),
                "image_url": src,
                "thumbnail_url": r.get("thumbnail_src") or src,
                "source_page": r.get("url", ""),
                "source": r.get("source", ""),
            })
            if len(out) >= results:
                break
        return out

    # Relevance filter first ; fall back to unfiltered if it removes everything.
    hits = _pick(True) or _pick(False)

    titles = [h["title"][:40] for h in hits[:3] if h["title"]]
    summary = f"{len(hits)} images for {query!r}"
    if titles:
        summary += ": " + " | ".join(titles)
    return tool_ok(summary, query=query, results=hits)


SPEC = ToolSpec(
    name="image_search",
    description=(
        "Search the web for IMAGES via the local SearXNG instance (images "
        "category). Returns results with image_url (the DIRECT image), "
        "thumbnail_url, title and source_page ; off-topic hits are filtered out. "
        "When the user wants to SEE / SHOW pictures, present each relevant result "
        "INLINE as a Markdown image so it renders visually: `![title](image_url)` "
        "— do NOT just paste links (use source_page only for an optional credit). "
        "To analyse a found image, bring it into the workspace with image_fetch "
        "first."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The image search query."},
            "language": {
                "type": "string",
                "description": "BCP-47 language/region code, e.g. 'fr-FR'. Default 'fr-FR'.",
            },
            "results": {
                "type": "integer",
                "description": f"Number of images to return (1-{_MAX_RESULTS}). Default {_DEFAULT_RESULTS}.",
            },
        },
        "required": ["query"],
    },
    handler=_handler,
)
