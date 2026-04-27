"""Unit tests for src/jeanmichel/tools/."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jeanmichel.tools.clock import SPEC as clock_spec
from jeanmichel.tools.conv_read_file import make_spec


class TestClock:
    def test_returns_utc_and_local_keys(self):
        result = json.loads(clock_spec.handler())
        assert "utc" in result
        assert "local" in result
        assert result["timezone"] == "UTC"

    def test_valid_timezone(self):
        result = json.loads(clock_spec.handler(timezone="America/Montreal"))
        assert result["timezone"] == "America/Montreal"

    def test_invalid_timezone_returns_error(self):
        result = json.loads(clock_spec.handler(timezone="Not/AZone"))
        assert "error" in result


class TestConvReadFile:
    def test_reads_file(self, tmp_path):
        conv = tmp_path / "conv"
        conv.mkdir()
        (conv / "note.txt").write_text("hello world")
        spec = make_spec(conv)
        assert spec.handler("note.txt") == "hello world"

    def test_file_not_found(self, tmp_path):
        conv = tmp_path / "conv"
        conv.mkdir()
        result = json.loads(make_spec(conv).handler("missing.txt"))
        assert "error" in result

    def test_path_traversal_blocked(self, tmp_path):
        conv = tmp_path / "conv"
        conv.mkdir()
        result = json.loads(make_spec(conv).handler("../../etc/passwd"))
        assert "error" in result

    def test_max_bytes_truncates(self, tmp_path):
        conv = tmp_path / "conv"
        conv.mkdir()
        (conv / "big.txt").write_bytes(b"x" * 200)
        result = make_spec(conv).handler("big.txt", max_bytes=10)
        assert len(result) == 10

    def test_non_utf8_returns_error(self, tmp_path):
        conv = tmp_path / "conv"
        conv.mkdir()
        (conv / "bin.dat").write_bytes(b"\xff\xfe")
        result = json.loads(make_spec(conv).handler("bin.dat"))
        assert "error" in result
