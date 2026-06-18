"""Tool: analyze_image — let a vision model "look at" a workspace image.

Workspace-centric vision (option A, cf. docs/image_vision.md) : reads the image's
normalized derivative from the workspace, sends ONE transient multimodal call to
the vision model (base64 only inside this call — never persisted in the
conversation), and returns the model's TEXTUAL analysis as the tool result. The
main conversation stays text-only.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from ..service import workspace
from ._base import ToolSpec
from ._errors import tool_error, tool_ok


def _default_vision_client() -> Any:
    """Lazily build a vision-capable client when the registry didn't inject one.

    Uses SUBAGENT_DEFAULT_MODEL (the generic specialist default, multimodal) — NOT MAIN_MODEL,
    which may be a text-only orchestrator depending on the toml.
    """
    try:
        from ..config import SUBAGENT_DEFAULT_MODEL
        from ..llm import OllamaClient

        return OllamaClient(model=SUBAGENT_DEFAULT_MODEL)
    except Exception:  # noqa: BLE001
        return None


def _handler(path: str, question: str, *, conv_folder: Path, vision_client: Any) -> str:
    try:
        target, mime = workspace.resolve_image(conv_folder, path, thumb=True)
    except workspace.WorkspaceError as exc:
        return tool_error("not_found" if exc.code == "not_found" else "invalid_path", exc.message)
    if mime != "image/webp":  # SVG / non-raster / normalization failed
        return tool_error(
            "unsupported_image",
            f"{path} is not a raster image I can analyze (resolved as {mime}).",
        )
    client = vision_client or _default_vision_client()
    if client is None:
        return tool_error("vision_unavailable", "No vision model client available.")
    b64 = base64.b64encode(target.read_bytes()).decode("ascii")
    try:
        resp = client.chat_messages(
            messages=[{"role": "user", "content": question, "images": [b64]}],
            tools=[],
            temperature=0.2,
            thinking=False,
        )
    except Exception as exc:  # noqa: BLE001
        return tool_error("vision_failed", f"Vision analysis failed: {exc}")
    analysis = (resp.content or "").strip()
    if not analysis:
        return tool_error("vision_empty", "The vision model returned no description.")
    return tool_ok(f"analysed {Path(path).name}", path=path, analysis=analysis)


def make_spec(conv_folder: Path, vision_client: Any = None) -> ToolSpec:
    """Bind the tool to a conversation workspace + a vision LLM client."""

    def handler(path: str, question: str = "Décris cette image en détail.") -> str:
        return _handler(path, question, conv_folder=conv_folder, vision_client=vision_client)

    return ToolSpec(
        name="analyze_image",
        description=(
            "Look at an image stored in the conversation workspace and return a "
            "textual analysis. Give the workspace-relative path and a precise "
            "question (what to read / describe / extract). Use this whenever the "
            "user attached or asked about an image, or after image_fetch. Raster "
            "images only (PNG/JPEG/GIF/WebP…), not SVG."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Workspace-relative path of the image (e.g. 'photo.jpg').",
                },
                "question": {
                    "type": "string",
                    "description": "What to analyze (e.g. 'What text appears?', 'Describe the chart').",
                },
            },
            "required": ["path"],
        },
        handler=handler,
    )
