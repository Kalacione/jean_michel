"""Tool: stackoverflow_search — Stack Exchange API on stackoverflow.com.

Surfaces questions matching a search query, prioritising answered ones.
For each question we return title, link, tags, score, answer_count, and
whether it has an accepted answer. The downstream agent can then call
`web_fetch` on the question link to read the actual content (question
body + top answers).

Auth is optional — without a key, the rate limit is 300 req/day per IP.
With `STACKEXCHANGE_KEY`, it goes up to 10 000 req/day.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from ._base import ToolSpec
from ._errors import tool_error, tool_ok

_API_BASE = "https://api.stackexchange.com/2.3"
_MAX_RESULTS = 10
_TIMEOUT_S = 10


def _key() -> str | None:
    return (os.environ.get("STACKEXCHANGE_KEY") or "").strip() or None


def _handler(
    query: str,
    tag: str | None = None,
    answered_only: bool = True,
) -> str:
    """Search Stack Overflow questions matching ``query``."""
    if not query or not query.strip():
        return tool_error("missing_query", "Provide a non-empty `query`.")

    params: dict[str, object] = {
        "site": "stackoverflow",
        "q": query.strip(),
        "order": "desc",
        "sort": "relevance",
        "pagesize": _MAX_RESULTS,
    }
    if tag and tag.strip():
        params["tagged"] = tag.strip()
    if answered_only:
        params["accepted"] = "True"

    k = _key()
    if k:
        params["key"] = k

    url = f"{_API_BASE}/search/advanced?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "jean-michel/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:  # noqa: S310
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return tool_error("api_call_failed", f"stackexchange request failed: {exc}")

    if "error_message" in raw:
        return tool_error("api_error", raw.get("error_message", "unknown"))

    items = []
    for it in (raw.get("items") or [])[:_MAX_RESULTS]:
        items.append({
            "title": it.get("title"),
            "link": it.get("link"),
            "tags": it.get("tags") or [],
            "score": it.get("score"),
            "answer_count": it.get("answer_count"),
            "is_answered": it.get("is_answered"),
            "has_accepted_answer": bool(it.get("accepted_answer_id")),
            "creation_date": it.get("creation_date"),  # epoch
        })
    has_more = bool(raw.get("has_more"))
    quota_remaining = raw.get("quota_remaining")
    summary = f"{len(items)} questions returned"
    if has_more:
        summary += " (has_more=true)"
    return tool_ok(
        summary,
        query=query,
        items=items,
        has_more=has_more,
        quota_remaining=quota_remaining,
    )


SPEC = ToolSpec(
    name="stackoverflow_search",
    description=(
        "Search Stack Overflow questions matching a query, via the Stack "
        "Exchange API. Returns up to 10 questions with title, link, tags, "
        "score, answer_count, and whether they have an accepted answer. "
        "Best for troubleshooting ('TypeError NoneType subscript'), "
        "how-to recipes ('how to debounce in React'), or finding the "
        "community consensus on a known problem. Follow up with "
        "`web_fetch` on the question `link` to read the full question + "
        "top answers. Authentication is optional (set STACKEXCHANGE_KEY "
        "for higher quota)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search text. Stack Overflow indexes title + body. "
                    "Quote phrases to match exactly. Examples: "
                    "'\"asyncio.gather\" exception handling', "
                    "'unexpected token import'."
                ),
            },
            "tag": {
                "type": "string",
                "description": (
                    "Optional tag filter (e.g. 'python', 'react', 'docker'). "
                    "Narrows results to questions carrying that tag."
                ),
            },
            "answered_only": {
                "type": "boolean",
                "description": (
                    "If True (default), restrict to questions that have an "
                    "accepted answer. Set False to also see open questions "
                    "(useful for known-but-unsolved problems)."
                ),
            },
        },
        "required": ["query"],
    },
    handler=_handler,
)
