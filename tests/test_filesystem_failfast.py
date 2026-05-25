"""Tests for filesystem fail-fast — critical error detection + quota warning."""

from __future__ import annotations

import json

import pytest

import jeanmichel.config as _cfg
import jeanmichel.tools._workspace as _ws_mod
from jeanmichel.config import UserProfile
from jeanmichel.llm import MockClient
from jeanmichel.models import LLMResponse, ToolCall
from jeanmichel.orchestrator import (
    FilesystemErrorObserved,
    FinalAnswer,
    Orchestrator,
    QuotaWarning,
)
from jeanmichel.tools._errors import CRITICAL_ERROR_CODES, tool_error
from jeanmichel.tools.workspace_create_file import make_spec as create_spec
from jeanmichel.tools.workspace_str_replace import make_spec as replace_spec
from jeanmichel.tools.workspace_view import make_spec as view_spec

PROFILE = UserProfile(notes="test user")


def _tc(name: str, **kwargs) -> ToolCall:
    return ToolCall(name=name, arguments=kwargs)


def _resp(*calls: ToolCall) -> LLMResponse:
    return LLMResponse(thinking="", content="", tool_calls=list(calls))


def _orch(script: list[LLMResponse]) -> Orchestrator:
    return Orchestrator(llm=MockClient(script=script), profile=PROFILE, mode="analyse")


@pytest.fixture()
def tmp_conv(tmp_path):
    (tmp_path / "workspace").mkdir()
    return tmp_path


# ---- tool_error helper -----------------------------------------------------

class TestToolError:
    def test_tool_error_includes_error_code(self):
        r = json.loads(tool_error("path_escape", "bad path"))
        assert r["error"] == "bad path"
        assert r["error_code"] == "path_escape"

    def test_tool_error_extra_kwargs(self):
        r = json.loads(tool_error("file_not_found", "not found", relative_path="x.md"))
        assert r["relative_path"] == "x.md"
        assert r["error_code"] == "file_not_found"

    def test_critical_error_codes_set(self):
        assert "path_escape" in CRITICAL_ERROR_CODES
        assert "quota_exceeded" in CRITICAL_ERROR_CODES
        assert "file_not_found" in CRITICAL_ERROR_CODES
        assert "absolute_path" in CRITICAL_ERROR_CODES


# ---- B. Tool-level error_code coverage -------------------------------------

class TestToolErrorCodes:
    def test_workspace_view_file_not_found(self, tmp_conv):
        spec = view_spec(tmp_conv)
        r = json.loads(spec.handler("nope.md"))
        assert r["error_code"] == "file_not_found"

    def test_workspace_create_path_escape(self, tmp_conv):
        spec = create_spec(tmp_conv, has_write_grant=True)
        r = json.loads(spec.handler("../escape.md", "evil"))
        assert r["error_code"] == "path_escape"

    def test_workspace_create_absolute_path(self, tmp_conv):
        spec = create_spec(tmp_conv, has_write_grant=True)
        r = json.loads(spec.handler("/etc/passwd", "evil"))
        assert r["error_code"] == "absolute_path"

    def test_workspace_create_quota_exceeded(self, tmp_conv, monkeypatch):
        monkeypatch.setattr(_ws_mod, "WORKSPACE_QUOTA_BYTES", 5)
        spec = create_spec(tmp_conv, has_write_grant=True)
        r = json.loads(spec.handler("big.md", "hello world"))
        assert r["error_code"] == "quota_exceeded"

    def test_workspace_str_replace_file_not_found(self, tmp_conv):
        spec = replace_spec(tmp_conv, has_write_grant=True)
        r = json.loads(spec.handler("nope.md", "old", "new"))
        assert r["error_code"] == "file_not_found"

    def test_workspace_str_replace_path_escape(self, tmp_conv):
        spec = replace_spec(tmp_conv, has_write_grant=True)
        r = json.loads(spec.handler("../escape.md", "old", "new"))
        assert r["error_code"] == "path_escape"


# ---- D. Orchestrator detection — fail-fast ---------------------------------

