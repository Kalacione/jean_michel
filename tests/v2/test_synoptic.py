"""Tests for the agent synoptic generator (P6, synoptic.py)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from jeanmichel import synoptic  # noqa: E402


def _conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_render_synoptic_from_schema(tmp_db_v2):
    conn = _conn(tmp_db_v2)
    md = synoptic.render_synoptic(conn)
    conn.close()

    assert "```mermaid" in md and "flowchart TD" in md
    # Router + a delegation edge.
    assert "jean_michel" in md
    assert "jean_michel --> code_runner" in md
    # Reasoner models surface (model_override fetched directly, not via Agent).
    assert "gemma4:26b" in md
    # Two routers: jean-michel (other modes) + code-router (code mode).
    assert "code_router" in md
    assert "deep · code mode" in md
    # Deliberation agents in their own subgraph, anchored on the code router.
    assert "subgraph DELIB" in md
    assert "critical_coder --> sergent_kiss" in md
    assert "code_router -. hard code step .-> DELIB" in md
    # The comparator whitelist fix (migrate_132) is visible as edges.
    assert "comparator_specialist --> web_search_specialist" in md
    # Roster table present.
    assert "| Agent | Role | Model |" in md


def test_main_writes_file(tmp_db_v2, tmp_path):
    out = tmp_path / "synoptic.md"
    rc = synoptic.main(["--out", str(out)])
    assert rc == 0
    assert out.exists() and "flowchart TD" in out.read_text(encoding="utf-8")
