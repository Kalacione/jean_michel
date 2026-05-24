"""Tests for _build_partial_report localisation (FR/EN)."""
from __future__ import annotations

from jeanmichel.orchestrator import (
    _build_partial_report,
    _extract_files_from_report,
)


def test_build_partial_report_en(tmp_path):
    md, payload = _build_partial_report(
        tmp_path, req_id="r1", agent_code="web-search-specialist",
        status="loop_detected", error="3 consecutive duplicate-blocked calls",
        recent_tool_calls=[{"name": "web_search", "arguments": {"query": "foo"}}],
        lang="en",
    )
    assert md.startswith("## Aborted report from web-search-specialist")
    assert "**Status:**" in md
    assert "### Files written to workspace before abort" in md
    assert "### Last tool calls attempted" in md
    assert "### Recommended next action" in md
    assert "loop_detected" in payload


def test_build_partial_report_fr(tmp_path):
    md, payload = _build_partial_report(
        tmp_path, req_id="r1", agent_code="web-search-specialist",
        status="loop_detected", error="3 appels dupliqués bloqués consécutifs",
        recent_tool_calls=[{"name": "web_search", "arguments": {"query": "foo"}}],
        lang="fr",
    )
    assert md.startswith("## Rapport interrompu de web-search-specialist")
    assert "**Statut :**" in md
    assert "### Fichiers écrits dans le workspace" in md
    assert "### Derniers appels d'outils tentés" in md
    assert "### Prochaine action recommandée" in md
    assert "loop_detected" in payload


def test_extract_files_parses_french_section(tmp_path):
    # Create some workspace files first.
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "notes.md").write_text("hello")
    md_fr, _ = _build_partial_report(
        tmp_path, req_id="r1", agent_code="x",
        status="x", error="x", recent_tool_calls=[], lang="fr",
    )
    files = _extract_files_from_report(md_fr)
    assert files == ["notes.md"]


def test_extract_files_parses_english_section(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "notes.md").write_text("hello")
    md_en, _ = _build_partial_report(
        tmp_path, req_id="r1", agent_code="x",
        status="x", error="x", recent_tool_calls=[], lang="en",
    )
    files = _extract_files_from_report(md_en)
    assert files == ["notes.md"]
