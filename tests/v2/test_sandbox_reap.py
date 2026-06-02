"""Tests for sandbox container reaping (R1) — bash_sandbox.reap_sandboxes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from jeanmichel.tools import bash_sandbox


def _ns(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_reap_all_stops_every_sandbox(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["docker", "ps"]:
            return _ns(stdout="jm-sandbox-aaa\njm-sandbox-bbb\n")
        return _ns()

    monkeypatch.setattr(bash_sandbox.subprocess, "run", fake_run)
    stopped = bash_sandbox.reap_sandboxes()
    assert stopped == ["jm-sandbox-aaa", "jm-sandbox-bbb"]
    assert ["docker", "stop", "jm-sandbox-aaa"] in calls
    assert ["docker", "stop", "jm-sandbox-bbb"] in calls


def test_reap_idle_filter_keeps_fresh(monkeypatch):
    def fake_run(cmd, **kw):
        if cmd[:2] == ["docker", "ps"]:
            return _ns(stdout="jm-sandbox-old\njm-sandbox-new\n")
        if cmd[:2] == ["docker", "inspect"]:
            name = cmd[-1]
            age = timedelta(hours=2) if name == "jm-sandbox-old" else timedelta(minutes=1)
            return _ns(stdout=(datetime.now(UTC) - age).isoformat() + "\n")
        return _ns()

    monkeypatch.setattr(bash_sandbox.subprocess, "run", fake_run)
    stopped = bash_sandbox.reap_sandboxes(max_idle_minutes=30)
    assert stopped == ["jm-sandbox-old"]  # fresh one kept (respawns on demand anyway)


def test_reap_none_running(monkeypatch):
    monkeypatch.setattr(bash_sandbox.subprocess, "run", lambda cmd, **kw: _ns(stdout=""))
    assert bash_sandbox.reap_sandboxes() == []


def test_reap_ignores_non_sandbox_names(monkeypatch):
    def fake_run(cmd, **kw):
        if cmd[:2] == ["docker", "ps"]:
            # The --filter should already scope this, but guard the prefix too.
            return _ns(stdout="jm-sandbox-aaa\nsome-other-container\n")
        return _ns()

    monkeypatch.setattr(bash_sandbox.subprocess, "run", fake_run)
    assert bash_sandbox.reap_sandboxes() == ["jm-sandbox-aaa"]
