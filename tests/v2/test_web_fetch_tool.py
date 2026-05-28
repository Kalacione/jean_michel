"""Tests for the web_fetch tool.

We stub `urllib.request.urlopen` to return canned HTML so tests stay
offline and deterministic. readability-lxml is exercised for real ; the
extracted text is validated to ensure markup is stripped.
"""
from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from jeanmichel.tools import web_fetch


# ---- canned HTTP response helpers ---------------------------------------


def _fake_urlopen(body: bytes, content_type: str = "text/html; charset=utf-8",
                  final_url: str | None = None, status: int = 200):
    """Return a fake urlopen callable returning the given body + content-type."""

    class _Resp:
        def __init__(self):
            self.headers = {"content-type": content_type, "content-length": str(len(body))}
            self._body = body
            self._final_url = final_url

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n: int | None = None):
            if n is None or n >= len(self._body):
                return self._body
            return self._body[:n]

        def geturl(self):
            return self._final_url or "https://example.com/page"

    return lambda req, timeout=10: _Resp()


_GOOD_HTML = """
<!DOCTYPE html>
<html>
  <head><title>Mars rover finds water — NASA</title></head>
  <body>
    <nav><a href="/">Home</a> <a href="/about">About</a></nav>
    <header><h1>Site header that should be stripped</h1></header>
    <article>
      <h1>Mars rover finds water</h1>
      <p>The Perseverance rover has detected unmistakable signs of water in
      the Jezero crater. Scientists say this confirms decades of theoretical
      modelling and could reshape the search for past microbial life.</p>
      <p>The discovery was announced at a press conference yesterday. The
      team plans further drilling operations to collect mineral samples for
      eventual return to Earth via the upcoming sample-return mission.</p>
      <p>Lead researcher Dr. Smith added that the find may have implications
      for human exploration: a reliable subsurface water source would
      drastically reduce the payload required for crewed missions.</p>
    </article>
    <footer>Footer content with copyright notice that should be stripped</footer>
    <script>console.log("tracking");</script>
  </body>
</html>
""".encode("utf-8")


# ---- happy path ---------------------------------------------------------


def test_fetch_extracts_article_text():
    with patch.object(web_fetch.urllib.request, "urlopen", _fake_urlopen(_GOOD_HTML)):
        out = json.loads(web_fetch._handler(url="https://example.com/mars"))
    assert "error" not in out
    assert "Mars rover finds water" in out["title"]
    # Article body present (the real signal that extraction worked).
    assert "Perseverance" in out["content"]
    assert "Jezero crater" in out["content"]
    assert "Dr. Smith" in out["content"]
    # Scripts get stripped by readability.
    assert "console.log" not in out["content"]
    # Output is plain text — no residual HTML tags.
    assert "<p>" not in out["content"]
    assert "<article>" not in out["content"]
    assert out["source_url"] == "https://example.com/page"
    assert out["truncated"] is False


def test_summary_includes_title_and_size():
    with patch.object(web_fetch.urllib.request, "urlopen", _fake_urlopen(_GOOD_HTML)):
        out = json.loads(web_fetch._handler(url="https://example.com/mars"))
    assert out["summary"].startswith("Mars rover finds water")
    assert "fetched" in out["summary"]
    assert "chars" in out["summary"]


# ---- input validation ---------------------------------------------------


def test_rejects_non_http_scheme():
    out = json.loads(web_fetch._handler(url="ftp://example.com/file"))
    assert out["error_code"] == "invalid_scheme"


def test_rejects_data_uri():
    out = json.loads(web_fetch._handler(url="data:text/html,<p>hi</p>"))
    assert out["error_code"] == "invalid_scheme"


def test_rejects_file_uri():
    out = json.loads(web_fetch._handler(url="file:///etc/passwd"))
    assert out["error_code"] == "invalid_scheme"


def test_rejects_url_without_host():
    out = json.loads(web_fetch._handler(url="https:///path-only"))
    assert out["error_code"] == "invalid_url"


# ---- content-type filtering ---------------------------------------------


