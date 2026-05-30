"""Tests for the three code-fetcher backend tools : github / stackoverflow / pypi.

All tools hit external HTTP APIs ; we stub `urllib.request.urlopen` to keep
the tests offline. Each tool gets : happy path (formatted output) + key
input validation + at least one error path.
"""
from __future__ import annotations

import json
import urllib.error
from unittest.mock import patch

from jeanmichel.tools import github, pypi, stackoverflow

# ---- urlopen stub --------------------------------------------------------


def _fake_urlopen(payload: dict, status: int = 200):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

        def geturl(self):
            return "https://example.com/x"

    return lambda req, timeout=10: _Resp()


# =============================================================================
# github.py
# =============================================================================


_GH_CODE_RESPONSE = {
    "total_count": 42,
    "items": [
        {
            "name": "main.py",
            "path": "src/main.py",
            "html_url": "https://github.com/foo/bar/blob/abc123/src/main.py",
            "score": 9.5,
            "repository": {
                "full_name": "foo/bar",
                "html_url": "https://github.com/foo/bar",
            },
        },
        {
            "name": "util.py",
            "path": "lib/util.py",
            "html_url": "https://github.com/baz/qux/blob/def456/lib/util.py",
            "score": 8.0,
            "repository": {
                "full_name": "baz/qux",
                "html_url": "https://github.com/baz/qux",
            },
        },
    ],
}

_GH_REPOS_RESPONSE = {
    "total_count": 7,
    "items": [
        {
            "full_name": "tiangolo/fastapi",
            "description": "FastAPI framework",
            "html_url": "https://github.com/tiangolo/fastapi",
            "stargazers_count": 75000,
            "language": "Python",
            "updated_at": "2026-05-28T00:00:00Z",
            "open_issues_count": 100,
            "archived": False,
        },
    ],
}


def test_github_search_code_requires_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    result = json.loads(github._handler_search_code(query="fastapi"))
    assert result["error_code"] == "api_key_missing"


