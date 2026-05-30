"""S2 — read API (owner-scoped GET endpoints).

Conversation messages / events / state / workspace + global user_memory.
Verifies owner scoping (403/404/401) and that workspace reads are confined by
``safe_resolve`` (path traversal blocked). Skipped without the ``[web]`` extras.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("argon2")
pytest.importorskip("itsdangerous")

from fastapi.testclient import TestClient  # noqa: E402, I001

from jeanmichel import db, persistence  # noqa: E402
from jeanmichel.api import app as api_app  # noqa: E402
from jeanmichel.api import auth  # noqa: E402
from jeanmichel.db import connect as db_connect  # noqa: E402
from jeanmichel.tools._workspace import workspace_root_for  # noqa: E402
from jeanmichel.tools.manage_user_memory import _handler as memory_handler  # noqa: E402


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


def _create_conv(client: TestClient, token: str, mode: str = "analyse") -> str:
    resp = client.post("/api/conversations", json={"mode": mode}, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _conv_folder(conv_id: str) -> Path:
    with db_connect() as conn:
        row = db.get_conversation(conn, conv_id)
    return Path(row["folder_path"])


@pytest.fixture()
def client(tmp_db_v2) -> TestClient:
    return TestClient(api_app.create_app())


@pytest.fixture()
def alice_conv(client) -> tuple[str, str, Path]:
    """A logged-in Alice + an owned conversation seeded with content."""
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    conv_id = _create_conv(client, token)
    folder = _conv_folder(conv_id)

    persistence.save_messages(folder, [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ])
    (folder / "events.jsonl").write_text(
        json.dumps({"type": "RequestStarted", "agent": "jean-michel"}) + "\n",
        encoding="utf-8",
    )
    (folder / "state.json").write_text(json.dumps({"depth_current": 2}), encoding="utf-8")
    ws = workspace_root_for(folder)
    (ws / "notes.md").write_text("# Notes\nbody line\n", encoding="utf-8")
    return token, conv_id, folder


# ---- Conversation reads ---------------------------------------------------


def test_get_messages(client, alice_conv):
    token, conv_id, _ = alice_conv
    resp = client.get(f"/api/conversations/{conv_id}/messages", headers=_auth(token))
    assert resp.status_code == 200
    msgs = resp.json()["messages"]
    assert [m["content"] for m in msgs] == ["hello", "hi there"]


def test_get_events(client, alice_conv):
    token, conv_id, _ = alice_conv
    resp = client.get(f"/api/conversations/{conv_id}/events", headers=_auth(token))
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert events[0]["type"] == "RequestStarted"


def test_get_state(client, alice_conv):
    token, conv_id, _ = alice_conv
    resp = client.get(f"/api/conversations/{conv_id}/state", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["state"]["depth_current"] == 2


# ---- Workspace reads ------------------------------------------------------


def test_workspace_tree_lists_file(client, alice_conv):
    token, conv_id, _ = alice_conv
    resp = client.get(f"/api/conversations/{conv_id}/workspace", headers=_auth(token))
    assert resp.status_code == 200
    names = {e["name"] for e in resp.json()["entries"]}
    assert "notes.md" in names


def test_workspace_file_read(client, alice_conv):
    token, conv_id, _ = alice_conv
    resp = client.get(
        f"/api/conversations/{conv_id}/workspace/file",
        params={"path": "notes.md"},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert "body line" in resp.json()["content"]


def test_workspace_file_not_found(client, alice_conv):
    token, conv_id, _ = alice_conv
    resp = client.get(
        f"/api/conversations/{conv_id}/workspace/file",
        params={"path": "ghost.md"},
        headers=_auth(token),
    )
    assert resp.status_code == 404


def test_workspace_path_traversal_blocked(client, alice_conv):
    """The acceptance guard : safe_resolve rejects '..' escapes → 400, not a read."""
    token, conv_id, _ = alice_conv
    resp = client.get(
        f"/api/conversations/{conv_id}/workspace/file",
        params={"path": "../../../../etc/passwd"},
        headers=_auth(token),
    )
    assert resp.status_code == 400


# ---- Owner scoping on reads -----------------------------------------------


def test_reads_are_owner_scoped(client, alice_conv):
    token_a, conv_id, _ = alice_conv
    _make_user("bob", "pw")
    token_b = _login(client, "bob", "pw")

    # Bob can't read Alice's conversation artifacts.
    assert client.get(f"/api/conversations/{conv_id}/messages", headers=_auth(token_b)).status_code == 403
    assert client.get(f"/api/conversations/{conv_id}/workspace", headers=_auth(token_b)).status_code == 403
    # Unknown conversation → 404.
    assert client.get("/api/conversations/nope/messages", headers=_auth(token_a)).status_code == 404
    # No token → 401.
    assert client.get(f"/api/conversations/{conv_id}/messages").status_code == 401


# ---- Memory reads ---------------------------------------------------------


def test_memory_list_and_recall(client):
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    _save_via_api(client, token, code="unity-mtl", description="senior dev", content="full body here")

    listed = client.get("/api/memory", headers=_auth(token))
    assert listed.status_code == 200
    entries = listed.json()["entries"]
    assert any(e["code"] == "unity-mtl" for e in entries)
    assert all("content" not in e for e in entries)  # index only

    recalled = client.get("/api/memory/user/unity-mtl", headers=_auth(token))
    assert recalled.status_code == 200
    assert recalled.json()["entry"]["content"] == "full body here"


def test_memory_filter_and_errors(client):
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    _save_via_api(client, token, type="feedback", code="kiss")

    assert client.get("/api/memory", params={"type": "feedback"}, headers=_auth(token)).status_code == 200
    assert client.get("/api/memory", params={"type": "garbage"}, headers=_auth(token)).status_code == 400
    assert client.get("/api/memory/user/ghost", headers=_auth(token)).status_code == 404
    assert client.get("/api/memory").status_code == 401  # auth required


# ---- Memory mutations (S5) ------------------------------------------------


def _save_via_api(client, token, **fields):
    body = {"type": "user", "code": "x", "title": "t", "description": "d", "content": "c"}
    body.update(fields)
    return client.post("/api/memory", json=body, headers=_auth(token))


def test_memory_save_then_recall(client):
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    resp = _save_via_api(client, token, code="unity-mtl", content="full body")
    assert resp.status_code == 201, resp.text
    assert resp.json()["saved"]["code"] == "unity-mtl"
    recalled = client.get("/api/memory/user/unity-mtl", headers=_auth(token))
    assert recalled.status_code == 200
    assert recalled.json()["entry"]["content"] == "full body"


def test_memory_save_duplicate_conflict(client):
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    assert _save_via_api(client, token, code="dup").status_code == 201
    assert _save_via_api(client, token, code="dup").status_code == 409


def test_memory_save_validation(client):
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    assert _save_via_api(client, token, type="garbage").status_code == 400  # invalid_type
    assert _save_via_api(client, token, title="x" * 61).status_code == 400  # title_too_long
    # Pydantic rejects a missing required field before reaching the service.
    bad = client.post("/api/memory", json={"type": "user", "code": "y"}, headers=_auth(token))
    assert bad.status_code == 422


def test_memory_update(client):
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    _save_via_api(client, token, code="z", title="old", description="od", content="oc")
    upd = client.patch("/api/memory/user/z", json={"title": "new"}, headers=_auth(token))
    assert upd.status_code == 200
    entry = client.get("/api/memory/user/z", headers=_auth(token)).json()["entry"]
    assert entry["title"] == "new"
    assert entry["description"] == "od"  # untouched
    assert client.patch("/api/memory/user/ghost", json={"title": "n"}, headers=_auth(token)).status_code == 404
    assert client.patch("/api/memory/user/z", json={}, headers=_auth(token)).status_code == 400  # no fields


def test_memory_delete(client):
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    _save_via_api(client, token, code="d1")
    assert client.delete("/api/memory/user/d1", headers=_auth(token)).status_code == 200
    assert client.get("/api/memory/user/d1", headers=_auth(token)).status_code == 404
    assert client.delete("/api/memory/user/ghost", headers=_auth(token)).status_code == 404


def test_memory_mutations_require_auth(client):
    body = {"type": "user", "code": "x", "title": "t", "description": "d", "content": "c"}
    assert client.post("/api/memory", json=body).status_code == 401
    assert client.patch("/api/memory/user/x", json={"title": "n"}).status_code == 401
    assert client.delete("/api/memory/user/x").status_code == 401


def test_memory_api_equals_tool(client):
    """Acceptance : API CRUD and the tool share one store — scoped to the SAME user."""
    alice_id = _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    # API write (alice) -> visible to the tool when scoped to alice.
    _save_via_api(client, token, code="shared", content="api-written")
    recalled = json.loads(memory_handler(action="recall", code="shared", user_id=alice_id))
    assert recalled["entry"]["content"] == "api-written"
    # Tool write (alice) -> visible to the API.
    memory_handler(
        action="save", type="feedback", code="tool-side",
        title="t", description="d", content="tool-written", user_id=alice_id,
    )
    api_entry = client.get("/api/memory/feedback/tool-side", headers=_auth(token)).json()["entry"]
    assert api_entry["content"] == "tool-written"


def test_memory_isolated_between_users(client):
    """The whole point : Alice's memory is invisible to Bob."""
    _make_user("alice", "pw")
    _make_user("bob", "pw")
    tok_a = _login(client, "alice", "pw")
    tok_b = _login(client, "bob", "pw")
    _save_via_api(client, tok_a, code="alice-secret", content="private")

    bob_codes = {e["code"] for e in client.get("/api/memory", headers=_auth(tok_b)).json()["entries"]}
    assert "alice-secret" not in bob_codes
    assert client.get("/api/memory/user/alice-secret", headers=_auth(tok_b)).status_code == 404
    assert client.get("/api/memory/user/alice-secret", headers=_auth(tok_a)).status_code == 200


