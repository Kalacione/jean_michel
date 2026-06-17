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
    body = client.get(f"/api/conversations/{conv_id}/plan", headers=_auth(token)).json()
    assert "## Context" in body["plan"]
    assert "status" in body  # plan-level acceptance status (None until a turn boundary sets it)


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


def test_list_plans_and_get_by_id(client, alice):
    """Phase 2 (B.4) : l'historique — GET /plans (index) + GET /plans/{id} (contenu archivé)."""
    from pathlib import Path

    from jeanmichel import persistence
    from jeanmichel import todo as todo_mod
    from jeanmichel.models import ConversationState
    token, conv_id = alice
    with db_connect() as conn:
        folder = Path(db.get_conversation(conn, conv_id)["folder_path"])
    # p1 superseded (archivé en plan_p1.md), p2 actif (plan.md).
    todo_mod.save_plan_file(folder, "plan_p1.md", "# Plan v1")
    todo_mod.save_plan(folder, "# Plan v2")
    persistence.save_state(folder, ConversationState(
        active_plan_id="p2",
        plans={
            "p1": {"plan_file": "plan_p1.md", "status": "superseded", "approved": True, "superseded_by": "p2"},
            "p2": {"plan_file": "plan.md", "status": "pending", "approved": False},
        },
    ))
    body = client.get(f"/api/conversations/{conv_id}/plans", headers=_auth(token)).json()
    assert body["active_plan_id"] == "p2"
    by_id = {p["plan_id"]: p for p in body["plans"]}
    assert by_id["p1"]["status"] == "superseded" and by_id["p1"]["is_active"] is False
    assert by_id["p2"]["is_active"] is True
    p1 = client.get(f"/api/conversations/{conv_id}/plans/p1", headers=_auth(token)).json()
    assert p1["plan"] == "# Plan v1" and p1["status"] == "superseded"
    p2 = client.get(f"/api/conversations/{conv_id}/plans/p2", headers=_auth(token))
    assert p2.json()["plan"] == "# Plan v2"
    # unknown id → 404 (path-traversal safe : the id is validated against the referent)
    assert client.get(f"/api/conversations/{conv_id}/plans/p9", headers=_auth(token)).status_code == 404
