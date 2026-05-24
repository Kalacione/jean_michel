"""Tool: wikipedia — search articles and retrieve page content.

Two ToolSpec objects are exported:
  SEARCH_SPEC    (name="wikipedia_search")   — search by keyword, returns titles
  GET_PAGE_SPEC  (name="wikipedia_get_page") — fetch a page by title, returns content

Both are stateless. The `wikipedia` library is imported lazily inside the
wrapper functions so missing installs surface as clear error strings.
"""

from __future__ import annotations

import time

from ._base import ToolSpec
from ._errors import tool_error, tool_ok

_SEARCH_RETRIES = 2      # extra attempts after first failure
_RETRY_DELAY_S = 0.5    # seconds between retries

_MAX_CONTENT_CHARS = 12_000   # page content ceiling — pages can be 100 k+ chars
_MAX_SUMMARY_CHARS = 2_000    # summary ceiling


# ---------------------------------------------------------------------------
# Internal wrappers (patched in tests)
# ---------------------------------------------------------------------------

_USER_AGENT = "jean-michel/1.0 (local AI assistant; https://github.com/local/jean-michel)"


def _wiki_search(query: str, results: int = 5) -> list[str]:
    """Return a list of Wikipedia article titles matching *query*."""
    import wikipedia  # noqa: PLC0415
    wikipedia.set_user_agent(_USER_AGENT)
    return wikipedia.search(query, results=results)


def _wiki_get_page(title: str, language: str = "en") -> dict:
    """Fetch a Wikipedia page and return metadata + content (not yet truncated)."""
    import wikipedia  # noqa: PLC0415
    wikipedia.set_user_agent(_USER_AGENT)
    wikipedia.set_lang(language)
    page = wikipedia.page(title, auto_suggest=False)
    return {
        "title": page.title,
        "url": page.url,
        "summary": page.summary,
        "content": page.content,
    }


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _search_handler(query: str, results: int = 5) -> str:
    results_count = max(1, min(int(results), 10))
    last_err: Exception | None = None
    for attempt in range(_SEARCH_RETRIES + 1):
        try:
            titles = _wiki_search(query, results=results_count)
            head = " | ".join(t[:40] for t in titles[:3])
            summary = f"{len(titles)} pages for {query!r}" + (f": {head}" if head else "")
            return tool_ok(summary, query=query, results=titles)
        except Exception as e:  # noqa: BLE001
            last_err = e
            if "too busy" in str(e).lower() and attempt < _SEARCH_RETRIES:
                time.sleep(_RETRY_DELAY_S)
                continue
            break
    return tool_error(
        "wikipedia_search_failed",
        f"Wikipedia search failed: {last_err}",
        hint="If the article title is well-known, try wikipedia_get_page with the exact title directly.",
    )


def _get_page_handler(title: str, language: str = "en") -> str:
    try:
        data = _wiki_get_page(title, language=language)
        data["content"] = data["content"][:_MAX_CONTENT_CHARS]
        data["summary"] = data["summary"][:_MAX_SUMMARY_CHARS]
        return tool_ok(
            f"page {data['title']!r} ({len(data['content'])} chars)",
            title=data["title"],
            url=data["url"],
            page_summary=data["summary"],
            content=data["content"],
        )
    except Exception as e:
        # DisambiguationError carries an `options` list
        if hasattr(e, "options"):
            return tool_error(
                "ambiguous_title",
                f"'{title}' is ambiguous — multiple Wikipedia articles match.",
                options=list(e.options)[:10],
            )
        return tool_error("wikipedia_page_error", f"Wikipedia page error: {e}")


# ---------------------------------------------------------------------------
# ToolSpec declarations
# ---------------------------------------------------------------------------

SEARCH_SPEC = ToolSpec(
    name="wikipedia_search",
    description=(
        "Search Wikipedia for articles matching a query. "
        "Returns a ranked list of article titles (up to 10). "
        "Use this first to find the most relevant article title, "
        "then call wikipedia_get_page to retrieve its content."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search terms, e.g. 'Leaning Tower of Pisa' or 'photosynthesis'.",
            },
            "results": {
                "type": "integer",
                "description": "Number of results to return (1-10). Default 5.",
            },
        },
        "required": ["query"],
    },
    handler=_search_handler,
)

GET_PAGE_SPEC = ToolSpec(
    name="wikipedia_get_page",
    description=(
        "Retrieve the full content of a Wikipedia article by exact title. "
        "Returns the article summary and up to 12 000 characters of body content. "
        "If the title is ambiguous, a list of candidate titles is returned instead. "
        "Always call wikipedia_search first to find the correct title."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Exact Wikipedia article title as returned by wikipedia_search.",
            },
            "language": {
                "type": "string",
                "description": (
                    "Wikipedia language edition, e.g. 'en', 'fr', 'de'. "
                    "Default 'en'."
                ),
            },
        },
        "required": ["title"],
    },
    handler=_get_page_handler,
)
