"""Tests for the v2 system prompt renderer — focused on the delegation_targets
block that lets routers/specialists see the agent codes they may delegate to.
"""
from __future__ import annotations

import pytest

from jeanmichel.prompts import (
    render_delegation_targets_block,
    render_system_prompt_v2,
)


# ---- render_delegation_targets_block ------------------------------------


def test_delegation_block_empty_returns_empty_string():
    assert render_delegation_targets_block([]) == ""


def test_delegation_block_lists_codes_and_roles():
    block = render_delegation_targets_block([
        ("wikipedia-specialist", "specialist", "Answer factual questions."),
        ("summarizer", "specialist", "Produce concise summaries."),
    ])
    assert "## Delegation targets" in block
    assert "`wikipedia-specialist` (specialist)" in block
    assert "`summarizer` (specialist)" in block
    assert "Answer factual questions." in block


def test_delegation_block_truncates_long_mission():
    long_mission = "x" * 500
    block = render_delegation_targets_block([
        ("foo", "specialist", long_mission),
    ])
    line = next(line for line in block.splitlines() if line.startswith("- `foo`"))
    assert line.endswith("…")
    # 160-char ceiling on the mission tail; the prefix `- \`foo\` (specialist) — `
    # is allowed extra characters but the mission proper is bounded.
    mission_part = line.split("— ", 1)[1]
    assert len(mission_part) <= 161  # 160 chars + ellipsis


def test_delegation_block_flattens_multiline_mission():
    block = render_delegation_targets_block([
        ("foo", "specialist", "first line\n  second   line\n\nthird line"),
    ])
    line = next(line for line in block.splitlines() if line.startswith("- `foo`"))
    assert "\n" not in line
    assert "first line second line third line" in line


# ---- render_system_prompt_v2 -------------------------------------------


@pytest.fixture
def base_kwargs():
    return {
        "agent_code": "jean-michel",
        "agent_name": "Jean-Michel",
        "agent_role": "router",
        "agent_mission": "Receive the request and delegate.",
        "paradigms": [],
        "user_profile_text": "name: test",
        "user_memory_block": "",
        "user_language": "fr",
        "mode": "analyse",
    }


def test_system_prompt_omits_delegation_block_when_no_targets(base_kwargs):
    prompt = render_system_prompt_v2(**base_kwargs)
    assert "## Delegation targets" not in prompt


def test_system_prompt_includes_delegation_block_when_targets(base_kwargs):
    prompt = render_system_prompt_v2(
        **base_kwargs,
        delegation_targets_meta=[
            ("summarizer", "specialist", "Produce concise summaries."),
            ("wikipedia-specialist", "specialist", "Search Wikipedia."),
        ],
    )
    assert "## Delegation targets" in prompt
    assert "`summarizer`" in prompt
    assert "`wikipedia-specialist`" in prompt
    # Block sits between the Conversation section and the DIRECTIVES section.
    delegation_idx = prompt.index("## Delegation targets")
    directives_idx = prompt.index("# DIRECTIVES")
    conversation_idx = prompt.index("## Conversation")
    assert conversation_idx < delegation_idx < directives_idx