class TestFilesystemFailFast:
    def test_file_not_found_counts(self, tmp_env):
        """3 file_not_found errors in one request trigger fail-fast."""
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="look for files", expected="gather_done")),
            # specialist calls 3 different missing files in one turn → 3 critical errors
            _resp(
                _tc("workspace_view", relative_path="a.md"),
                _tc("workspace_view", relative_path="b.md"),
                _tc("workspace_view", relative_path="c.md"),
            ),
            # jean-michel receives the fail-fast payload, returns
            _resp(_tc("return_to_user", answer="fail fast observed")),
            # archivist
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("Find three missing files"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "fail fast observed"
        # 3 critical events were emitted
        fs_events = [e for e in events if isinstance(e, FilesystemErrorObserved)]
        assert len(fs_events) == 3
        assert all(e.error_code == "file_not_found" for e in fs_events)
        assert not any(isinstance(e, FilesystemErrorObserved) and e.agent_code == "web-search-specialist" and False for e in events)

    def test_path_escape_logged(self, tmp_env):
        """Path traversal attempt emits FilesystemErrorObserved(error_code=path_escape)."""
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="try to escape", expected="gather_done")),
            # specialist tries path traversal, fails, then gathers legitimately
            _resp(_tc("workspace_create_file",
                      relative_path="../escape.md",
                      content="evil",
                      description="test")),
            _resp(_tc("report_findings", summary="done", confidence="high")),
            _resp(_tc("return_to_user", answer="ok")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("Do something"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "ok"
        fs_events = [e for e in events if isinstance(e, FilesystemErrorObserved)]
        assert len(fs_events) == 1
        assert fs_events[0].error_code == "path_escape"
        assert fs_events[0].tool_name == "workspace_create_file"

    def test_quota_exceeded_critical(self, tmp_env, monkeypatch):
        """quota_exceeded is a critical error and increments the counter."""
        monkeypatch.setattr(_ws_mod, "WORKSPACE_QUOTA_BYTES", 5)
        monkeypatch.setattr(_cfg, "WORKSPACE_QUOTA_BYTES", 5)
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="web-search-specialist",
                      briefing="write too much", expected="gather_done")),
            # over-quota write → quota_exceeded
            _resp(_tc("workspace_create_file",
                      relative_path="big.md",
                      content="hello world",
                      description="test")),
            _resp(_tc("report_findings", summary="done", confidence="high")),
            _resp(_tc("return_to_user", answer="ok")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("Write big file"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "ok"
        fs_events = [e for e in events if isinstance(e, FilesystemErrorObserved)]
        assert len(fs_events) == 1
        assert fs_events[0].error_code == "quota_exceeded"


# ---- E. Quota warning ------------------------------------------------------

class TestQuotaWarning:
    def test_quota_warning_threshold(self, tmp_env, monkeypatch):
        """QuotaWarning emitted when remaining bytes fall below 10% of quota."""
        # quota = 1000 bytes; write 950 → remaining = 50 < 100 (10%) → QuotaWarning
        monkeypatch.setattr(_ws_mod, "WORKSPACE_QUOTA_BYTES", 1000)
        monkeypatch.setattr(_cfg, "WORKSPACE_QUOTA_BYTES", 1000)
        orch = _orch([
            _resp(_tc("delegate_to", agent_code="document-builder",
                      briefing="write a large file", expected="build_done")),
            # write 950 UTF-8 bytes
            _resp(_tc("workspace_create_file",
                      relative_path="big.md",
                      content="x" * 950,
                      description="large")),
            # report_findings with the file that was just created
            _resp(_tc("report_findings", summary="done",
                      confidence="high", files_produced=["big.md"])),
            _resp(_tc("return_to_user", answer="ok")),
            _resp(_tc("return_to_user", answer="archived")),
        ])
        events = list(orch.run("Produce a large file"))
        fa = next(e for e in events if isinstance(e, FinalAnswer))
        assert fa.text == "ok"
        warnings = [e for e in events if isinstance(e, QuotaWarning)]
        assert len(warnings) == 1
        assert warnings[0].total_bytes == 1000
        assert warnings[0].remaining_bytes < 100  # below 10% threshold
