"""Tests for the orchestrator determinism map generator (P6, orchestrator_map.py)."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from jeanmichel import config, orchestrator_map  # noqa: E402


def test_render_has_live_values_and_control_points():
    md = orchestrator_map.render_orchestrator_map()
    # Live config values (not hardcoded in prose) — read from the module.
    assert f"`{config.MAX_DEPTH}`" in md
    assert "0.7, 0.8, 0.9, 0.95" in md  # COMPACTION_THRESHOLDS live
    # Control points present.
    for cp in ("PreToolUse hook", "Worktree isolation", "Repo edit gates",
               "Context packet (CRP)", "Deliberation trigger"):
        assert cp in md, cp
    # Points to the README for narrative (no duplication of prose).
    assert "README.md" in md


def test_main_writes_file(tmp_path):
    out = tmp_path / "o.md"
    assert orchestrator_map.main(["--out", str(out)]) == 0
    assert out.exists() and "determinism map" in out.read_text(encoding="utf-8")
