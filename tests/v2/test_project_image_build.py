"""C3 — project sandbox image build + per-user notification WebSocket.

Covers the background build trigger (``api/project_build``), the notifications
registry/push (``api/notifications``), and the ``/ws/notifications`` endpoint.
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("argon2")
pytest.importorskip("itsdangerous")

from fastapi import WebSocketDisconnect  # noqa: E402, I001
from fastapi.testclient import TestClient  # noqa: E402

from jeanmichel import db  # noqa: E402
from jeanmichel.api import app as api_app  # noqa: E402
from jeanmichel.api import auth, notifications, project_build  # noqa: E402
from jeanmichel.db import connect as db_connect  # noqa: E402
from jeanmichel.tools import repo_exec  # noqa: E402


def _make_user(username: str, password: str) -> int:
    with db_connect() as conn:
        return db.create_web_user(conn, username, auth.hash_password(password))


def _login(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


# ---- notifications registry ------------------------------------------------


def test_notifications_register_unregister():
    sentinel = object()
    notifications.register(99, sentinel)
    assert sentinel in notifications._conns.get(99, set())
    notifications.unregister(99, sentinel)
    assert 99 not in notifications._conns


# ---- trigger_image_build (background) --------------------------------------


def _capture(monkeypatch):
    got: list[tuple[int, dict]] = []
    ev = threading.Event()

    def fake_notify(uid, payload):
        got.append((uid, payload))
        ev.set()

    monkeypatch.setattr(notifications, "notify", fake_notify)
    return got, ev


def test_trigger_build_empty_dockerfile_is_noop(monkeypatch):
    got, _ = _capture(monkeypatch)
    project_build.trigger_image_build(
        {"id": 1, "name": "P", "dockerfile": "   ", "code_repo": "/x", "repo_kind": "local"}, 7
    )
    assert got == []  # nothing to build → repo-default at runtime, no notif


def test_trigger_build_ssh_is_deferred(monkeypatch):
    got, _ = _capture(monkeypatch)
    project_build.trigger_image_build(
        {"id": 1, "name": "P", "dockerfile": "FROM alpine", "code_repo": "git@h:o/r.git", "repo_kind": "ssh"}, 7
    )
    assert len(got) == 1 and got[0][1]["state"] == "deferred"


def test_trigger_build_missing_local_path_is_deferred(monkeypatch):
    got, _ = _capture(monkeypatch)
    project_build.trigger_image_build(
        {"id": 1, "name": "P", "dockerfile": "FROM alpine", "code_repo": "/nope/x", "repo_kind": "local"}, 7
    )
    assert got[0][1]["state"] == "deferred"


def test_trigger_build_local_ok(monkeypatch, tmp_path):
    got, ev = _capture(monkeypatch)
    monkeypatch.setattr(repo_exec, "build_image", lambda c, ctx, t: (True, ""))
    project_build.trigger_image_build(
        {"id": 2, "name": "P", "dockerfile": "FROM alpine", "code_repo": str(tmp_path), "repo_kind": "local"}, 7
    )
    assert ev.wait(5)
    assert got[0] == (7, got[0][1]) and got[0][1]["state"] == "ok"


def test_trigger_build_local_failed(monkeypatch, tmp_path):
    got, ev = _capture(monkeypatch)
    monkeypatch.setattr(repo_exec, "build_image", lambda c, ctx, t: (False, "boom"))
    project_build.trigger_image_build(
        {"id": 2, "name": "P", "dockerfile": "FROM alpine", "code_repo": str(tmp_path), "repo_kind": "local"}, 7
    )
    assert ev.wait(5)
    assert got[0][1]["state"] == "failed" and "boom" in got[0][1]["error"]


# ---- /ws/notifications endpoint --------------------------------------------


def test_ws_notifications_rejects_bad_token(tmp_db_v2):
    client = TestClient(api_app.create_app())
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        "/ws/notifications?token=garbage"
    ) as ws:
        ws.receive_json()


def test_ws_notifications_receives_push(tmp_db_v2):
    # `with TestClient(...)` runs the lifespan → notifications.set_loop(serving loop).
    with TestClient(api_app.create_app()) as client:
        uid = _make_user("alice", "pw")
        token = _login(client, "alice", "pw")
        with client.websocket_connect(f"/ws/notifications?token={token}") as ws:
            ws.send_text("hi")  # ensure the server is past register() before we push
            notifications.notify(uid, {
                "type": "notification", "kind": "project_image_build",
                "project_name": "P", "state": "ok", "error": "",
            })
            m = ws.receive_json()
    assert m["type"] == "notification" and m["kind"] == "project_image_build" and m["state"] == "ok"
