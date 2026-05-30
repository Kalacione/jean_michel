"""Read-only workspace access for the web API.

Scoped to a conversation's ``workspace/`` subfolder. Conversation-root files
(messages.json, state.json, events.jsonl) are NOT exposed here — they have
dedicated endpoints. The path-traversal guard is the shared
``tools._workspace.safe_resolve`` (the security-critical part) ; this module
adds the API-shaped tree listing + file read on top.

Errors are signalled by raising ``WorkspaceError(code, message)``.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import mimetypes
import os
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import IMAGE_MAX_PX, WORKSPACE_UPLOAD_MAX_BYTES
from ..tools._workspace import quota_remaining, safe_resolve, workspace_root_for

_log = logging.getLogger(__name__)

_MAX_TREE_DEPTH = 2
DEFAULT_MAX_BYTES = 100_000

# Raster image types Pillow can thumbnail. SVG is vector → served as-is.
_RASTER_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
_IMAGE_EXTS = _RASTER_EXTS | {".svg"}
_THUMBS_DIR = ".thumbs"


class WorkspaceError(Exception):
    """A workspace op failed. ``code`` ∈ {invalid_path, not_found, not_utf8,
    too_large, quota_exceeded, exists}."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _entry(p: Path, depth: int) -> dict[str, Any]:
    stat = p.stat()
    node: dict[str, Any] = {
        "name": p.name,
        "type": "directory" if p.is_dir() else "file",
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }
    if p.is_file():
        node["size_bytes"] = stat.st_size
    if p.is_dir() and depth < _MAX_TREE_DEPTH:
        node["children"] = [
            _entry(c, depth + 1) for c in sorted(p.iterdir()) if not c.name.startswith(".")
        ]
    return node


def list_tree(conv_folder: Path, sub_path: str = "") -> dict[str, Any]:
    """Tree of the conversation workspace (max 2 levels), optionally from sub_path."""
    ws_root = workspace_root_for(conv_folder)
    if sub_path:
        try:
            start = safe_resolve(ws_root, sub_path)
        except ValueError as exc:
            raise WorkspaceError("invalid_path", str(exc)) from exc
        if not start.is_dir():
            raise WorkspaceError("not_found", f"Not a directory: {sub_path}")
    else:
        start = ws_root
    entries = [_entry(p, 1) for p in sorted(start.iterdir()) if not p.name.startswith(".")]
    return {"workspace": sub_path, "entries": entries}


def read_file(
    conv_folder: Path, relative_path: str, max_bytes: int = DEFAULT_MAX_BYTES
) -> dict[str, Any]:
    """Read a UTF-8 file under the conversation workspace. Workspace-scoped only."""
    ws_root = workspace_root_for(conv_folder)
    try:
        target = safe_resolve(ws_root, relative_path)
    except ValueError as exc:
        raise WorkspaceError("invalid_path", str(exc)) from exc
    if not target.is_file():
        raise WorkspaceError("not_found", f"Not found: {relative_path}")
    data = target.read_bytes()
    truncated = len(data) > max_bytes
    try:
        content = data[:max_bytes].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkspaceError("not_utf8", "File is not valid UTF-8.") from exc
    return {"path": relative_path, "content": content, "truncated": truncated}


def resolve_download(conv_folder: Path, relative_path: str) -> Path:
    """Resolve a workspace file for raw (binary-safe) download. Workspace-scoped.

    Unlike ``read_file``, this neither decodes nor truncates — the endpoint
    streams the file as-is. Raises ``WorkspaceError(invalid_path | not_found)``.
    """
    ws_root = workspace_root_for(conv_folder)
    try:
        target = safe_resolve(ws_root, relative_path)
    except ValueError as exc:
        raise WorkspaceError("invalid_path", str(exc)) from exc
    if not target.is_file():
        raise WorkspaceError("not_found", f"Not found: {relative_path}")
    return target


def is_image(relative_path: str) -> bool:
    """True if the path looks like an image we can show inline (incl. SVG)."""
    return Path(relative_path).suffix.lower() in _IMAGE_EXTS


def _thumb_cache_path(ws_root: Path, target: Path) -> Path:
    digest = hashlib.sha1(target.relative_to(ws_root).as_posix().encode("utf-8")).hexdigest()
    return ws_root / _THUMBS_DIR / f"{digest[:16]}.webp"


def _generate_thumb(src: Path, dest: Path, max_px: int) -> bool:
    """Normalize ``src`` → a ≤max_px WebP at ``dest`` (EXIF-corrected, first GIF
    frame). Returns False if Pillow can't handle it (caller serves the original)."""
    try:
        from PIL import Image, ImageOps

        with Image.open(src) as img:
            img = ImageOps.exif_transpose(img)  # honour phone orientation
            if img.mode not in ("RGB", "RGBA", "L"):
                img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
            img.thumbnail((max_px, max_px))  # preserves ratio, never upscales
            dest.parent.mkdir(parents=True, exist_ok=True)
            img.save(dest, "WEBP", quality=82, method=4)
        return True
    except Exception as exc:  # noqa: BLE001 — corrupt/unsupported → serve original
        _log.warning("thumbnail generation failed for %s: %s", src, exc)
        return False


