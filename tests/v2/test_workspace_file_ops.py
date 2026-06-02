"""Tests for the workspace file-management tools (R4):
workspace_create_dir, workspace_delete_file, workspace_delete_dir."""

from __future__ import annotations

import json

from jeanmichel.tools import (
    build_registry,
    workspace_create_dir,
    workspace_delete_dir,
    workspace_delete_file,
)


def _ok(result: str) -> dict:
    d = json.loads(result)
    assert "error" not in d, d
    return d


def _err(result: str) -> dict:
    d = json.loads(result)
    assert "error" in d, d
    return d


# ---- create_dir -----------------------------------------------------------


def test_create_dir(tmp_path):
    spec = workspace_create_dir.make_spec(tmp_path, has_write_grant=True)
    _ok(spec.handler(relative_path="src/utils"))
    assert (tmp_path / "workspace" / "src" / "utils").is_dir()
    _ok(spec.handler(relative_path="src/utils"))  # idempotent


def test_create_dir_requires_write_grant(tmp_path):
    spec = workspace_create_dir.make_spec(tmp_path, has_write_grant=False)
    assert _err(spec.handler(relative_path="x"))["error_code"] == "no_write_grant"


# ---- delete_file ----------------------------------------------------------


def test_delete_file(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    (ws / "a.txt").write_text("hi")
    spec = workspace_delete_file.make_spec(tmp_path, has_write_grant=True)
    _ok(spec.handler(relative_path="a.txt"))
    assert not (ws / "a.txt").exists()


def test_delete_file_refuses_directory(tmp_path):
    (tmp_path / "workspace" / "d").mkdir(parents=True)
    spec = workspace_delete_file.make_spec(tmp_path, has_write_grant=True)
    assert _err(spec.handler(relative_path="d"))["error_code"] == "is_a_directory"


def test_delete_file_not_found(tmp_path):
    spec = workspace_delete_file.make_spec(tmp_path, has_write_grant=True)
    assert _err(spec.handler(relative_path="nope.txt"))["error_code"] == "file_not_found"


# ---- delete_dir -----------------------------------------------------------


def test_delete_dir_recursive(tmp_path):
    sub = tmp_path / "workspace" / "d" / "sub"
    sub.mkdir(parents=True)
    (sub / "f.txt").write_text("x")
    spec = workspace_delete_dir.make_spec(tmp_path, has_write_grant=True)
    _ok(spec.handler(relative_path="d"))
    assert not (tmp_path / "workspace" / "d").exists()


def test_delete_dir_refuses_file(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    (ws / "a.txt").write_text("x")
    spec = workspace_delete_dir.make_spec(tmp_path, has_write_grant=True)
    assert _err(spec.handler(relative_path="a.txt"))["error_code"] == "not_a_directory"


def test_delete_dir_cannot_escape(tmp_path):
    spec = workspace_delete_dir.make_spec(tmp_path, has_write_grant=True)
    assert _err(spec.handler(relative_path="../.."))["error_code"] in ("path_escape", "absolute_path")


# ---- registry -------------------------------------------------------------


def test_new_tools_in_registry(tmp_path):
    reg = build_registry(tmp_path, has_workspace_write=True)
    for name in ("workspace_create_dir", "workspace_delete_file", "workspace_delete_dir"):
        assert name in reg
