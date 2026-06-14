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
        "memory_block": "",
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


# ---- layer-based language/context gating (P1) -----------------------------


def test_specialist_prompt_is_english_only_no_user_context(base_kwargs):
    """Center layer (specialist) : no user profile, no user language → English only."""
    kw = {**base_kwargs, "agent_code": "code-analyst", "agent_role": "specialist"}
    prompt = render_system_prompt_v2(**kw)
    assert "Detected language" not in prompt
    assert "## Human" not in prompt
    assert "name: test" not in prompt           # user profile withheld
    assert "Work ENTIRELY in English" in prompt
    assert "fr" not in prompt.split("# DIRECTIVES")[0]  # user language not leaked


def test_router_prompt_keeps_user_context_and_language(base_kwargs):
    """Edge (router) : keeps the user profile + user language + ask_human note."""
    prompt = render_system_prompt_v2(**base_kwargs)  # role=router
    assert "## Human" in prompt and "name: test" in prompt
    assert "Detected language" in prompt and ": fr" in prompt
    assert "ask_human" in prompt  # router note


def test_resume_doctrine_in_contracts(base_kwargs):
    """P5 : the HUMAN INPUT NEEDED marker doctrine is in both contracts so the
    resumable round-trip actually triggers — specialist emits it, router relays + resumes."""
    spec = render_system_prompt_v2(
        **{**base_kwargs, "agent_code": "code-analyst", "agent_role": "specialist"}
    )
    router = render_system_prompt_v2(**base_kwargs)  # role=router
    assert "HUMAN INPUT NEEDED:" in spec        # specialist knows the marker
    assert "RESUME this exact task" in spec
    assert "HUMAN INPUT NEEDED" in router       # router knows to relay + re-delegate
    assert "re-delegate to the SAME" in router


def test_finalizer_prompt_keeps_user_language_no_ask_human(base_kwargs):
    """Edge (finalizer) : human-facing language, but no ask_human (it can't)."""
    kw = {**base_kwargs, "agent_code": "synthesizer", "agent_role": "finalizer"}
    prompt = render_system_prompt_v2(**kw)
    assert "Detected language" in prompt and ": fr" in prompt
    assert "ask_human" not in prompt