def test_github_search_code_rejects_empty_query(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake")
    result = json.loads(github._handler_search_code(query=""))
    assert result["error_code"] == "missing_query"


def test_github_search_code_formats_results(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake")
    with patch.object(github.urllib.request, "urlopen",
                      _fake_urlopen(_GH_CODE_RESPONSE)):
        result = json.loads(github._handler_search_code(
            query="streaming", language="python"
        ))
    assert "error" not in result
    assert len(result["items"]) == 2
    assert result["items"][0]["repo"] == "foo/bar"
    assert result["items"][0]["path"] == "src/main.py"
    assert result["items"][0]["raw_url"] == (
        "https://raw.githubusercontent.com/foo/bar/abc123/src/main.py"
    )
    assert "language:python" in result["query"]
    assert "total_count=42" in result["summary"]


def test_github_search_code_passes_token_header(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake")
    captured: list = []

    def _spy(req, timeout=10):
        captured.append(req)
        return _fake_urlopen(_GH_CODE_RESPONSE)(req, timeout)

    with patch.object(github.urllib.request, "urlopen", _spy):
        github._handler_search_code(query="x")

    assert captured, "urlopen should be called"
    req = captured[0]
    assert req.get_header("Authorization") == "Bearer ghp_fake"


def test_github_search_repos_works_without_token(monkeypatch):
    """Repos endpoint is anonymous-friendly (lower rate limit only)."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with patch.object(github.urllib.request, "urlopen",
                      _fake_urlopen(_GH_REPOS_RESPONSE)):
        result = json.loads(github._handler_search_repos(query="fastapi"))
    assert "error" not in result
    assert result["items"][0]["full_name"] == "tiangolo/fastapi"
    assert result["items"][0]["stars"] == 75000


def test_github_search_repos_with_sort_and_language(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    captured: list = []

    def _spy(req, timeout=10):
        captured.append(req)
        return _fake_urlopen(_GH_REPOS_RESPONSE)(req, timeout)

    with patch.object(github.urllib.request, "urlopen", _spy):
        github._handler_search_repos(query="web", sort="stars", language="rust")

    url = captured[0].full_url
    assert "sort=stars" in url
    assert "language%3Arust" in url or "language:rust" in url.replace("+", " ")


def test_raw_url_from_html():
    html = "https://github.com/foo/bar/blob/main/path/to/file.py"
    raw = github._raw_url_from_html(html)
    assert raw == "https://raw.githubusercontent.com/foo/bar/main/path/to/file.py"


def test_raw_url_from_html_returns_none_for_non_blob():
    assert github._raw_url_from_html("https://github.com/foo/bar") is None
    assert github._raw_url_from_html(None) is None


# =============================================================================
# stackoverflow.py
# =============================================================================


_SO_RESPONSE = {
    "items": [
        {
            "title": "TypeError: NoneType object is not subscriptable",
            "link": "https://stackoverflow.com/q/12345",
            "tags": ["python", "typeerror"],
            "score": 142,
            "answer_count": 7,
            "is_answered": True,
            "accepted_answer_id": 67890,
            "creation_date": 1700000000,
        },
        {
            "title": "How to handle None in list comprehension",
            "link": "https://stackoverflow.com/q/22222",
            "tags": ["python"],
            "score": 50,
            "answer_count": 3,
            "is_answered": True,
            "accepted_answer_id": None,
            "creation_date": 1701000000,
        },
    ],
    "has_more": False,
    "quota_remaining": 295,
}


def test_stackoverflow_rejects_empty_query():
    result = json.loads(stackoverflow._handler(query=""))
    assert result["error_code"] == "missing_query"


def test_stackoverflow_formats_results():
    with patch.object(stackoverflow.urllib.request, "urlopen",
                      _fake_urlopen(_SO_RESPONSE)):
        result = json.loads(stackoverflow._handler(query="NoneType subscript"))
    assert "error" not in result
    assert len(result["items"]) == 2
    assert result["items"][0]["title"].startswith("TypeError")
    assert result["items"][0]["has_accepted_answer"] is True
    assert result["items"][1]["has_accepted_answer"] is False
    assert result["quota_remaining"] == 295


def test_stackoverflow_applies_tag_filter():
    captured: list = []

    def _spy(req, timeout=10):
        captured.append(req)
        return _fake_urlopen(_SO_RESPONSE)(req, timeout)

    with patch.object(stackoverflow.urllib.request, "urlopen", _spy):
        stackoverflow._handler(query="async", tag="python")

    url = captured[0].full_url
    assert "tagged=python" in url


def test_stackoverflow_handles_api_error():
    err_payload = {"error_message": "throttled, slow down", "items": []}
    with patch.object(stackoverflow.urllib.request, "urlopen",
                      _fake_urlopen(err_payload)):
        result = json.loads(stackoverflow._handler(query="x"))
    assert result["error_code"] == "api_error"
    assert "throttled" in result["error"]


def test_stackoverflow_handles_network_failure():
    def _boom(req, timeout=10):
        raise OSError("net down")
    with patch.object(stackoverflow.urllib.request, "urlopen", _boom):
        result = json.loads(stackoverflow._handler(query="x"))
    assert result["error_code"] == "api_call_failed"


# =============================================================================
# pypi.py
# =============================================================================


_PYPI_RESPONSE = {
    "info": {
        "name": "fastapi",
        "version": "0.115.0",
        "summary": "FastAPI framework, high performance, easy to learn",
        "author": "Sebastián Ramírez",
        "license": "MIT",
        "home_page": "https://github.com/tiangolo/fastapi",
        "project_urls": {
            "Homepage": "https://github.com/tiangolo/fastapi",
            "Documentation": "https://fastapi.tiangolo.com/",
        },
        "requires_python": ">=3.8",
        "requires_dist": ["starlette>=0.40.0", "pydantic>=2.0", "typing-extensions"],
        "yanked": False,
        "package_url": "https://pypi.org/project/fastapi/",
    },
    "releases": {f"0.{i}.0": [] for i in range(50)},
}


def test_pypi_rejects_empty_package():
    result = json.loads(pypi._handler(package=""))
    assert result["error_code"] == "missing_package"


def test_pypi_rejects_invalid_name():
    result = json.loads(pypi._handler(package="foo/../bar"))
    assert result["error_code"] == "invalid_name"


def test_pypi_returns_metadata():
    with patch.object(pypi.urllib.request, "urlopen",
                      _fake_urlopen(_PYPI_RESPONSE)):
        result = json.loads(pypi._handler(package="fastapi"))
    assert "error" not in result
    assert result["name"] == "fastapi"
    assert result["version"] == "0.115.0"
    assert result["requires_python"] == ">=3.8"
    assert len(result["requires_dist"]) == 3
    assert result["release_count"] == 50
    assert result["yanked"] is False
    assert "FastAPI framework" in result["description"]
    assert "FastAPI framework" in result["summary"]  # also in the one-liner


def test_pypi_handles_404_as_not_found():
    def _raise(req, timeout=10):
        raise urllib.error.HTTPError(
            "https://pypi.org/x", 404, "Not Found", hdrs=None, fp=None,
        )

    with patch.object(pypi.urllib.request, "urlopen", _raise):
        result = json.loads(pypi._handler(package="nonexistent-pkg"))
    assert result["error_code"] == "not_found"


def test_pypi_truncates_long_dep_list():
    payload = {
        "info": {
            "name": "x",
            "version": "1.0",
            "summary": "test",
            "requires_dist": [f"dep-{i}>=1.0" for i in range(50)],
        },
        "releases": {"1.0": []},
    }
    with patch.object(pypi.urllib.request, "urlopen", _fake_urlopen(payload)):
        result = json.loads(pypi._handler(package="x"))
    assert len(result["requires_dist"]) == 30
    assert result["requires_dist_truncated"] is True


# =============================================================================
# ToolSpec sanity
# =============================================================================


def test_specs_have_correct_names():
    assert github.SEARCH_CODE_SPEC.name == "github_search_code"
    assert github.SEARCH_REPOS_SPEC.name == "github_search_repos"
    assert stackoverflow.SPEC.name == "stackoverflow_search"
    assert pypi.SPEC.name == "pypi_lookup"
