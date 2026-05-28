"""Tests for `jeanmichel.tokens` — heuristic token estimation."""

from __future__ import annotations

from jeanmichel.tokens import (
    estimate_messages_tokens,
    estimate_text_tokens,
    estimate_tools_payload_tokens,
)


def test_estimate_text_empty_is_zero():
    assert estimate_text_tokens("") == 0
    assert estimate_text_tokens(None) == 0


def test_estimate_text_scales_roughly_with_length():
    short = "hello"
    long_text = "hello world " * 100  # 1200 chars
    assert estimate_text_tokens(short) < estimate_text_tokens(long_text)
    # ~4 chars per token : long_text should be roughly 300 tokens.
    assert 250 <= estimate_text_tokens(long_text) <= 350


def test_estimate_messages_empty_is_zero():
    assert estimate_messages_tokens([]) == 0


def test_estimate_messages_grows_with_content():
    one = [{"role": "user", "content": "hi"}]
    many = [{"role": "user", "content": "hi"}] * 10
    assert estimate_messages_tokens(many) > estimate_messages_tokens(one)


def test_estimate_messages_counts_tool_calls():
    plain = [{"role": "assistant", "content": "hello"}]
    with_tool = [
        {
            "role": "assistant",
            "content": "hello",
            "tool_calls": [
                {"function": {"name": "clock", "arguments": {"timezone": "UTC"}}}
            ],
        }
    ]
    assert estimate_messages_tokens(with_tool) > estimate_messages_tokens(plain)


def test_estimate_messages_counts_tool_role_name():
    plain = [{"role": "user", "content": "x"}]
    with_tool_msg = [
        {"role": "tool", "tool_name": "wikipedia_search", "content": "x"}
    ]
    assert estimate_messages_tokens(with_tool_msg) > estimate_messages_tokens(plain)


def test_estimate_tools_payload_empty_is_zero():
    assert estimate_tools_payload_tokens([]) == 0


def test_estimate_tools_payload_grows_with_schema():
    tiny = [{"function": {"name": "clock", "description": "", "parameters": {}}}]
    big = [
        {
            "function": {
                "name": "complex_tool",
                "description": "A " * 200,
                "parameters": {
                    "type": "object",
                    "properties": {
                        f"param_{i}": {"type": "string", "description": "x" * 50}
                        for i in range(10)
                    },
                },
            }
        }
    ]
    assert estimate_tools_payload_tokens(big) > estimate_tools_payload_tokens(tiny) * 5
