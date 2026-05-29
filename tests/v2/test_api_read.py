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
    memory_handler(
        action="save", type="user", code="unity-mtl",
        title="Dev", description="senior dev", content="full body here",
    )

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
    memory_handler(action="save", type="feedback", code="kiss", title="t", description="d", content="c")

    assert client.get("/api/memory", params={"type": "feedback"}, headers=_auth(token)).status_code == 200
    assert client.get("/api/memory", params={"type": "garbage"}, headers=_auth(token)).status_code == 400
    assert client.get("/api/memory/user/ghost", headers=_auth(token)).status_code == 404
    assert client.get("/api/memory").status_code == 401  # auth required
