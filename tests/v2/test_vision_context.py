"""Vision B plumbing : image-token accounting + non-persistence of base64."""
from __future__ import annotations

import json

from jeanmichel import persistence
from jeanmichel.tokens import estimate_messages_tokens


def test_image_tokens_counted():
    base = estimate_messages_tokens([{"role": "user", "content": "hi"}])
    with_img = estimate_messages_tokens(
        [{"role": "user", "content": "hi", "images": ["b64a", "b64b"]}]
    )
    assert with_img - base == 512  # ~256 tokens per image


def test_save_messages_strips_images(tmp_path):
    persistence.save_messages(tmp_path, [
        {"role": "user", "content": "look", "images": ["BIGBASE64"]},
        {"role": "assistant", "content": "ok"},
    ])
    raw = (tmp_path / "messages.json").read_text(encoding="utf-8")
    assert "BIGBASE64" not in raw  # base64 never hits the conversation file
    loaded = json.loads(raw)
    assert "images" not in loaded[0]
    assert loaded[0]["content"] == "look"  # text preserved
