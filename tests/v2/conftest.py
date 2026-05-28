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
    """Fresh DB with v1 schema + migrate_101 (user_memory table).

    Patches `jeanmichel.config.DB_PATH` so tools using `db.connect()` resolve
    to this temp DB. Migration 100 is NOT applied here ; tests that need the
    paradigm realignment ask for it explicitly via the SQL file.
    """
    monkeypatch.setenv("JEANMICHEL_HOME", str(tmp_path))

    import jeanmichel.config as cfg
    cfg.DB_PATH = tmp_path / "jm_v2.db"
    cfg.REPO_ROOT = tmp_path
    cfg.CONVERSATIONS_DIR = tmp_path / "conversations"
    cfg.USER_PROFILE_PATH = tmp_path / "user_profile.toml"
    cfg.CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

    schema = (_ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    migration_101 = (
        _ROOT / "db" / "migrations" / "migrate_101_user_memory.sql"
    ).read_text(encoding="utf-8")

    conn = sqlite3.connect(cfg.DB_PATH)
    conn.executescript(schema)
    conn.executescript(migration_101)
    conn.commit()
    conn.close()

    return cfg.DB_PATH
