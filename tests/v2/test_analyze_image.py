"""Tests for analyze_image — transient workspace-image vision (option A).

The vision model is a MockClient, so the test is offline. We verify the tool
reads a real workspace image, sends a base64 image to the model, returns the
textual analysis, and rejects non-raster / missing inputs.
"""
from __future__ import annotations

import json

from jeanmichel.llm import MockClient
from jeanmichel.models import LLMResponse
from jeanmichel.tools import analyze_image
from jeanmichel.tools._workspace import workspace_root_for


def _png(folder, name="pic.png", size=(30, 20)):
    from PIL import Image

    Image.new("RGB", size, (220, 30, 30)).save(workspace_root_for(folder) / name, "PNG")


def test_analyze_image_returns_text(tmp_path):
    _png(tmp_path)
    client = MockClient(script=[LLMResponse(thinking="", content="A red rectangle.")])
    spec = analyze_image.make_spec(tmp_path, vision_client=client)
    out = json.loads(spec.handler("pic.png", "What is it?"))
    assert out["analysis"] == "A red rectangle."
    assert out["path"] == "pic.png"
    # The vision call carried a base64 image (transient — not persisted anywhere).
    sent = client.calls_v2[-1]["messages"][0]
    assert sent["images"] and isinstance(sent["images"][0], str)


def test_analyze_image_rejects_non_raster(tmp_path):
    (workspace_root_for(tmp_path) / "logo.svg").write_text("<svg/>", encoding="utf-8")
    client = MockClient(script=[LLMResponse(thinking="", content="x")])
    spec = analyze_image.make_spec(tmp_path, vision_client=client)
    out = json.loads(spec.handler("logo.svg", "?"))
    assert out["error_code"] == "unsupported_image"
    assert not client.calls_v2  # never reached the model


def test_analyze_image_missing_file(tmp_path):
    client = MockClient(script=[LLMResponse(thinking="", content="x")])
    spec = analyze_image.make_spec(tmp_path, vision_client=client)
    out = json.loads(spec.handler("ghost.png", "?"))
    assert out["error_code"] == "not_found"
