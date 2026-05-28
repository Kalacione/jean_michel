"""Tool: web_fetch — download a URL and return the cleaned article text.

Designed as the natural follow-up to ``news_latest`` / ``news_archive`` /
``web_search`` : those tools surface URLs ; ``web_fetch`` reads the actual
page. Avoids burning N news-API credits when the LLM just wants to read a
couple of articles surfaced by a single search.

Article extraction goes through ``readability-lxml`` (the same algorithm
used by Pocket / Instapaper). We then strip the residual HTML to plain
text with the stdlib parser — no extra dep beyond readability itself.

Hard limits :
- Only `http` / `https` URLs.
- Max 5 MB downloaded (cap on read).
- Max 80 000 chars returned (truncated with marker).
- 10 s socket timeout.
"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from readability import Document  # type: ignore[import-untyped]

from ._base import ToolSpec
from ._errors import tool_error, tool_ok

_TIMEOUT_S = 10
_MAX_BYTES = 5_000_000
_MAX_OUTPUT_CHARS = 80_000
_USER_AGENT = "jean-michel/1.0 (+https://github.com/, readability article fetch)"

# HTML tags whose start triggers a newline in the flattened output, to keep
# paragraph structure visible after extraction.
_BLOCK_TAGS = frozenset({
    "p", "br", "div", "section", "article",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "blockquote", "pre", "tr",
})


class _TextExtractor(HTMLParser):
    """Flatten an HTML fragment to plain text with paragraph breaks."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def handle_starttag(self, tag: str, attrs):  # noqa: ANN001
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def get_text(self) -> str:
        text = "".join(self._parts)
        # Collapse runs of blank lines to at most one blank line.
        text = re.sub(r"\n[ \t]*\n+", "\n\n", text)
        # Collapse repeated spaces (but keep newlines).
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip()


def _html_to_text(html: str) -> str:
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
    except Exception:  # noqa: BLE001 — never crash on malformed HTML
        return html
    return extractor.get_text()


def _handler(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return tool_error(
            "invalid_scheme",
            f"URL scheme {parsed.scheme!r} not allowed. Use http or https.",
        )
    if not parsed.netloc:
        return tool_error("invalid_url", f"URL missing host: {url!r}")

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:  # noqa: S310
            ctype_full = (resp.headers.get("content-type") or "").lower()
            ctype = ctype_full.split(";", 1)[0].strip()
            # Branch on content-type :
            #   text/html, application/xhtml+xml → readability extraction
            #   text/* and application/json      → return as-is (source code,
            #                                       markdown, plain text, json)
            #   anything else                    → refuse (binary, PDF, video…)
            is_html = ctype in ("text/html", "application/xhtml+xml")
            is_plain = ctype.startswith("text/") or ctype == "application/json"
            if not is_html and not is_plain:
                return tool_error(
                    "not_text",
                    f"Content-Type {ctype_full!r} is not text — refusing to extract.",
                )
            raw_bytes = resp.read(_MAX_BYTES)
            final_url = resp.geturl()
    except urllib.error.HTTPError as exc:
        return tool_error("http_error", f"HTTP {exc.code} on {url}: {exc.reason}")
    except Exception as exc:  # noqa: BLE001
        return tool_error("fetch_failed", f"fetch failed for {url}: {exc}")

    raw_text = raw_bytes.decode("utf-8", errors="replace")
    title = ""

    if is_html:
        try:
            doc = Document(raw_text)
            title = (doc.short_title() or doc.title() or "").strip()
            summary_html = doc.summary()
        except Exception as exc:  # noqa: BLE001
            return tool_error("extraction_failed", f"readability failed: {exc}")
        text = _html_to_text(summary_html)
    else:
        # Plain text / source code / markdown / json — no extraction, just
        # return the body as-is (with the standard size cap below).
        text = raw_text

    truncated = False
    if len(text) > _MAX_OUTPUT_CHARS:
        text = text[:_MAX_OUTPUT_CHARS].rstrip() + "\n\n[… truncated …]"
        truncated = True

    summary = f"fetched {len(text)} chars from {parsed.netloc}"
    if truncated:
        summary += " (truncated)"
    if title:
        summary = f"{title} — {summary}"

    return tool_ok(
        summary,
        title=title,
        content=text,
        source_url=final_url,
        content_type=ctype,
        truncated=truncated,
    )


SPEC = ToolSpec(
    name="web_fetch",
    description=(
        "Download an http/https URL and return its textual content. "
        "Designed for following up on URLs surfaced by news_latest, "
        "news_archive, web_search, or github_search_code — instead of "
        "re-querying with new keywords (which costs an API credit on news "
        "endpoints), you fetch the specific articles or files that look "
        "most relevant. "
        "Behaviour by Content-Type : "
        "(a) `text/html` / `application/xhtml+xml` → readability extraction "
        "(strips navigation, ads, footers ; returns plain text body) ; "
        "(b) `text/*` and `application/json` → returned as-is (raw source "
        "code from raw.githubusercontent.com, markdown, plain text, JSON) ; "
        "(c) binary types (PDF, images, video) → error. "
        "Output is capped at ~80 000 characters (truncation marker added)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": (
                    "The http or https URL to fetch. Typically taken from "
                    "the `link` field of a news_latest / news_archive result, "
                    "or the `url` field of a web_search result."
                ),
            },
        },
        "required": ["url"],
    },
    handler=_handler,
)
