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


@pytest.fixture(autouse=True)
def _mcp_off_by_default(monkeypatch):
    """Force the MCP manager inert for the whole suite.

    A developer's local `mcp_servers.toml` must never make tests hit the network
    or become non-deterministic. The dedicated MCP tests install their own
    manager explicitly.
    """
    import jeanmichel.mcp_client as mcp_client
    monkeypatch.setattr(mcp_client, "_manager", mcp_client.MCPManager({}, {}))


@pytest.fixture(autouse=True)
def _snapshot_disabled_by_default(monkeypatch):
    """Force per-conversation git snapshots OFF for the whole suite.

    The feature is opt-in (off by default in code), but a developer's local
    `.env` may enable it (`JEANMICHEL_CONVERSATION_SNAPSHOT_ENABLED=1`), which
    is loaded at config import. Pin it off so the suite stays deterministic and
    creates no git repos in tmp dirs. The dedicated snapshot tests re-enable it
    explicitly.
    """
    import jeanmichel.config as cfg
    monkeypatch.setattr(cfg, "CONVERSATION_SNAPSHOT_ENABLED", False)


@pytest.fixture(autouse=True)
def _code_worktree_disabled_by_default(monkeypatch):
    """Force code-mode git worktrees OFF for the whole suite.

    Opt-in feature (off in code), but a developer's `.env` may enable it
    (`JEANMICHEL_CODE_WORKTREE_ENABLED=1`), loaded at config import. Pin it off
    so the suite never creates a worktree of the real repo. The worktree / repo /
    deliberation tests re-enable it explicitly on a tmp PROJECT_ROOT.
    """
    import jeanmichel.config as cfg
    monkeypatch.setattr(cfg, "CODE_WORKTREE_ENABLED", False)


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
    cfg.CLI_PROFILE_PATH = tmp_path / "cli_profile.toml"
    cfg.CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

    schema = (_ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(cfg.DB_PATH)
    conn.executescript(schema)
    conn.commit()
    conn.close()

    return cfg.DB_PATH
