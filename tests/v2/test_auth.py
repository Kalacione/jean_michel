"""S1 — auth + multi-user web frontend.

Covers: web_users / conversation_users db helpers, password hashing + signed
tokens, and the API (login / me / conversation isolation / 403 on foreign conv).

Skipped entirely when the ``[web]`` extras aren't installed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

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


# ---- Web user profile (M3) ------------------------------------------------


def test_create_web_user_with_profile_fields(tmp_db_v2):
    """Base fields are filled at creation ; the rest default to '' (never NULL)."""
    with db_connect() as conn:
        uid = db.create_web_user(
            conn, "alice", auth.hash_password("pw"),
            name="Alice", city="Montréal", country="CA", language="fr",
        )
        row = db.get_web_user_by_id(conn, uid)
    assert row["name"] == "Alice"
    assert row["city"] == "Montréal"
    assert row["country"] == "CA"
    assert row["language"] == "fr"
    assert row["interests"] == "" and row["notes"] == ""


def test_update_web_user_profile(tmp_db_v2):
    uid = _make_user("alice", "pw")
    with db_connect() as conn:
        db.update_web_user_profile(conn, uid, city="Laval", notes="likes KISS")
        row = db.get_web_user_by_id(conn, uid)
    assert row["city"] == "Laval"
    assert row["notes"] == "likes KISS"
    # No known field in the patch → no-op (the row is left untouched).
    with db_connect() as conn:
        db.update_web_user_profile(conn, uid, bogus="x")
        assert db.get_web_user_by_id(conn, uid)["city"] == "Laval"


def test_cli_user_is_reserved(tmp_db_v2):
    """The schema seeds a `cli` user ; it's the CLI identity + default memory scope."""
    with db_connect() as conn:
        assert db.cli_user_id(conn) == db.get_web_user_by_username(conn, "cli")["id"]


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


# ---- Conversation title / rename / delete + cascade ----------------------


def test_default_title_helper():
    from jeanmichel.service.turn_runner import _default_title

    assert _default_title("  Quelle météo à Montréal ?  ") == "Quelle météo à Montréal ?"
    assert _default_title("x" * 80) == "x" * 60 + "…"
    assert _default_title("   ") == "Conversation"
    assert _default_title("line one\nline two") == "line one line two"


def test_set_title_if_empty_then_preserved(tmp_db_v2):
    conv_id, _ = conversation_svc.create_conversation("chat")
    with db_connect() as conn:
        db.set_title_if_empty(conn, conv_id, "first")
        assert db.get_conversation(conn, conv_id)["title"] == "first"
        # A later seed must NOT overwrite (preserves an earlier default / user edit).
        db.set_title_if_empty(conn, conv_id, "second")
        assert db.get_conversation(conn, conv_id)["title"] == "first"


def test_rename_does_not_bump_modified(tmp_db_v2):
    conv_id, _ = conversation_svc.create_conversation("chat")
    with db_connect() as conn:
        before = db.get_conversation(conn, conv_id)["modified_at"]
        db.rename_conversation(conn, conv_id, "My title")
        row = db.get_conversation(conn, conv_id)
    assert row["title"] == "My title"
    assert row["modified_at"] == before  # rename is metadata, not an interaction


def test_touch_bumps_modified(tmp_db_v2):
    conv_id, _ = conversation_svc.create_conversation("chat")
    with db_connect() as conn:
        conn.execute(
            "UPDATE conversations SET modified_at='2000-01-01T00:00:00Z' WHERE id=?", (conv_id,)
        )
    with db_connect() as conn:
        db.touch_conversation(conn, conv_id)
        after = db.get_conversation(conn, conv_id)["modified_at"]
    assert after > "2000-01-01T00:00:00Z"


