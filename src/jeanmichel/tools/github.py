"""Tools: github_search_code + github_search_repos — GitHub REST API.

Two tools exposing the GitHub `/search/*` endpoints. Authentication is read
from the ``GITHUB_TOKEN`` env var (fine-grained PAT, read-only on public
repos is enough). Without a token, `/search/code` fails (GitHub requires
auth for it) and `/search/repositories` falls back to 60 req/h
unauthenticated quota.

Pattern : these tools surface lists of code files / repos with their URLs.
The downstream agent (typically code-fetcher) follows up with `web_fetch`
on the most relevant raw URLs to read full file content — exactly the
same pattern as `news_latest` + `web_fetch`.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from ._base import ToolSpec
from ._errors import tool_error, tool_ok

_API_BASE = "https://api.github.com"
_MAX_RESULTS = 10
_TIMEOUT_S = 10


def _token() -> str | None:
    return (os.environ.get("GITHUB_TOKEN") or "").strip() or None


def _api_get(endpoint: str, params: dict[str, object]) -> dict:
    """Authenticated GET to the GitHub REST API. Raises on non-2xx."""
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    url = f"{_API_BASE}{endpoint}?{qs}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "jean-michel/1.0",
    }
    tok = _token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# github_search_code
# ---------------------------------------------------------------------------


def _handler_search_code(query: str, language: str | None = None) -> str:
    """Search code across public GitHub repositories."""
    if not query or not query.strip():
        return tool_error("missing_query", "Provide a non-empty `query`.")
    if _token() is None:
        return tool_error(
            "api_key_missing",
            "GITHUB_TOKEN env var not set. /search/code requires authentication. "
            "Generate a fine-grained PAT (read-only on public repos) at "
            "https://github.com/settings/tokens.",
        )
    q = query.strip()
    if language:
        q = f"{q} language:{language.strip()}"
    try:
        raw = _api_get("/search/code", {"q": q, "per_page": _MAX_RESULTS})
    except Exception as exc:  # noqa: BLE001
        return tool_error("api_call_failed", f"GitHub /search/code failed: {exc}")

    items = []
    for it in (raw.get("items") or [])[:_MAX_RESULTS]:
        repo = it.get("repository") or {}
        items.append({
            "repo": repo.get("full_name"),
            "path": it.get("path"),
            "name": it.get("name"),
            "html_url": it.get("html_url"),
            "raw_url": _raw_url_from_html(it.get("html_url")),
            "repo_url": repo.get("html_url"),
            "score": it.get("score"),
        })
    total = int(raw.get("total_count") or 0)
    summary = f"{len(items)} code hits returned (total_count={total})"
    return tool_ok(summary, query=q, items=items)


def _raw_url_from_html(html_url: str | None) -> str | None:
    """Convert https://github.com/<repo>/blob/<sha>/<path> → raw.githubusercontent.com."""
    if not html_url or "github.com/" not in html_url or "/blob/" not in html_url:
        return None
    # Replace github.com/<repo>/blob/<sha>/<path>
    # with    raw.githubusercontent.com/<repo>/<sha>/<path>
    after = html_url.split("github.com/", 1)[1]
    if "/blob/" not in after:
        return None
    parts = after.split("/blob/", 1)
    return f"https://raw.githubusercontent.com/{parts[0]}/{parts[1]}"


SEARCH_CODE_SPEC = ToolSpec(
    name="github_search_code",
    description=(
        "Search for code snippets across public GitHub repositories via the "
        "/search/code endpoint. Returns up to 10 file hits with repo, path, "
        "and a `raw_url` you can feed to `web_fetch` to read the full file "
        "content (avoids re-querying for the same file). "
        "REQUIRES authentication — set GITHUB_TOKEN. "
        "GitHub search supports operators: `repo:owner/name`, `language:python`, "
        "`extension:py`, `filename:foo.py`, `in:file`, `in:path`."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "GitHub code-search query. Examples: "
                    "'\"fastapi streaming\" language:python', "
                    "'asyncio.gather repo:tiangolo/fastapi'."
                ),
            },
            "language": {
                "type": "string",
                "description": (
                    "Optional language filter appended as `language:<lang>` "
                    "to the query (e.g. 'python', 'typescript', 'go')."
                ),
            },
        },
        "required": ["query"],
    },
    handler=_handler_search_code,
)


# ---------------------------------------------------------------------------
# github_search_repos
# ---------------------------------------------------------------------------


def _handler_search_repos(
    query: str,
    sort: str | None = None,
    language: str | None = None,
) -> str:
    """Search public repositories by relevance, stars, or recent activity."""
    if not query or not query.strip():
        return tool_error("missing_query", "Provide a non-empty `query`.")
    q = query.strip()
    if language:
        q = f"{q} language:{language.strip()}"

    params: dict[str, object] = {"q": q, "per_page": _MAX_RESULTS}
    if sort and sort.strip() in ("stars", "forks", "updated", "help-wanted-issues"):
        params["sort"] = sort.strip()
    try:
        raw = _api_get("/search/repositories", params)
    except Exception as exc:  # noqa: BLE001
        return tool_error("api_call_failed", f"GitHub /search/repositories failed: {exc}")

    items = []
    for it in (raw.get("items") or [])[:_MAX_RESULTS]:
        items.append({
            "full_name": it.get("full_name"),
            "description": it.get("description"),
            "html_url": it.get("html_url"),
            "stars": it.get("stargazers_count"),
            "language": it.get("language"),
            "updated_at": it.get("updated_at"),
            "open_issues": it.get("open_issues_count"),
            "archived": it.get("archived"),
        })
    total = int(raw.get("total_count") or 0)
    summary = f"{len(items)} repos returned (total_count={total})"
    return tool_ok(summary, query=q, items=items)


SEARCH_REPOS_SPEC = ToolSpec(
    name="github_search_repos",
    description=(
        "Search public GitHub repositories by keyword, with optional "
        "sort by stars / forks / last update. Returns up to 10 repos with "
        "stars, primary language, update date and URL. Useful for finding "
        "popular libraries, comparing alternatives, or discovering "
        "well-maintained projects in a niche. Anonymous access is rate-limited "
        "(60 req/h) — set GITHUB_TOKEN for 5000 req/h. "
        "Same GitHub search operators as /search/code apply."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Repository search query. Examples: "
                    "'web framework language:python', 'orm', "
                    "'streaming json parser language:rust'."
                ),
            },
            "sort": {
                "type": "string",
                "enum": ["stars", "forks", "updated", "help-wanted-issues"],
                "description": (
                    "Optional sort criterion. Default is GitHub relevance scoring."
                ),
            },
            "language": {
                "type": "string",
                "description": (
                    "Optional language filter appended as `language:<lang>` "
                    "to the query."
                ),
            },
        },
        "required": ["query"],
    },
    handler=_handler_search_repos,
)