# ---- User profile (M3) ----------------------------------------------------


def test_profile_get_defaults_empty(client):
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    resp = client.get("/api/profile", headers=_auth(token))
    assert resp.status_code == 200
    profile = resp.json()["profile"]
    assert set(profile) == set(db.WEB_PROFILE_FIELDS)
    assert all(v == "" for v in profile.values())


def test_profile_get_reflects_creation_fields(client):
    with db_connect() as conn:
        db.create_web_user(conn, "alice", auth.hash_password("pw"), name="Alice", city="Montréal")
    token = _login(client, "alice", "pw")
    profile = client.get("/api/profile", headers=_auth(token)).json()["profile"]
    assert profile["name"] == "Alice"
    assert profile["city"] == "Montréal"


def test_profile_patch_updates_and_persists(client):
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    patched = client.patch(
        "/api/profile", json={"city": "Laval", "language": "fr"}, headers=_auth(token)
    )
    assert patched.status_code == 200
    assert patched.json()["profile"]["city"] == "Laval"
    # Re-read confirms persistence ; unspecified fields stay untouched.
    profile = client.get("/api/profile", headers=_auth(token)).json()["profile"]
    assert profile["city"] == "Laval"
    assert profile["language"] == "fr"
    assert profile["name"] == ""


def test_profile_patch_ignores_unknown_and_null(client):
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    # Unknown keys are dropped by the model ; null values are skipped (no overwrite).
    resp = client.patch(
        "/api/profile",
        json={"city": "Laval", "ghost": "x", "country": None},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert resp.json()["profile"]["city"] == "Laval"


def test_profile_is_user_scoped(client):
    _make_user("alice", "pw")
    _make_user("bob", "pw")
    tok_a = _login(client, "alice", "pw")
    tok_b = _login(client, "bob", "pw")
    client.patch("/api/profile", json={"city": "Montréal"}, headers=_auth(tok_a))
    # Bob's profile is unaffected by Alice's update.
    assert client.get("/api/profile", headers=_auth(tok_b)).json()["profile"]["city"] == ""


def test_profile_requires_auth(client):
    assert client.get("/api/profile").status_code == 401
    assert client.patch("/api/profile", json={"city": "x"}).status_code == 401


# ---- TTS endpoint (S6) ----------------------------------------------------


def test_tts_requires_auth(client):
    assert client.get("/api/tts", params={"text": "hello"}).status_code == 401


def test_tts_returns_wav(client, monkeypatch):
    from jeanmichel import voice

    monkeypatch.setattr(voice, "synthesize_to_bytes", lambda text: b"RIFFfakewavdata")
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    r = client.get("/api/tts", params={"text": "hello"}, headers=_auth(token))
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.content == b"RIFFfakewavdata"


def test_tts_unavailable_returns_503(client, monkeypatch):
    from jeanmichel import voice

    monkeypatch.setattr(voice, "synthesize_to_bytes", lambda text: None)
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    r = client.get("/api/tts", params={"text": "hello"}, headers=_auth(token))
    assert r.status_code == 503