def resolve_image(conv_folder: Path, relative_path: str, thumb: bool) -> tuple[Path, str]:
    """Resolve a workspace image for serving. Returns ``(path, media_type)``.

    With ``thumb`` and a rasterizable image, returns a cached ≤IMAGE_MAX_PX WebP
    derivative (generated/refreshed on demand, reused by vision later). Otherwise
    (SVG, non-image, thumb off, or Pillow failure) returns the original with its
    guessed MIME. Raises ``WorkspaceError(invalid_path | not_found)``.
    """
    ws_root = workspace_root_for(conv_folder)
    try:
        target = safe_resolve(ws_root, relative_path)
    except ValueError as exc:
        raise WorkspaceError("invalid_path", str(exc)) from exc
    if not target.is_file():
        raise WorkspaceError("not_found", f"Not found: {relative_path}")
    if thumb and target.suffix.lower() in _RASTER_EXTS:
        cache = _thumb_cache_path(ws_root, target)
        fresh = cache.is_file() and cache.stat().st_mtime >= target.stat().st_mtime
        if fresh or _generate_thumb(target, cache, IMAGE_MAX_PX):
            return cache, "image/webp"
    mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return target, mime


def normalized_image_b64(conv_folder: Path, relative_path: str) -> str | None:
    """Base64 of the normalized ≤IMAGE_MAX_PX WebP derivative of a workspace
    image, or None if the path isn't a rasterizable image. Feeds Gemma vision
    in-context (transient ; never persisted)."""
    try:
        target, mime = resolve_image(conv_folder, relative_path, thumb=True)
    except WorkspaceError:
        return None
    if mime != "image/webp":
        return None
    return base64.b64encode(target.read_bytes()).decode("ascii")


def save_upload(conv_folder: Path, filename: str, data: bytes) -> dict[str, Any]:
    """Write one uploaded file at the workspace root. Single source of upload truth.

    The client-supplied ``filename`` is reduced to its basename (any directory
    component is dropped) then validated by ``safe_resolve``. Guards, in order :
    per-file size (``WORKSPACE_UPLOAD_MAX_BYTES``), no silent overwrite, then the
    cumulative workspace quota. Raises ``WorkspaceError`` with code ∈
    {invalid_path, too_large, exists, quota_exceeded}.
    """
    name = Path(filename).name.strip()
    if not name:
        raise WorkspaceError("invalid_path", "Empty filename.")
    if len(data) > WORKSPACE_UPLOAD_MAX_BYTES:
        limit_mb = WORKSPACE_UPLOAD_MAX_BYTES / (1024 * 1024)
        raise WorkspaceError(
            "too_large", f"{name} exceeds the {limit_mb:.0f} MB per-file limit."
        )
    ws_root = workspace_root_for(conv_folder)
    try:
        target = safe_resolve(ws_root, name)
    except ValueError as exc:
        raise WorkspaceError("invalid_path", str(exc)) from exc
    if target.exists():
        raise WorkspaceError("exists", f"File already exists: {name}")
    if len(data) > quota_remaining(ws_root):
        raise WorkspaceError("quota_exceeded", "Workspace quota exceeded.")
    target.write_bytes(data)
    return {"name": name, "size_bytes": len(data)}


def filter_existing(conv_folder: Path, rel_paths: list[str]) -> list[str]:
    """Keep only workspace-relative paths that resolve to real files inside the
    workspace (dedup, order-preserving). Validates message attachments before
    they reach the LLM — drops anything missing or escaping the workspace.
    """
    ws_root = workspace_root_for(conv_folder)
    out: list[str] = []
    seen: set[str] = set()
    for p in rel_paths or []:
        if not isinstance(p, str) or p in seen:
            continue
        try:
            target = safe_resolve(ws_root, p)
        except ValueError:
            continue
        if target.is_file():
            seen.add(p)
            out.append(p)
    return out


def zip_workspace(conv_folder: Path) -> Path | None:
    """Zip the whole workspace into a temp ``.zip``. Returns its path (the caller
    deletes it after streaming) or None when the workspace has no files."""
    ws_root = workspace_root_for(conv_folder)
    files = [
        p
        for p in sorted(ws_root.rglob("*"))
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(ws_root).parts)
    ]
    if not files:
        return None
    fd, tmp = tempfile.mkstemp(suffix=".zip", prefix="jm_workspace_")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, f.relative_to(ws_root).as_posix())
    return Path(tmp)
