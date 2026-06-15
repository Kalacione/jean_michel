"""Rich plan document read + human edit API — GET/PUT /conversations/{id}/plan.

Backs the markdown plan editor + the Approve bar preview. Verifies round-trip,
clear-on-empty, and owner scoping. Skipped without the ``[web]`` extras.
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


@pytest.fixture()
def client(tmp_db_v2) -> TestClient:
    return TestClient(api_app.create_app())


@pytest.fixture()
def alice(client) -> tuple[str, str]:
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    resp = client.post("/api/conversations", json={"mode": "code"}, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return token, resp.json()["id"]


def test_get_plan_empty(client, alice):
    token, conv_id = alice
    resp = client.get(f"/api/conversations/{conv_id}/plan", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["plan"] is None


def test_put_then_get_plan_roundtrip(client, alice):
    token, conv_id = alice
    md = "# Plan\n\n## Context\nWhy this approach.\n\n## Steps\n1. do the thing\n"
    resp = client.put(f"/api/conversations/{conv_id}/plan", json={"markdown": md}, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["plan"].startswith("# Plan")
    got = client.get(f"/api/conversations/{conv_id}/plan", headers=_auth(token)).json()["plan"]
    assert "## Context" in got


def test_put_empty_plan_clears(client, alice):
    token, conv_id = alice
    client.put(f"/api/conversations/{conv_id}/plan", json={"markdown": "# Plan\n"}, headers=_auth(token))
    resp = client.put(f"/api/conversations/{conv_id}/plan", json={"markdown": "  "}, headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["plan"] is None


def test_plan_owner_scoped(client, alice):
    _token, conv_id = alice
    _make_user("bob", "pw")
    bob = _login(client, "bob", "pw")
    assert client.get(f"/api/conversations/{conv_id}/plan", headers=_auth(bob)).status_code in (403, 404)
    resp = client.put(
        f"/api/conversations/{conv_id}/plan", json={"markdown": "# x"}, headers=_auth(bob)
    )
    assert resp.status_code in (403, 404)
