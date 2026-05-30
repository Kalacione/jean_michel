"""Tests for the image_search tool (SearXNG images category).

SearXNG is mocked → offline : we patch the health check + the raw search to
canned image results, then assert the handler's shaping + de-duplication.
"""
from __future__ import annotations

import json
from pathlib import Path

from jeanmichel.tools import build_registry, image_search


def test_image_search_shapes_and_dedupes(monkeypatch):
    monkeypatch.setattr(image_search._ws, "_ensure_running", lambda: None)
    raw = [
        {"title": "Cat A", "img_src": "https://x/a.jpg", "thumbnail_src": "https://x/a_t.jpg",
         "url": "https://x/page-a", "source": "x"},
        {"title": "dup", "img_src": "https://x/a.jpg", "thumbnail_src": "https://x/a_t.jpg",
         "url": "https://x/page-a2", "source": "x"},  # same img_src → dropped
        {"title": "Cat B", "img_src": "https://y/b.png", "thumbnail_src": "",
         "url": "https://y/page-b", "source": "y"},
    ]
    monkeypatch.setattr(image_search, "_do_image_search", lambda q, lang, n: raw)

    out = json.loads(image_search._handler("cats", results=5))
    hits = out["results"]
    assert [h["image_url"] for h in hits] == ["https://x/a.jpg", "https://y/b.png"]  # deduped
    assert hits[0]["thumbnail_url"] == "https://x/a_t.jpg"
    assert hits[1]["thumbnail_url"] == "https://y/b.png"  # falls back to img_src when no thumb
    assert hits[1]["source_page"] == "https://y/page-b"
    assert out["query"] == "cats"


def test_image_search_searxng_unavailable(monkeypatch):
    monkeypatch.setattr(image_search._ws, "_ensure_running", lambda: "down")
    out = json.loads(image_search._handler("cats"))
    assert out["error_code"] == "searxng_unavailable"


def test_image_search_in_registry():
    assert "image_search" in build_registry(Path("/tmp/jm-img-test"), conv_id="c1")
