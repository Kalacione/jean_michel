"""Conversation git-snapshot API : list / revert / fork.

Real git is required (the snapshot module shells out) ; skipped if git is
absent or the ``[web]`` extras are missing. The feature flag is enabled for
this module (the conftest autouse pins it off by default). Turns are simulated
by committing seeded content (the same ``snapshot.commit_turn`` run_turn calls
at end-of-turn) — no orchestrator/Ollama needed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("argon2")
pytest.importorskip("itsdangerous")

if shutil.which("git") is None:
    pytest.skip("git not available", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402, I001

from jeanmichel import config, db, persistence, snapshot  # noqa: E402
from jeanmichel.api import app as api_app  # noqa: E402
from jeanmichel.api import auth, executor  # noqa: E402
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


def _conv_folder(conv_id: str) -> Path:
    with db_connect() as conn:
        return Path(db.get_conversation(conn, conv_id)["folder_path"])


def _snapshots(client: TestClient, token: str, conv_id: str) -> list[dict]:
    resp = client.get(f"/api/conversations/{conv_id}/snapshots", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["snapshots"]


@pytest.fixture()
def client(tmp_db_v2, monkeypatch) -> TestClient:
    monkeypatch.setattr(config, "CONVERSATION_SNAPSHOT_ENABLED", True)
    return TestClient(api_app.create_app())


@pytest.fixture()
def alice_conv(client) -> tuple[str, str, Path]:
    """Alice + an owned conversation with one turn snapshot beyond ``init``."""
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    conv_id = client.post(
        "/api/conversations", json={"mode": "code"}, headers=_auth(token)
    ).json()["id"]
    folder = _conv_folder(conv_id)
    # Simulate one turn : write content + commit (what run_turn does at the end).
    persistence.save_messages(folder, [
        {"role": "user", "content": "écris tri.py"},
        {"role": "assistant", "content": "voilà tri.py"},
    ])
    (folder / "workspace").mkdir(exist_ok=True)
    (folder / "workspace" / "tri.py").write_text("x = 1\n", encoding="utf-8")
    snapshot.commit_turn(folder, "turn: écris tri.py")
    return token, conv_id, folder


def test_list_snapshots(client, alice_conv):
    token, conv_id, _ = alice_conv
    assert [s["subject"] for s in _snapshots(client, token, conv_id)] == [
        "init", "turn: écris tri.py",
    ]


def test_fork_creates_owned_conversation(client, alice_conv):
    token, conv_id, _ = alice_conv
    commit = _snapshots(client, token, conv_id)[1]["commit"]
    resp = client.post(
        f"/api/conversations/{conv_id}/fork", json={"commit": commit}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    new_id = resp.json()["id"]
    assert new_id != conv_id
    # The fork is owned by Alice (shows up in her list) and carries the content.
    listed = client.get("/api/conversations", headers=_auth(token)).json()["conversations"]
    assert any(c["id"] == new_id for c in listed)
    assert (_conv_folder(new_id) / "workspace" / "tri.py").exists()


def test_revert_rewinds_messages(client, alice_conv):
    token, conv_id, folder = alice_conv
    first_turn = _snapshots(client, token, conv_id)[1]["commit"]
    # Second turn, then revert to the first.
    persistence.save_messages(folder, [
        {"role": "user", "content": "écris tri.py"},
        {"role": "assistant", "content": "voilà tri.py"},
        {"role": "user", "content": "ajoute des tests"},
        {"role": "assistant", "content": "tests ajoutés"},
    ])
    (folder / "workspace" / "tests.py").write_text("assert True\n", encoding="utf-8")
    snapshot.commit_turn(folder, "turn: ajoute tests")

    resp = client.post(
        f"/api/conversations/{conv_id}/revert", json={"commit": first_turn}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    assert len(persistence.load_messages(folder)) == 2
    assert not (folder / "workspace" / "tests.py").exists()


def test_revert_409_when_turn_in_progress(client, alice_conv, monkeypatch):
    token, conv_id, _ = alice_conv
    commit = _snapshots(client, token, conv_id)[1]["commit"]
    monkeypatch.setattr(executor.turn_lock, "locked", lambda: True)
    resp = client.post(
        f"/api/conversations/{conv_id}/revert", json={"commit": commit}, headers=_auth(token)
    )
    assert resp.status_code == 409


def test_invalid_commit_rejected(client, alice_conv):
    token, conv_id, _ = alice_conv
    resp = client.post(
        f"/api/conversations/{conv_id}/revert", json={"commit": "not-a-sha!!"}, headers=_auth(token)
    )
    assert resp.status_code == 422


def test_ownership_enforced(client, alice_conv):
    _token_a, conv_id, _ = alice_conv
    _make_user("bob", "pw")
    btoken = _login(client, "bob", "pw")
    assert client.get(
        f"/api/conversations/{conv_id}/snapshots", headers=_auth(btoken)
    ).status_code in (403, 404)
    assert client.post(
        f"/api/conversations/{conv_id}/fork", json={"commit": "0" * 40}, headers=_auth(btoken)
    ).status_code in (403, 404)
