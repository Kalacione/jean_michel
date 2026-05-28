"""Test fixtures for the v2 suite.

The v2 tests are intentionally lightweight : they don't need a SQLite DB,
they don't need a real Ollama. The foundation modules (events, llm,
persistence) are pure Python and testable in isolation. The DB fixture
below is only required by Phase 5+ tests (user_memory).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT / "src"))


@pytest.fixture()
def conv_folder(tmp_path: Path) -> Path:
    """Empty conversation folder backed by pytest's tmp_path."""
    folder = tmp_path / "conv_test"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


@pytest.fixture()
def tmp_db_v2(tmp_path: Path, monkeypatch) -> Path:
    """Fresh DB loaded from the consolidated v2 schema.

    `db/schema.sql` is now the v2 final state (Phase 8 consolidation), so
    loading it alone is enough — no migrations to apply. Patches
    `jeanmichel.config.DB_PATH` so tools using `db.connect()` resolve here.
    """
    monkeypatch.setenv("JEANMICHEL_HOME", str(tmp_path))

    import jeanmichel.config as cfg
    cfg.DB_PATH = tmp_path / "jm_v2.db"
    cfg.REPO_ROOT = tmp_path
    cfg.CONVERSATIONS_DIR = tmp_path / "conversations"
    cfg.USER_PROFILE_PATH = tmp_path / "user_profile.toml"
    cfg.CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

    schema = (_ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(cfg.DB_PATH)
    conn.executescript(schema)
    conn.commit()
    conn.close()

    return cfg.DB_PATH
