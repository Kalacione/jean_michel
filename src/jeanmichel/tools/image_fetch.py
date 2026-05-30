"""Tool: image_fetch — download a web image into the conversation workspace.

Enables the "analyse an image found on the web" flow
(image_search → image_fetch → analyze_image). SSRF-guarded : http(s) only,
blocks private/loopback hosts, requires an ``image/*`` Content-Type, caps the
download at ``WORKSPACE_UPLOAD_MAX_BYTES`` (22 MB, same as upload), and writes
through the workspace save path (quota + no overwrite).
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse
import urllib.request
from pathlib import Path

from ..config import WORKSPACE_UPLOAD_MAX_BYTES
from ..service import workspace
from ._base import ToolSpec
from ._errors import tool_error, tool_ok

_FETCH_TIMEOUT_S = 15
_CTYPE_EXT = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
    "image/gif": ".gif", "image/bmp": ".bmp", "image/tiff": ".tiff",
    "image/svg+xml": ".svg",
}


def _is_blocked_host(host: str) -> bool:
    """True if the host resolves to a private/loopback/reserved address (SSRF)."""
    if not host:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:  # noqa: BLE001
        return True
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
            return True
    return False


def _filename_for(url: str, ctype: str) -> str:
    base = Path(urllib.parse.urlparse(url).path).name.split("?")[0] or "image"
    if "." not in base:
        base += _CTYPE_EXT.get(ctype, ".img")
    return base


def _handler(url: str, *, conv_folder: Path) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return tool_error("bad_url", "Only http(s) URLs are allowed.")
    if _is_blocked_host(parsed.hostname or ""):
        return tool_error("blocked_host", "Refusing to fetch a private/local address.")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "jean-michel/1.0"})
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_S) as resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if not ctype.startswith("image/"):
                return tool_error("not_image", f"Not an image (Content-Type: {ctype or 'unknown'}).")
            data = resp.read(WORKSPACE_UPLOAD_MAX_BYTES + 1)
    except Exception as exc:  # noqa: BLE001
        return tool_error("fetch_failed", f"Fetch failed: {exc}")
    if len(data) > WORKSPACE_UPLOAD_MAX_BYTES:
        limit_mb = WORKSPACE_UPLOAD_MAX_BYTES // (1024 * 1024)
        return tool_error("too_large", f"Image exceeds the {limit_mb} MB limit.")
    try:
        saved = workspace.save_upload(conv_folder, _filename_for(url, ctype), data)
    except workspace.WorkspaceError as exc:
        return tool_error(exc.code, exc.message)
    return tool_ok(
        f"saved {saved['name']} ({saved['size_bytes']} bytes)",
        path=saved["name"],
        size_bytes=saved["size_bytes"],
    )


def make_spec(conv_folder: Path) -> ToolSpec:
    def handler(url: str) -> str:
        return _handler(url, conv_folder=conv_folder)

    return ToolSpec(
        name="image_fetch",
        description=(
            "Download a web image (e.g. an image_url returned by image_search) "
            "INTO the conversation workspace so it can be shown or analysed with "
            "analyze_image. http(s) only ; images only ; max 22 MB."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Direct http(s) URL of the image."},
            },
            "required": ["url"],
        },
        handler=handler,
    )
