"""S3 — turn execution over WebSocket (live event streaming + sync->async bridge).

The happy path drives the REAL ``turn_runner`` via two scripted MockClients
(dispatcher -> deep ; main agent -> a direct final answer), so it exercises the
actual orchestrator path and proves events both stream live AND persist for
replay. Auth / ownership rejection and the unknown-message path are covered too.
Skipped without the ``[web]`` extras.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("argon2")
pytest.importorskip("itsdangerous")

from fastapi import WebSocketDisconnect  # noqa: E402, I001
from fastapi.testclient import TestClient  # noqa: E402

from jeanmichel import db, persistence  # noqa: E402
from jeanmichel.api import app as api_app  # noqa: E402
from jeanmichel.api import auth, executor  # noqa: E402
from jeanmichel.db import connect as db_connect  # noqa: E402
from jeanmichel.llm import MockClient  # noqa: E402
from jeanmichel.models import LLMResponse  # noqa: E402


# ---- Helpers --------------------------------------------------------------


def _make_user(username: str, password: str) -> int:
    with db_connect() as conn:
        return db.create_web_user(conn, username, auth.hash_password(password))


def _login(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def _create_conv(client: TestClient, token: str, mode: str = "analyse") -> str:
    resp = client.post(
        "/api/conversations", json={"mode": mode}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _conv_folder(conv_id: str) -> Path:
    with db_connect() as conn:
        row = db.get_conversation(conn, conv_id)
    return Path(row["folder_path"])


def _deep_clients() -> tuple[MockClient, MockClient]:
    """Dispatcher classifies DEEP ; main agent answers directly (no tool calls)."""
    dispatch = MockClient(
        script=[LLMResponse(thinking="", content='{"intent":"deep","tool":null,"args":{}}')]
    )
    main = MockClient(script=[LLMResponse(thinking="", content="The answer is 42.")])
    return dispatch, main


@pytest.fixture()
def client(tmp_db_v2) -> TestClient:
    return TestClient(api_app.create_app())


# ---- Happy path : real turn, live stream + persistence --------------------


def test_ws_streams_real_turn(client, monkeypatch):
    monkeypatch.setattr(executor, "get_llm_clients", _deep_clients)
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    conv_id = _create_conv(client, token)

    with client.websocket_connect(f"/ws/conversations/{conv_id}?token={token}") as ws:
        ws.send_json({"type": "turn", "text": "compare rust and go"})
        msgs = []
        while True:
            m = ws.receive_json()
            msgs.append(m)
            if m["type"] in ("final", "error"):
                break

    assert msgs[-1] == {"type": "final", "answer": "The answer is 42."}, msgs
    kinds = {m["type"] for m in msgs}
    assert "dispatch" in kinds
    ev_types = {m["event"]["type"] for m in msgs if m["type"] == "event"}
    assert {"RequestStarted", "RequestCompleted"} <= ev_types

    # Events are also persisted (replayable via the REST events endpoint).
    persisted = persistence.load_events(_conv_folder(conv_id))
    assert any(e["type"] == "RequestStarted" for e in persisted)


# ---- Auth / ownership rejection (handshake refused) -----------------------


def test_ws_rejects_missing_token(client):
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    conv_id = _create_conv(client, token)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        f"/ws/conversations/{conv_id}"
    ) as ws:
        ws.receive_json()


def test_ws_rejects_bad_token(client):
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    conv_id = _create_conv(client, token)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        f"/ws/conversations/{conv_id}?token=garbage"
    ) as ws:
        ws.receive_json()


def test_ws_rejects_foreign_conversation(client):
    _make_user("alice", "pw")
    _make_user("bob", "pw")
    tok_a = _login(client, "alice", "pw")
    tok_b = _login(client, "bob", "pw")
    conv_a = _create_conv(client, tok_a)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        f"/ws/conversations/{conv_a}?token={tok_b}"
    ) as ws:
        ws.receive_json()


# ---- Protocol : unknown message -------------------------------------------


def test_ws_unknown_message(client, monkeypatch):
    monkeypatch.setattr(executor, "get_llm_clients", lambda: (object(), object()))
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    conv_id = _create_conv(client, token)
    with client.websocket_connect(f"/ws/conversations/{conv_id}?token={token}") as ws:
        ws.send_json({"type": "ping"})
        m = ws.receive_json()
        assert m["type"] == "error"
