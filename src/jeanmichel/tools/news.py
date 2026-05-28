"""Tools: news_latest + news_archive — query the NewsData.io API.

Two distinct tools sharing the same backend :

- ``news_latest`` hits `/api/1/latest` (live breaking news, 48 h window on the
  free tier — and a 12 h delay on the free tier as of 2026-05).
- ``news_archive`` hits `/api/1/archive` with `from_date` / `to_date` for
  retrieving historical articles within a date range.

Authentication : NewsData.io API key in the `apikey` query parameter, sourced
from the ``NEWSDATA_API_KEY`` env var. The tools fail fast with a clear
error when the key is missing — they do not silently degrade.

Response shape : we surface only the subset useful to the LLM (title, source,
date, link, short description), not the raw NewsData payload (which includes
AI sentiment/tags only available on paid plans). Article count is capped at
``_MAX_ARTICLES`` to keep the tool_call result under a reasonable token cost ;
the LLM can paginate by passing ``page=<nextPage>`` to fetch more.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

from ._base import ToolSpec
from ._errors import tool_error, tool_ok

_BASE_URL = "https://newsdata.io/api/1"
_MAX_ARTICLES = 10  # = one free-tier credit on NewsData.io
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _api_key() -> str | None:
    return (os.environ.get("NEWSDATA_API_KEY") or "").strip() or None


def _http_get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _build_url(endpoint: str, params: dict[str, object]) -> str:
    encoded = urllib.parse.urlencode(
        {k: v for k, v in params.items() if v is not None and v != ""}
    )
    return f"{_BASE_URL}/{endpoint}?{encoded}"


def _format_articles(raw: dict) -> tuple[str, list[dict], str | None]:
    """Return (summary, articles, next_page_token) from a NewsData.io response."""
    status = raw.get("status")
    if status != "success":
        msg = raw.get("results", {}).get("message") or raw.get("message") or "unknown"
        raise RuntimeError(f"newsdata.io returned status={status!r}: {msg}")

    total = int(raw.get("totalResults") or 0)
    results = raw.get("results") or []
    articles: list[dict] = []
    for r in results[:_MAX_ARTICLES]:
        articles.append({
            "title": r.get("title"),
            "source": r.get("source_name") or r.get("source_id"),
            "date": r.get("pubDate"),
            "link": r.get("link"),
            "description": r.get("description"),
            "language": r.get("language"),
        })
    next_page = raw.get("nextPage")
    summary = f"{len(articles)} articles returned (totalResults={total})"
    return summary, articles, next_page


def _call(endpoint: str, params: dict[str, object]) -> str:
    key = _api_key()
    if key is None:
        return tool_error(
            "api_key_missing",
            "NEWSDATA_API_KEY env var not set. Sign up at newsdata.io to obtain a free key.",
        )
    params["apikey"] = key
    try:
        raw = _http_get_json(_build_url(endpoint, params))
    except Exception as exc:  # noqa: BLE001
        return tool_error("api_call_failed", f"newsdata.io request failed: {exc}")
    try:
        summary, articles, next_page = _format_articles(raw)
    except RuntimeError as exc:
        return tool_error("api_error", str(exc))
    return tool_ok(summary, articles=articles, next_page=next_page)


# ---------------------------------------------------------------------------
# news_latest
# ---------------------------------------------------------------------------


def _handler_latest(
    query: str | None = None,
    language: str | None = None,
    country: str | None = None,
    category: str | None = None,
    domain: str | None = None,
    page: str | None = None,
) -> str:
    """Fetch breaking news from the past 48 h."""
    if not (query or country or category or domain):
        return tool_error(
            "missing_filter",
            "Provide at least one of: query, country, category, domain.",
        )
    params: dict[str, object] = {
        "q": query,
        "language": language,
        "country": country,
        "category": category,
        "domain": domain,
        "page": page,
        "size": _MAX_ARTICLES,
        "removeduplicate": 1,
    }
    return _call("latest", params)


LATEST_SPEC = ToolSpec(
    name="news_latest",
    description=(
        "Fetch breaking news from NewsData.io (past 48 h). "
        "At least ONE filter is required (query, country, category, or domain). "
        "Note: free-tier responses have a ~12 h delay — for true real-time, "
        "use web_search. Returns up to 10 articles per call with title, source, "
        "date, link, and short description. Paginate via the next_page token."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Keyword search (free text). NewsData.io supports basic "
                    "operators: AND, OR, NOT, quotes for phrases. "
                    "100-char limit on the free tier."
                ),
            },
            "language": {
                "type": "string",
                "description": (
                    "ISO 639-1 language code (e.g. 'en', 'fr', 'es'). "
                    "Multiple values comma-separated."
                ),
            },
            "country": {
                "type": "string",
                "description": (
                    "ISO 3166-1 alpha-2 country code (e.g. 'us', 'fr', 'ca'). "
                    "Multiple values comma-separated."
                ),
            },
            "category": {
                "type": "string",
                "description": (
                    "Comma-separated list of: business, entertainment, "
                    "environment, food, health, politics, science, sports, "
                    "technology, top, tourism, world."
                ),
            },
            "domain": {
                "type": "string",
                "description": (
                    "Comma-separated source domains to restrict to "
                    "(e.g. 'bbc.com,reuters.com')."
                ),
            },
            "page": {
                "type": "string",
                "description": "next_page token from a previous response for pagination.",
            },
        },
        "required": [],
    },
    handler=_handler_latest,
)


# ---------------------------------------------------------------------------
# news_archive
# ---------------------------------------------------------------------------


def _handler_archive(
    query: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    language: str | None = None,
    country: str | None = None,
    category: str | None = None,
    domain: str | None = None,
    page: str | None = None,
) -> str:
    """Search the news archive over a date range."""
    if not (query or country or category or domain):
        return tool_error(
            "missing_filter",
            "Provide at least one of: query, country, category, domain.",
        )
    for label, val in (("from_date", from_date), ("to_date", to_date)):
        if val is not None and not _DATE_RE.match(val):
            return tool_error(
                "invalid_date",
                f"{label} must be YYYY-MM-DD (got {val!r}).",
            )
    params: dict[str, object] = {
        "q": query,
        "from_date": from_date,
        "to_date": to_date,
        "language": language,
        "country": country,
        "category": category,
        "domain": domain,
        "page": page,
        "size": _MAX_ARTICLES,
        "removeduplicate": 1,
    }
    return _call("archive", params)


ARCHIVE_SPEC = ToolSpec(
    name="news_archive",
    description=(
        "Search the NewsData.io archive over a date range. "
        "At least ONE filter is required (query, country, category, or domain). "
        "Provide from_date and/or to_date in YYYY-MM-DD format to narrow the "
        "window. Free-tier archive depth is limited (paid plans extend back to "
        "2014-2018). Returns up to 10 articles per call. Paginate via next_page."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keyword search (free text). 100-char limit on the free tier.",
            },
            "from_date": {
                "type": "string",
                "description": "Earliest publication date (YYYY-MM-DD).",
            },
            "to_date": {
                "type": "string",
                "description": "Latest publication date (YYYY-MM-DD).",
            },
            "language": {
                "type": "string",
                "description": "ISO 639-1 language code(s), comma-separated.",
            },
            "country": {
                "type": "string",
                "description": "ISO 3166-1 alpha-2 country code(s), comma-separated.",
            },
            "category": {
                "type": "string",
                "description": (
                    "Comma-separated list of: business, entertainment, "
                    "environment, food, health, politics, science, sports, "
                    "technology, top, tourism, world."
                ),
            },
            "domain": {
                "type": "string",
                "description": "Comma-separated source domains to restrict to.",
            },
            "page": {
                "type": "string",
                "description": "next_page token from a previous response for pagination.",
            },
        },
        "required": [],
    },
    handler=_handler_archive,
)
