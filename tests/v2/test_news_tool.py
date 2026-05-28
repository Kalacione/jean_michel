"""Tests for the news_latest / news_archive tools (NewsData.io).

The tools hit an external HTTP API. We stub `urllib.request.urlopen` to
return a canned JSON payload so the tests run offline and deterministically.
"""
from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from jeanmichel.tools import news


# ---- canned response -----------------------------------------------------


_OK_PAYLOAD = {
    "status": "success",
    "totalResults": 42,
    "results": [
        {
            "article_id": "abc",
            "title": "Mars rover finds water",
            "link": "https://example.com/mars",
            "description": "The rover detected …",
            "pubDate": "2026-05-28 09:00:00",
            "source_id": "nyt",
            "source_name": "New York Times",
            "language": "english",
        },
        {
            "article_id": "def",
            "title": "AI bill passes",
            "link": "https://example.com/ai",
            "description": "The bill outlines …",
            "pubDate": "2026-05-28 08:30:00",
            "source_id": "bbc",
            "source_name": "BBC",
            "language": "english",
        },
    ],
    "nextPage": "page-token-2",
}


def _fake_urlopen(payload: dict):
    """Return a context manager that mimics urllib.request.urlopen(...).read()."""

    class _Resp:
        def __init__(self, data: bytes):
            self._data = data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self._data

    return lambda url, timeout=10: _Resp(json.dumps(payload).encode("utf-8"))


# ---- news_latest ---------------------------------------------------------


def test_latest_missing_api_key(monkeypatch):
    monkeypatch.delenv("NEWSDATA_API_KEY", raising=False)
    result = json.loads(news._handler_latest(query="mars"))
    assert result["error_code"] == "api_key_missing"


def test_latest_missing_filter(monkeypatch):
    monkeypatch.setenv("NEWSDATA_API_KEY", "fake")
    result = json.loads(news._handler_latest())
    assert result["error_code"] == "missing_filter"


def test_latest_returns_formatted_articles(monkeypatch):
    monkeypatch.setenv("NEWSDATA_API_KEY", "fake")
    with patch.object(news.urllib.request, "urlopen", _fake_urlopen(_OK_PAYLOAD)):
        result = json.loads(news._handler_latest(query="mars"))
    assert "error" not in result
    assert result["summary"].startswith("2 articles returned")
    assert "totalResults=42" in result["summary"]
    assert len(result["articles"]) == 2
    a = result["articles"][0]
    assert a["title"] == "Mars rover finds water"
    assert a["source"] == "New York Times"
    assert a["date"] == "2026-05-28 09:00:00"
    assert a["link"] == "https://example.com/mars"
    assert result["next_page"] == "page-token-2"


def test_latest_passes_params(monkeypatch):
    monkeypatch.setenv("NEWSDATA_API_KEY", "fake-key")
    captured_url: list[str] = []

    def _spy(url, timeout=10):
        captured_url.append(url)
        return _fake_urlopen(_OK_PAYLOAD)(url, timeout)

    with patch.object(news.urllib.request, "urlopen", _spy):
        news._handler_latest(query="ukraine", language="fr", country="fr,ca")

    assert captured_url, "urlopen should have been called"
    url = captured_url[0]
    assert "q=ukraine" in url
    assert "language=fr" in url
    assert "country=fr%2Cca" in url
    assert "apikey=fake-key" in url
    assert "/api/1/latest" in url
    assert "removeduplicate=1" in url


# ---- news_archive --------------------------------------------------------


def test_archive_invalid_date_format(monkeypatch):
    monkeypatch.setenv("NEWSDATA_API_KEY", "fake")
    result = json.loads(news._handler_archive(query="mars", from_date="2026/05/01"))
    assert result["error_code"] == "invalid_date"


def test_archive_accepts_well_formed_dates(monkeypatch):
    monkeypatch.setenv("NEWSDATA_API_KEY", "fake")
    captured_url: list[str] = []

    def _spy(url, timeout=10):
        captured_url.append(url)
        return _fake_urlopen(_OK_PAYLOAD)(url, timeout)

    with patch.object(news.urllib.request, "urlopen", _spy):
        result = json.loads(news._handler_archive(
            query="cop28", from_date="2025-11-01", to_date="2025-12-15"
        ))

    assert "error" not in result
    url = captured_url[0]
    assert "/api/1/archive" in url
    assert "from_date=2025-11-01" in url
    assert "to_date=2025-12-15" in url


def test_archive_missing_filter(monkeypatch):
    monkeypatch.setenv("NEWSDATA_API_KEY", "fake")
    result = json.loads(news._handler_archive(from_date="2025-01-01"))
    assert result["error_code"] == "missing_filter"


# ---- API errors ----------------------------------------------------------


def test_api_returns_error_status(monkeypatch):
    monkeypatch.setenv("NEWSDATA_API_KEY", "fake")
    payload = {
        "status": "error",
        "results": {"message": "Invalid API key"},
    }
    with patch.object(news.urllib.request, "urlopen", _fake_urlopen(payload)):
        result = json.loads(news._handler_latest(query="x"))
    assert result["error_code"] == "api_error"


def test_http_failure(monkeypatch):
    monkeypatch.setenv("NEWSDATA_API_KEY", "fake")

    def _boom(url, timeout=10):
        raise OSError("network down")

    with patch.object(news.urllib.request, "urlopen", _boom):
        result = json.loads(news._handler_latest(query="x"))
    assert result["error_code"] == "api_call_failed"


# ---- ToolSpec sanity -----------------------------------------------------


def test_specs_are_registered():
    assert news.LATEST_SPEC.name == "news_latest"
    assert news.ARCHIVE_SPEC.name == "news_archive"
    assert news.LATEST_SPEC.handler is news._handler_latest
    assert news.ARCHIVE_SPEC.handler is news._handler_archive
