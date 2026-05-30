"""Tests for image_fetch — download a web image into the workspace (SSRF-guarded).

Network is mocked → offline. We verify the SSRF/scheme guards, the image-only
Content-Type check, and that a successful fetch lands in the workspace.
"""
from __future__ import annotations

import json

from jeanmichel.tools import image_fetch
from jeanmichel.tools._workspace import workspace_root_for


class _FakeResp:
    def __init__(self, data: bytes, ctype: str) -> None:
        self._data = data
        self.headers = {"Content-Type": ctype}

    def read(self, n: int = -1) -> bytes:
        return self._data if n < 0 else self._data[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_image_fetch_blocks_private_host(tmp_path):
    out = json.loads(image_fetch.make_spec(tmp_path).handler("http://127.0.0.1/x.png"))
    assert out["error_code"] == "blocked_host"


def test_image_fetch_rejects_non_http(tmp_path):
    out = json.loads(image_fetch.make_spec(tmp_path).handler("ftp://example.com/x.png"))
    assert out["error_code"] == "bad_url"


def test_image_fetch_saves_into_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(image_fetch, "_is_blocked_host", lambda h: False)
    monkeypatch.setattr(
        image_fetch.urllib.request, "urlopen",
        lambda req, timeout=None: _FakeResp(b"\x89PNGdata", "image/png"),
    )
    out = json.loads(image_fetch.make_spec(tmp_path).handler("https://example.com/cat.png"))
    assert out["path"] == "cat.png"
    assert (workspace_root_for(tmp_path) / "cat.png").read_bytes() == b"\x89PNGdata"


def test_image_fetch_rejects_non_image(tmp_path, monkeypatch):
    monkeypatch.setattr(image_fetch, "_is_blocked_host", lambda h: False)
    monkeypatch.setattr(
        image_fetch.urllib.request, "urlopen",
        lambda req, timeout=None: _FakeResp(b"<html>", "text/html"),
    )
    out = json.loads(image_fetch.make_spec(tmp_path).handler("https://example.com/page.html"))
    assert out["error_code"] == "not_image"
