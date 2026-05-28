"""Tests for the minimal `.env` loader in `jeanmichel.config`."""
from __future__ import annotations

import os
from pathlib import Path

from jeanmichel.config import _load_dotenv


def test_loads_simple_keys(tmp_path: Path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("FOO=bar\nBAZ=qux\n", encoding="utf-8")
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("BAZ", raising=False)
    _load_dotenv(env)
    assert os.environ["FOO"] == "bar"
    assert os.environ["BAZ"] == "qux"


def test_skips_comments_and_blanks(tmp_path: Path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# leading comment\n"
        "\n"
        "KEY1=value1\n"
        "  # indented comment\n"
        "KEY2=value2\n",
        encoding="utf-8",
    )
    for k in ("KEY1", "KEY2"):
        monkeypatch.delenv(k, raising=False)
    _load_dotenv(env)
    assert os.environ["KEY1"] == "value1"
    assert os.environ["KEY2"] == "value2"


def test_strips_quotes(tmp_path: Path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        'DOUBLE="hello"\n'
        "SINGLE='world'\n"
        "UNQUOTED=raw\n",
        encoding="utf-8",
    )
    for k in ("DOUBLE", "SINGLE", "UNQUOTED"):
        monkeypatch.delenv(k, raising=False)
    _load_dotenv(env)
    assert os.environ["DOUBLE"] == "hello"
    assert os.environ["SINGLE"] == "world"
    assert os.environ["UNQUOTED"] == "raw"


def test_shell_env_wins_over_dotenv(tmp_path: Path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("MY_KEY=from_dotenv\n", encoding="utf-8")
    monkeypatch.setenv("MY_KEY", "from_shell")
    _load_dotenv(env)
    assert os.environ["MY_KEY"] == "from_shell"


def test_missing_file_is_noop(tmp_path: Path):
    # Just verify no exception is raised when the file doesn't exist.
    _load_dotenv(tmp_path / "does-not-exist.env")


def test_malformed_lines_silently_ignored(tmp_path: Path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "GOOD=ok\n"
        "this_is_not_a_pair\n"
        "ALSO_GOOD=fine\n",
        encoding="utf-8",
    )
    for k in ("GOOD", "ALSO_GOOD"):
        monkeypatch.delenv(k, raising=False)
    _load_dotenv(env)
    assert os.environ["GOOD"] == "ok"
    assert os.environ["ALSO_GOOD"] == "fine"


def test_value_with_equals_sign_preserved(tmp_path: Path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("URL=https://api.example.com/?key=value&other=thing\n", encoding="utf-8")
    monkeypatch.delenv("URL", raising=False)
    _load_dotenv(env)
    assert os.environ["URL"] == "https://api.example.com/?key=value&other=thing"
