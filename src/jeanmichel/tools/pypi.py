"""Tool: pypi_lookup — fetch metadata for a single PyPI package.

PyPI's Warehouse JSON API : `GET https://pypi.org/pypi/<package>/json`.
No auth, no rate limit in practice.

Pattern : pypi_lookup is a *lookup by exact name*, not a search. To
discover candidate names, use GitHub search ('language:python') or
Stack Overflow first ; once you have a name, pypi_lookup gives you
version, summary, project URL, requires_python, dependencies declared
in the latest release.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

from ._base import ToolSpec
from ._errors import tool_error, tool_ok

_API_URL = "https://pypi.org/pypi"
_TIMEOUT_S = 10
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MAX_REQUIRES = 30  # cap dep list in output to avoid huge dumps


def _handler(package: str) -> str:
    """Return metadata for ``package`` (latest release)."""
    if not package or not package.strip():
        return tool_error("missing_package", "Provide a non-empty `package` name.")
    name = package.strip()
    if not _NAME_RE.match(name):
        return tool_error(
            "invalid_name",
            f"{name!r} is not a valid PyPI package name (allowed: letters, digits, . _ -).",
        )

    url = f"{_API_URL}/{urllib.parse.quote(name)}/json"
    req = urllib.request.Request(url, headers={"User-Agent": "jean-michel/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:  # noqa: S310
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return tool_error("not_found", f"Package {name!r} not found on PyPI.")
        return tool_error("http_error", f"PyPI HTTP {exc.code}: {exc.reason}")
    except Exception as exc:  # noqa: BLE001
        return tool_error("api_call_failed", f"PyPI request failed: {exc}")

    info = raw.get("info") or {}
    requires_dist = info.get("requires_dist") or []
    truncated_deps = len(requires_dist) > _MAX_REQUIRES
    requires_dist = requires_dist[:_MAX_REQUIRES]

    payload = {
        "name": info.get("name"),
        "version": info.get("version"),
        "description": info.get("summary"),  # PyPI calls it "summary" ; we
        # rename to avoid clashing with the `summary` arg of tool_ok().
        "author": info.get("author") or info.get("author_email"),
        "license": info.get("license"),
        "home_page": info.get("home_page") or (info.get("project_urls") or {}).get("Homepage"),
        "project_urls": info.get("project_urls") or {},
        "requires_python": info.get("requires_python"),
        "requires_dist": requires_dist,
        "requires_dist_truncated": truncated_deps,
        "yanked": bool(info.get("yanked")),
        "release_count": len(raw.get("releases") or {}),
        "package_url": info.get("package_url"),
    }
    one_line = f"{payload['name']} {payload['version']}"
    if payload["description"]:
        one_line += f" — {payload['description']}"
    if payload["yanked"]:
        one_line = "[YANKED] " + one_line
    return tool_ok(one_line[:200], **payload)


SPEC = ToolSpec(
    name="pypi_lookup",
    description=(
        "Fetch metadata for a single Python package from PyPI (Warehouse JSON "
        "API). Returns name, version, summary, author, license, home page, "
        "requires_python, declared dependencies (capped at 30), and number "
        "of releases. Use this AFTER you've identified candidate package "
        "names (via github_search_repos or stackoverflow_search) to verify "
        "maintenance status (release_count, yanked flag) and surface basic "
        "metadata. No auth required."
    ),
    parameters={
        "type": "object",
        "properties": {
            "package": {
                "type": "string",
                "description": (
                    "Exact package name as published on PyPI. Examples: "
                    "'requests', 'fastapi', 'numpy', 'pydantic-core'."
                ),
            },
        },
        "required": ["package"],
    },
    handler=_handler,
)
