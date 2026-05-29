"""S1 — auth + multi-user web frontend.

Covers: web_users / conversation_users db helpers, password hashing + signed
tokens, and the API (login / me / conversation isolation / 403 on foreign conv).

Skipped entirely when the ``[web]`` extras aren't installed.
"""

from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("argon2")
pytest.importorskip("itsdangerous")

from fastapi.testclient import TestClient  # noqa: E402, I001

from jeanmichel import db  # noqa: E402
from jeanmichel.api import app as api_app  # noqa: E402
from jeanmichel.api import auth  # noqa: E402
from jeanmichel.db import connect as db_connect  # noqa: E402
from jeanmichel.service import conversation as conversation_svc  # noqa: E402


# ---- Helpers --------------------------------------------------------------


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
    # tmp_db_v2 redirects config paths to a temp dir (DB + conversations/ +
    # .api_secret all isolated). The app reads config dynamically at request time.
    return TestClient(api_app.create_app())


# ---- DB helpers -----------------------------------------------------------


def test_create_and_get_web_user(tmp_db_v2):
    uid = _make_user("alice", "pw")
    with db_connect() as conn:
        row = db.get_web_user_by_username(conn, "alice")
        assert row is not None
        assert row["id"] == uid
        assert row["username"] == "alice"
        assert row["password_hash"] != "pw"  # hashed, not plaintext
        assert db.get_web_user_by_username(conn, "ghost") is None


def test_duplicate_username_rejected(tmp_db_v2):
    _make_user("alice", "pw")
    with pytest.raises(sqlite3.IntegrityError), db_connect() as conn:
        db.create_web_user(conn, "alice", "other-hash")


def test_associate_and_ownership(tmp_db_v2):
    alice = _make_user("alice", "pw")
    bob = _make_user("bob", "pw")
    conv_id, _ = conversation_svc.create_conversation("analyse")
    with db_connect() as conn:
        db.associate_conversation_user(conn, alice, conv_id)
    with db_connect() as conn:
        assert db.user_owns_conversation(conn, alice, conv_id) is True
        assert db.user_owns_conversation(conn, bob, conv_id) is False


def test_list_conversations_for_user_is_scoped(tmp_db_v2):
    alice = _make_user("alice", "pw")
    bob = _make_user("bob", "pw")
    conv_a, _ = conversation_svc.create_conversation("analyse")
    conv_b, _ = conversation_svc.create_conversation("chat")
    cli_conv, _ = conversation_svc.create_conversation("analyse")  # no association
    with db_connect() as conn:
        db.associate_conversation_user(conn, alice, conv_a)
        db.associate_conversation_user(conn, bob, conv_b)
    with db_connect() as conn:
        alice_ids = {r["id"] for r in db.list_conversations_for_user(conn, alice)}
        bob_ids = {r["id"] for r in db.list_conversations_for_user(conn, bob)}
    assert alice_ids == {conv_a}
    assert bob_ids == {conv_b}
    # The unassociated (CLI-style) conversation is invisible to everyone.
    assert cli_conv not in alice_ids and cli_conv not in bob_ids


# ---- Password hashing + tokens --------------------------------------------


def test_password_hash_roundtrip():
    h = auth.hash_password("s3cret")
    assert h != "s3cret"
    assert auth.verify_password(h, "s3cret") is True
    assert auth.verify_password(h, "wrong") is False


def test_token_roundtrip(tmp_db_v2):
    token = auth.make_token({"id": 7, "username": "alice"})
    assert auth.verify_token(token) == {"id": 7, "username": "alice"}


def test_token_tampered_returns_none(tmp_db_v2):
    assert auth.verify_token("not-a-real-token") is None
    token = auth.make_token({"id": 1, "username": "x"})
    assert auth.verify_token(token + "x") is None


def test_authenticate(tmp_db_v2):
    _make_user("alice", "pw")
    assert auth.authenticate("alice", "pw") == {
        "id": auth.authenticate("alice", "pw")["id"],
        "username": "alice",
    }
    assert auth.authenticate("alice", "wrong") is None
    assert auth.authenticate("ghost", "pw") is None


# ---- API : login / me -----------------------------------------------------


def test_login_ok(client):
    _make_user("alice", "pw")
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "pw"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"]
    assert body["user"]["username"] == "alice"


def test_login_bad_password(client):
    _make_user("alice", "pw")
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "nope"})
    assert resp.status_code == 401


def test_login_unknown_user(client):
    resp = client.post("/api/auth/login", json={"username": "ghost", "password": "pw"})
    assert resp.status_code == 401


def test_me_requires_token(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers=_auth("garbage")).status_code == 401


def test_me_with_token(client):
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    resp = client.get("/api/auth/me", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["user"]["username"] == "alice"


# ---- API : conversation isolation + ownership guard -----------------------


def test_conversation_isolation_and_403(client):
    _make_user("alice", "pw")
    _make_user("bob", "pw")
    tok_a = _login(client, "alice", "pw")
    tok_b = _login(client, "bob", "pw")

    # Alice creates a conversation.
    created = client.post("/api/conversations", json={"mode": "chat"}, headers=_auth(tok_a))
    assert created.status_code == 201
    conv_a = created.json()["id"]

    # Alice sees it ; Bob does not.
    alice_list = client.get("/api/conversations", headers=_auth(tok_a)).json()["conversations"]
    bob_list = client.get("/api/conversations", headers=_auth(tok_b)).json()["conversations"]
    assert conv_a in {c["id"] for c in alice_list}
    assert conv_a not in {c["id"] for c in bob_list}

    # Owner can read it ; foreign user gets 403 ; unknown id gets 404.
    assert client.get(f"/api/conversations/{conv_a}", headers=_auth(tok_a)).status_code == 200
    assert client.get(f"/api/conversations/{conv_a}", headers=_auth(tok_b)).status_code == 403
    assert client.get("/api/conversations/does-not-exist", headers=_auth(tok_a)).status_code == 404


def test_conversation_endpoints_require_auth(client):
    assert client.get("/api/conversations").status_code == 401
    assert client.post("/api/conversations", json={"mode": "analyse"}).status_code == 401
