"""Paradigm curation API (Phase 1) — list / get / patch / bind + promotion review.

The editor surface behind the web ParadigmsDialog. Reads expose EVERY paradigm with
full metadata (unlike the agent-injection view) ; writes go through db.* ; the promotion
queue (kind='rule' candidates, cross-conversation) is reviewed here. Auth-gated
(single-user system). Skipped without the ``[web]`` extras.
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
from jeanmichel.service import consolidation  # noqa: E402


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
def token(client) -> str:
    _make_user("alice", "pw")
    return _login(client, "alice", "pw")


def _first_paradigm(client, token) -> dict:
    paradigms = client.get("/api/paradigms", headers=_auth(token)).json()["paradigms"]
    assert paradigms, "seed DB should ship baseline paradigms"
    return paradigms[0]


def _seed_rule(client, token) -> tuple[str, dict]:
    """A pending rule candidate on a fresh conversation, grounded on a real category."""
    p = _first_paradigm(client, token)
    conv_id = client.post(
        "/api/conversations", json={"mode": "analyse"}, headers=_auth(token)
    ).json()["id"]
    cand = {
        "kind": "rule",
        "section_code": p["section_code"],
        "category_code": p["category_code"],
        "title": "Wibblezorp the flibber",
        "content": "- Always wibblezorp the flibber before frobnicating.",
        "grounding_quote": "user said wibblezorp",
        "suggested_action": "create",
    }
    consolidation.add_pending(conv_id, [cand])
    return conv_id, cand


# ---- list / get -----------------------------------------------------------


def test_list_paradigms_full_metadata(client, token):
    resp = client.get("/api/paradigms", headers=_auth(token))
    assert resp.status_code == 200
    paradigms = resp.json()["paradigms"]
    assert paradigms
    p = paradigms[0]
    for k in (
        "id", "code", "title", "content", "rationale", "is_global", "active",
        "order_priority", "section_code", "category_code", "agents", "modes",
    ):
        assert k in p, f"missing {k}"
    assert isinstance(p["agents"], list)
    assert isinstance(p["modes"], list)
    assert isinstance(p["is_global"], bool)
    assert isinstance(p["active"], bool)


def test_get_paradigm_by_code(client, token):
    code = _first_paradigm(client, token)["code"]
    resp = client.get(f"/api/paradigms/{code}", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["paradigm"]["code"] == code


def test_get_unknown_paradigm_404(client, token):
    assert client.get("/api/paradigms/nope-xyz", headers=_auth(token)).status_code == 404


# ---- patch : edit content/rationale, toggle active, set modes -------------


def test_patch_content_and_rationale_persist(client, token):
    code = _first_paradigm(client, token)["code"]
    resp = client.patch(
        f"/api/paradigms/{code}",
        json={"content": "- brand new content", "rationale": "why-note"},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert resp.json()["paradigm"]["content"] == "- brand new content"
    again = client.get(f"/api/paradigms/{code}", headers=_auth(token)).json()["paradigm"]
    assert again["content"] == "- brand new content"
    assert again["rationale"] == "why-note"


def test_patch_toggle_active_and_modes(client, token):
    code = _first_paradigm(client, token)["code"]
    resp = client.patch(
        f"/api/paradigms/{code}",
        json={"active": False, "modes": ["chat", "analyse"]},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    p = resp.json()["paradigm"]
    assert p["active"] is False
    assert set(p["modes"]) == {"chat", "analyse"}
    cleared = client.patch(f"/api/paradigms/{code}", json={"modes": []}, headers=_auth(token))
    assert cleared.json()["paradigm"]["modes"] == []


def test_patch_invalid_mode_400(client, token):
    code = _first_paradigm(client, token)["code"]
    resp = client.patch(f"/api/paradigms/{code}", json={"modes": ["bogus"]}, headers=_auth(token))
    assert resp.status_code == 400


def test_patch_unknown_404(client, token):
    resp = client.patch("/api/paradigms/nope", json={"title": "x"}, headers=_auth(token))
    assert resp.status_code == 404


# ---- bind / unbind --------------------------------------------------------


def test_bind_and_unbind_agent(client, token):
    code = _first_paradigm(client, token)["code"]
    agent = client.get("/api/agents", headers=_auth(token)).json()["agents"][0]["code"]
    bound = client.post(f"/api/paradigms/{code}/bind", json={"agent": agent}, headers=_auth(token))
    assert bound.status_code == 200
    assert agent in bound.json()["paradigm"]["agents"]
    unbound = client.delete(f"/api/paradigms/{code}/bind/{agent}", headers=_auth(token))
    assert unbound.status_code == 200
    assert agent not in unbound.json()["paradigm"]["agents"]


def test_bind_unknown_agent_404(client, token):
    code = _first_paradigm(client, token)["code"]
    resp = client.post(f"/api/paradigms/{code}/bind", json={"agent": "ghost"}, headers=_auth(token))
    assert resp.status_code == 404


# ---- promotions (rule candidates) -----------------------------------------


def test_list_promotions(client, token):
    conv_id, cand = _seed_rule(client, token)
    resp = client.get("/api/paradigms/promotions", headers=_auth(token))
    assert resp.status_code == 200
    promos = resp.json()["promotions"]
    assert any(
        pr["conversation_id"] == conv_id and pr["candidate"]["title"] == cand["title"]
        for pr in promos
    )


def test_apply_promotion_creates_dark_paradigm(client, token):
    conv_id, cand = _seed_rule(client, token)
    resp = client.post(
        "/api/paradigms/promotions/apply",
        json={"conversation_id": conv_id, "candidate": cand, "action": "create"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    new_code = resp.json()["applied"]["code"]
    created = client.get(f"/api/paradigms/{new_code}", headers=_auth(token)).json()["paradigm"]
    assert created["active"] is False  # DARK — nothing auto-applies
    assert created["content"] == cand["content"]
    # The candidate left the queue (marked applied).
    assert not any(pr["candidate"]["title"] == cand["title"] for pr in resp.json()["promotions"])


def test_dismiss_promotion(client, token):
    conv_id, cand = _seed_rule(client, token)
    resp = client.post(
        "/api/paradigms/promotions/dismiss",
        json={"conversation_id": conv_id, "candidate": cand},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert not any(pr["candidate"]["title"] == cand["title"] for pr in resp.json()["promotions"])
    # Dismissed → never resurfaces.
    again = client.get("/api/paradigms/promotions", headers=_auth(token)).json()["promotions"]
    assert not any(pr["candidate"]["title"] == cand["title"] for pr in again)


# ---- auth -----------------------------------------------------------------


def test_paradigm_endpoints_require_auth(client):
    assert client.get("/api/paradigms").status_code == 401
    assert client.get("/api/paradigms/x").status_code == 401
    assert client.patch("/api/paradigms/x", json={"title": "n"}).status_code == 401
    assert client.post("/api/paradigms/x/bind", json={"agent": "a"}).status_code == 401
    assert client.get("/api/paradigms/promotions").status_code == 401
    assert client.post(
        "/api/paradigms/promotions/dismiss", json={"conversation_id": "c", "candidate": {}}
    ).status_code == 401
