"""Tool: image_search — search the web for IMAGES via the local SearXNG instance.

Thin variant of ``web_search`` using SearXNG's ``categories=images``. Returns
image + thumbnail URLs and the source page for each hit ; it does NOT download
the files (display is browser-side). SearXNG startup/health is reused from
``web_search``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from . import web_search as _ws
from ._base import ToolSpec
from ._errors import tool_error, tool_ok

_MAX_RESULTS = 12
_DEFAULT_RESULTS = 6


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

    seen: set[str] = set()
    hits: list[dict] = []
    for r in raw:
        src = r.get("img_src") or r.get("thumbnail_src") or ""
        if not src or src in seen:
            continue
        seen.add(src)
        hits.append({
            "title": r.get("title", ""),
            "image_url": src,
            "thumbnail_url": r.get("thumbnail_src") or src,
            "source_page": r.get("url", ""),
            "source": r.get("source", ""),
        })
        if len(hits) >= results:
            break

    titles = [h["title"][:40] for h in hits[:3] if h["title"]]
    summary = f"{len(hits)} images for {query!r}"
    if titles:
        summary += ": " + " | ".join(titles)
    return tool_ok(summary, query=query, results=hits)


SPEC = ToolSpec(
    name="image_search",
    description=(
        "Search the web for IMAGES via the local SearXNG instance (images "
        "category). Returns image URLs, thumbnail URLs and the source page for "
        "each hit — it does NOT download the files. Use it when the user wants "
        "to find or look at pictures. (Analysing a found image requires bringing "
        "it into the workspace first.)"
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
