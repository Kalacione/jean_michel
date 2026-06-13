"""Plan (todo.json) read + human edit API — GET/PUT /conversations/{id}/todo.

Backs the inline plan editor (plan mode). Verifies round-trip, validation, and
owner scoping. Skipped without the ``[web]`` extras.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("argon2")
pytest.importorskip("itsdangerous")

from fastapi.testclient import TestClient  # noqa: E402, I001

from jeanmichel import db  # noqa: E402
from jeanmichel.api import app as api_app  # noqa: E402
from jeanmichel.api import auth  # noqa: E402
from jeanmichel.db import connect as db_connect  # noqa: E402


def _make_user(username: str, password: str) -> int:
    with db_connect() as conn:
        return db.create_web_user(conn, username, auth.hash_password(password))


def _login(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_conv(client: TestClient, token: str, mode: str = "code") -> str:
    resp = client.post("/api/conversations", json={"mode": mode}, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.fixture()
def client(tmp_db_v2) -> TestClient:
    return TestClient(api_app.create_app())


@pytest.fixture()
def alice(client) -> tuple[str, str]:
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    return token, _create_conv(client, token)


def test_get_todo_empty(client, alice):
    token, conv_id = alice
    resp = client.get(f"/api/conversations/{conv_id}/todo", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["todo"] is None


def test_put_then_get_todo_roundtrip(client, alice):
    token, conv_id = alice
    body = {"goal": "ship X", "items": [
        {"text": "write the module", "status": "in_progress"},
        {"text": "add tests", "status": "pending"},
    ]}
    resp = client.put(f"/api/conversations/{conv_id}/todo", json=body, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    todo = resp.json()["todo"]
    assert todo["goal"] == "ship X"
    assert [it["text"] for it in todo["items"]] == ["write the module", "add tests"]
    # IDs assigned by position ; persisted (GET returns the same).
    got = client.get(f"/api/conversations/{conv_id}/todo", headers=_auth(token)).json()["todo"]
    assert got == todo


def test_put_todo_rejects_empty_items(client, alice):
    token, conv_id = alice
    resp = client.put(
        f"/api/conversations/{conv_id}/todo", json={"goal": "g", "items": []}, headers=_auth(token)
    )
    assert resp.status_code == 422


def test_put_todo_rejects_two_in_progress(client, alice):
    token, conv_id = alice
    body = {"goal": "g", "items": [
        {"text": "a", "status": "in_progress"},
        {"text": "b", "status": "in_progress"},
    ]}
    resp = client.put(f"/api/conversations/{conv_id}/todo", json=body, headers=_auth(token))
    assert resp.status_code == 422


def test_todo_owner_scoped(client, alice):
    _token, conv_id = alice
    _make_user("bob", "pw")
    bob = _login(client, "bob", "pw")
    assert client.get(f"/api/conversations/{conv_id}/todo", headers=_auth(bob)).status_code in (403, 404)
    resp = client.put(
        f"/api/conversations/{conv_id}/todo",
        json={"goal": "x", "items": [{"text": "a", "status": "pending"}]},
        headers=_auth(bob),
    )
    assert resp.status_code in (403, 404)
