"""Shared fixtures for the jean-michel test suite."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture()
def tmp_env(tmp_path, monkeypatch):
    """Isolated environment: temp dir + fresh DB + patched config paths."""
    monkeypatch.setenv("JEANMICHEL_HOME", str(tmp_path))

    import jeanmichel.config as cfg
    cfg.REPO_ROOT = tmp_path
    cfg.DB_PATH = tmp_path / "jeanmichel.db"
    cfg.CONVERSATIONS_DIR = tmp_path / "conversations"
    cfg.USER_PROFILE_PATH = tmp_path / "user_profile.toml"
    cfg.CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

    schema = (ROOT / "db" / "schema.sql").read_text()
    conn = sqlite3.connect(cfg.DB_PATH)
    conn.executescript(schema)
    conn.commit()
    conn.close()

    return tmp_path