def test_delete_conversation_cascades_association(tmp_db_v2):
    """migrate_114 : deleting only the conversation row cascades to its links."""
    alice = _make_user("alice", "pw")
    conv_id, _ = conversation_svc.create_conversation("chat")
    with db_connect() as conn:
        db.associate_conversation_user(conn, alice, conv_id)
        assert db.user_owns_conversation(conn, alice, conv_id) is True
    with db_connect() as conn:
        db.delete_conversation(conn, conv_id)  # no manual link delete
    with db_connect() as conn:
        assert db.get_conversation(conn, conv_id) is None
        assert db.user_owns_conversation(conn, alice, conv_id) is False


def test_list_returns_title_and_orders_by_activity(tmp_db_v2):
    alice = _make_user("alice", "pw")
    a, _ = conversation_svc.create_conversation("chat")
    b, _ = conversation_svc.create_conversation("chat")
    with db_connect() as conn:
        db.associate_conversation_user(conn, alice, a)
        db.associate_conversation_user(conn, alice, b)
        db.rename_conversation(conn, a, "Alpha")
        # Deterministic activity : a newer than b → a must come first.
        conn.execute("UPDATE conversations SET modified_at='2026-01-01T00:00:00Z' WHERE id=?", (b,))
        conn.execute("UPDATE conversations SET modified_at='2026-02-01T00:00:00Z' WHERE id=?", (a,))
    with db_connect() as conn:
        rows = db.list_conversations_for_user(conn, alice)
    assert [r["id"] for r in rows] == [a, b]
    assert {r["id"]: r["title"] for r in rows}[a] == "Alpha"


def test_conversation_rename_api(client):
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    conv_id = client.post(
        "/api/conversations", json={"mode": "chat"}, headers=_auth(token)
    ).json()["id"]
    r = client.patch(
        f"/api/conversations/{conv_id}", json={"title": "  Mon sujet  "}, headers=_auth(token)
    )
    assert r.status_code == 200
    assert r.json()["title"] == "Mon sujet"  # stripped
    assert client.get(
        f"/api/conversations/{conv_id}", headers=_auth(token)
    ).json()["title"] == "Mon sujet"
    listed = client.get("/api/conversations", headers=_auth(token)).json()["conversations"]
    assert next(c for c in listed if c["id"] == conv_id)["title"] == "Mon sujet"


def test_conversation_rename_empty_rejected(client):
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    conv_id = client.post(
        "/api/conversations", json={"mode": "chat"}, headers=_auth(token)
    ).json()["id"]
    assert client.patch(
        f"/api/conversations/{conv_id}", json={"title": "   "}, headers=_auth(token)
    ).status_code == 422


def test_conversation_delete_api(client):
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    conv_id = client.post(
        "/api/conversations", json={"mode": "chat"}, headers=_auth(token)
    ).json()["id"]
    with db_connect() as conn:
        folder = Path(db.get_conversation(conn, conv_id)["folder_path"])
    assert folder.exists()
    assert client.delete(f"/api/conversations/{conv_id}", headers=_auth(token)).status_code == 204
    assert client.get("/api/conversations", headers=_auth(token)).json()["conversations"] == []
    assert client.get(f"/api/conversations/{conv_id}", headers=_auth(token)).status_code == 404
    assert not folder.exists()  # on-disk folder removed too


def test_conversation_rename_delete_owner_scoped(client):
    _make_user("alice", "pw")
    _make_user("bob", "pw")
    tok_a = _login(client, "alice", "pw")
    tok_b = _login(client, "bob", "pw")
    conv_id = client.post(
        "/api/conversations", json={"mode": "chat"}, headers=_auth(tok_a)
    ).json()["id"]
    assert client.patch(
        f"/api/conversations/{conv_id}", json={"title": "x"}, headers=_auth(tok_b)
    ).status_code == 403
    assert client.delete(
        f"/api/conversations/{conv_id}", headers=_auth(tok_b)
    ).status_code == 403
    assert client.patch(f"/api/conversations/{conv_id}", json={"title": "x"}).status_code == 401
    assert client.delete(f"/api/conversations/{conv_id}").status_code == 401
