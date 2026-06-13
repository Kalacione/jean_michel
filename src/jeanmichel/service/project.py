"""Project CRUD — pure data layer, single SQL/validation source.

Shared by the web API (and, read-only, by the CLI ``--project`` resolution).
A project is owned by a web_user, identified by a kebab-case ``code`` unique
per owner, and groups conversations (1 project → N conversations). Deleting a
project sets its conversations' ``project_id`` to NULL and cascades its
scope='project' memory (migrate_124/125).

Errors are signalled by raising ``ProjectOpError(code, message)``.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .. import db

VALID_STATUSES: frozenset[str] = frozenset({"active", "archived"})
VALID_REPO_KINDS: frozenset[str] = frozenset({"local", "ssh"})
MAX_CODE_CHARS = 60
MAX_NAME_CHARS = 100
MAX_DESCRIPTION_CHARS = 500
MAX_REPO_CHARS = 500
MAX_DOCKERFILE_CHARS = 20000


def _validate_repo_kind(repo_kind: str) -> None:
    if repo_kind not in VALID_REPO_KINDS:
        raise ProjectOpError("invalid_repo_kind", f"repo_kind must be one of {sorted(VALID_REPO_KINDS)}.")


def _validate_code_repo(code_repo: str) -> None:
    if len(code_repo) > MAX_REPO_CHARS:
        raise ProjectOpError("repo_too_long", f"code_repo must be <= {MAX_REPO_CHARS} chars.")


def _validate_dockerfile(dockerfile: str) -> None:
    if len(dockerfile) > MAX_DOCKERFILE_CHARS:
        raise ProjectOpError("dockerfile_too_long", f"dockerfile must be <= {MAX_DOCKERFILE_CHARS} chars.")


class ProjectOpError(Exception):
    """A project operation failed. Carries a stable error code + message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _validate_code(code: str) -> None:
    if not code or not code.strip():
        raise ProjectOpError("invalid_args", "code is required.")
    if " " in code:
        raise ProjectOpError(
            "invalid_code", "code must not contain spaces. Use kebab-case (e.g. 'jean-michel')."
        )
    if len(code) > MAX_CODE_CHARS:
        raise ProjectOpError("code_too_long", f"code must be <= {MAX_CODE_CHARS} chars.")


def create(
    conn: sqlite3.Connection, *, user_id: int, code: str, name: str, description: str = "",
    code_repo: str = "", repo_kind: str = "local", dockerfile: str = "",
) -> dict[str, Any]:
    """Create a project for ``user_id``. Raises on validation / duplicate code."""
    _validate_code(code)
    if not name or not name.strip():
        raise ProjectOpError("invalid_args", "name is required.")
    if len(name) > MAX_NAME_CHARS:
        raise ProjectOpError("name_too_long", f"name must be <= {MAX_NAME_CHARS} chars.")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise ProjectOpError(
            "description_too_long", f"description must be <= {MAX_DESCRIPTION_CHARS} chars."
        )
    _validate_code_repo(code_repo)
    _validate_repo_kind(repo_kind)
    _validate_dockerfile(dockerfile)
    if db.get_project_by_code(conn, user_id, code) is not None:
        raise ProjectOpError("already_exists", f"A project with code='{code}' already exists.")
    pid = db.create_project(
        conn, user_id=user_id, code=code, name=name, description=description,
        code_repo=code_repo, repo_kind=repo_kind, dockerfile=dockerfile,
    )
    row = db.get_project(conn, pid)
    return dict(row)


def list_(conn: sqlite3.Connection, *, user_id: int, include_archived: bool = True) -> list[dict]:
    return [dict(r) for r in db.list_projects_for_user(conn, user_id, include_archived=include_archived)]


def get_owned(conn: sqlite3.Connection, *, user_id: int, project_id: int) -> dict[str, Any]:
    """Return a project the user owns, or raise not_found (also when another user owns it)."""
    row = db.get_project(conn, project_id)
    if row is None or row["user_id"] != user_id:
        raise ProjectOpError("not_found", f"No project with id={project_id}.")
    return dict(row)


def update(
    conn: sqlite3.Connection, *, user_id: int, project_id: int,
    name: str | None = None, description: str | None = None, status: str | None = None,
    code_repo: str | None = None, repo_kind: str | None = None, dockerfile: str | None = None,
) -> dict[str, Any]:
    """Update an owned project. Raises not_found / invalid_status / validation errors."""
    get_owned(conn, user_id=user_id, project_id=project_id)  # ownership check
    if all(v is None for v in (name, description, status, code_repo, repo_kind, dockerfile)):
        raise ProjectOpError(
            "invalid_args",
            "update requires at least one of: name, description, status, code_repo, repo_kind, dockerfile.",
        )
    if name is not None and (not name.strip() or len(name) > MAX_NAME_CHARS):
        raise ProjectOpError("invalid_args", f"name must be non-empty and <= {MAX_NAME_CHARS} chars.")
    if description is not None and len(description) > MAX_DESCRIPTION_CHARS:
        raise ProjectOpError("description_too_long", f"description must be <= {MAX_DESCRIPTION_CHARS} chars.")
    if status is not None and status not in VALID_STATUSES:
        raise ProjectOpError("invalid_status", f"status must be one of {sorted(VALID_STATUSES)}.")
    if code_repo is not None:
        _validate_code_repo(code_repo)
    if repo_kind is not None:
        _validate_repo_kind(repo_kind)
    if dockerfile is not None:
        _validate_dockerfile(dockerfile)
    db.update_project(
        conn, project_id, name=name, description=description, status=status,
        code_repo=code_repo, repo_kind=repo_kind, dockerfile=dockerfile,
    )
    return dict(db.get_project(conn, project_id))


def delete(conn: sqlite3.Connection, *, user_id: int, project_id: int) -> int:
    """Delete an owned project. Returns its id. Raises not_found."""
    get_owned(conn, user_id=user_id, project_id=project_id)
    db.delete_project(conn, project_id)
    return project_id