def test_rejects_binary_content_type():
    with patch.object(
        web_fetch.urllib.request, "urlopen",
        _fake_urlopen(b"%PDF-1.7", content_type="application/pdf"),
    ):
        out = json.loads(web_fetch._handler(url="https://example.com/x.pdf"))
    assert out["error_code"] == "not_text"


def test_accepts_xhtml():
    with patch.object(
        web_fetch.urllib.request, "urlopen",
        _fake_urlopen(_GOOD_HTML, content_type="application/xhtml+xml"),
    ):
        out = json.loads(web_fetch._handler(url="https://example.com/x"))
    assert "error" not in out


def test_accepts_text_plain_returns_raw():
    """raw.githubusercontent.com returns text/plain for source files —
    bypass readability and return body as-is."""
    body = b"def hello():\n    print('hi')\n\n# end\n"
    with patch.object(
        web_fetch.urllib.request, "urlopen",
        _fake_urlopen(body, content_type="text/plain; charset=utf-8"),
    ):
        out = json.loads(web_fetch._handler(
            url="https://raw.githubusercontent.com/foo/bar/main/x.py"
        ))
    assert "error" not in out
    assert out["content"] == "def hello():\n    print('hi')\n\n# end\n"
    assert out["content_type"] == "text/plain"
    assert out["title"] == ""  # no extraction → no title
    assert out["truncated"] is False


def test_accepts_text_markdown():
    body = b"# Heading\n\nSome **bold** content.\n"
    with patch.object(
        web_fetch.urllib.request, "urlopen",
        _fake_urlopen(body, content_type="text/markdown"),
    ):
        out = json.loads(web_fetch._handler(url="https://example.com/README.md"))
    assert "error" not in out
    assert "# Heading" in out["content"]


def test_accepts_json():
    body = b'{"name": "fastapi", "version": "0.115.0"}'
    with patch.object(
        web_fetch.urllib.request, "urlopen",
        _fake_urlopen(body, content_type="application/json"),
    ):
        out = json.loads(web_fetch._handler(url="https://example.com/api/x"))
    assert "error" not in out
    assert "fastapi" in out["content"]


def test_rejects_image_content_type():
    with patch.object(
        web_fetch.urllib.request, "urlopen",
        _fake_urlopen(b"\x89PNG\r\n", content_type="image/png"),
    ):
        out = json.loads(web_fetch._handler(url="https://example.com/x.png"))
    assert out["error_code"] == "not_text"


# ---- network errors -----------------------------------------------------


def test_handles_http_error():
    import urllib.error

    def _raise(req, timeout=10):
        raise urllib.error.HTTPError(
            "https://example.com/x", 404, "Not Found", hdrs=None, fp=None,
        )

    with patch.object(web_fetch.urllib.request, "urlopen", _raise):
        out = json.loads(web_fetch._handler(url="https://example.com/x"))
    assert out["error_code"] == "http_error"
    assert "404" in out["error"]


def test_handles_generic_failure():
    def _raise(req, timeout=10):
        raise OSError("network down")

    with patch.object(web_fetch.urllib.request, "urlopen", _raise):
        out = json.loads(web_fetch._handler(url="https://example.com/x"))
    assert out["error_code"] == "fetch_failed"


# ---- truncation ---------------------------------------------------------


def test_truncates_long_content():
    long_paragraph = "<p>" + ("word " * 25_000) + "</p>"  # ~125k chars
    huge_html = ("<html><body><article>" + long_paragraph + "</article></body></html>").encode()
    with patch.object(web_fetch.urllib.request, "urlopen", _fake_urlopen(huge_html)):
        out = json.loads(web_fetch._handler(url="https://example.com/x"))
    assert out["truncated"] is True
    assert "truncated" in out["summary"]
    assert "[… truncated …]" in out["content"]
    assert len(out["content"]) <= 80_500  # cap + marker


# ---- ToolSpec sanity ----------------------------------------------------


def test_spec_is_registered():
    assert web_fetch.SPEC.name == "web_fetch"
    assert web_fetch.SPEC.handler is web_fetch._handler
    assert "url" in web_fetch.SPEC.parameters["properties"]
    assert "url" in web_fetch.SPEC.parameters["required"]
