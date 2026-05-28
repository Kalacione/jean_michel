"""Test fixtures for the v2 suite.

The v2 tests are intentionally lightweight : they don't need a SQLite DB,
they don't need a real Ollama. The foundation modules (events, llm,
persistence) are pure Python and testable in isolation.
"""

from __future__ import annotations

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
