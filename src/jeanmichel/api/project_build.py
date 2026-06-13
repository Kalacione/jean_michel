"""Background build of a project's sandbox Docker image + push the result.

When a project's Dockerfile is set/changed (project settings), build the image in
a background thread and push the outcome over the per-user notification WS so the
GUI can toast — WITHOUT blocking the save request. Empty Dockerfile ⇒ nothing to
build (the repo-default image is used at runtime). No local build context (e.g. an
ssh repo not yet cloned) ⇒ a 'deferred' notice; the lazy build in ``repo_exec``
catches it on first use.

Lives in the API layer (not ``service/``) because it pushes over ``notifications``.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from ..tools import repo_exec
from . import notifications

_log = logging.getLogger(__name__)


def _push(owner_uid: int, project: dict[str, Any], state: str, error: str = "") -> None:
    notifications.notify(owner_uid, {
        "type": "notification",
        "kind": "project_image_build",
        "project_id": project.get("id"),
        "project_name": project.get("name", ""),
        "state": state,            # "ok" | "failed" | "deferred"
        "error": error,
    })


def trigger_image_build(project: dict[str, Any], owner_uid: int) -> None:
    """Kick off a background build of the project's sandbox image (best-effort).

    Non-blocking: returns immediately. Pushes a ``project_image_build`` notif with
    state ok/failed (local build) or deferred (no local context)."""
    dockerfile = (project.get("dockerfile") or "").strip()
    if not dockerfile:
        return  # nothing to build — repo-default at runtime
    code_repo = (project.get("code_repo") or "").strip()
    if project.get("repo_kind") != "local" or not code_repo or not Path(code_repo).is_dir():
        _push(owner_uid, project, "deferred")
        return
    context = Path(code_repo)
    raw = project.get("dockerfile") or ""
    tag = repo_exec.project_image_tag(project.get("id"), raw)

    def _run() -> None:
        try:
            ok, err = repo_exec.build_image(raw, context, tag)
        except Exception as exc:  # noqa: BLE001 — never let a build thread die loud
            _log.warning("project image build crashed: %s", exc)
            _push(owner_uid, project, "failed", str(exc)[:600])
            return
        _push(owner_uid, project, "ok" if ok else "failed", err)

    threading.Thread(target=_run, name=f"img-build-{project.get('id')}", daemon=True).start()
