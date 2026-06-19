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
from jeanmichel.service import consolidation  # noqa: E402
from jeanmichel.service import memory  # noqa: E402
from jeanmichel.tools._workspace import workspace_root_for  # noqa: E402
from jeanmichel.tools.manage_memory import _handler as memory_handler  # noqa: E402


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


def test_get_pending_memory(client, alice_conv):
    token, conv_id, _folder = alice_conv
    consolidation.add_pending(conv_id, [
        {"scope": "user", "code": "likes_tea", "title": "T", "description": "d", "content": "c"},
    ])
    resp = client.get(f"/api/conversations/{conv_id}/pending-memory", headers=_auth(token))
    assert resp.status_code == 200
    assert [c["code"] for c in resp.json()["pending_memory"]] == ["likes_tea"]


def test_dismiss_pending_memory_prunes_and_persists(client, alice_conv):
    token, conv_id, _folder = alice_conv
    a = {"scope": "user", "code": "a", "title": "A", "description": "d", "content": "c"}
    b = {"scope": "user", "code": "b", "title": "B", "description": "d", "content": "c"}
    consolidation.add_pending(conv_id, [a, b])
    resp = client.post(
        f"/api/conversations/{conv_id}/pending-memory/dismiss", json=a, headers=_auth(token)
    )
    assert resp.status_code == 200
    assert [c["code"] for c in resp.json()["pending_memory"]] == ["b"]
    # Persisted : a re-GET no longer returns the dismissed candidate (no resurrection).
    again = client.get(f"/api/conversations/{conv_id}/pending-memory", headers=_auth(token))
    assert [c["code"] for c in again.json()["pending_memory"]] == ["b"]


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


# ---- Workspace upload / download ------------------------------------------


def test_workspace_upload_and_download_roundtrip(client, alice_conv):
    token, conv_id, _ = alice_conv
    up = client.post(
        f"/api/conversations/{conv_id}/workspace/upload",
        files=[("files", ("hello.txt", b"hello bytes", "text/plain"))],
        headers=_auth(token),
    )
    assert up.status_code == 200, up.text
    result = up.json()["results"][0]
    assert result == {"status": "ok", "name": "hello.txt", "size_bytes": len(b"hello bytes")}

    dl = client.get(
        f"/api/conversations/{conv_id}/workspace/download",
        params={"path": "hello.txt"},
        headers=_auth(token),
    )
    assert dl.status_code == 200
    assert dl.content == b"hello bytes"
    assert "attachment" in dl.headers.get("content-disposition", "")


def test_workspace_upload_multiple_partial_conflict(client, alice_conv):
    # 'notes.md' is seeded by the fixture → conflict ; 'fresh.txt' is new → ok.
    token, conv_id, _ = alice_conv
    up = client.post(
        f"/api/conversations/{conv_id}/workspace/upload",
        files=[
            ("files", ("notes.md", b"x", "text/plain")),
            ("files", ("fresh.txt", b"y", "text/plain")),
        ],
        headers=_auth(token),
    )
    assert up.status_code == 200
    by_name = {r["name"]: r for r in up.json()["results"]}
    assert by_name["notes.md"]["status"] == "error"
    assert by_name["notes.md"]["code"] == "exists"
    assert by_name["fresh.txt"]["status"] == "ok"


def test_workspace_upload_too_large(client, alice_conv, monkeypatch):
    monkeypatch.setattr("jeanmichel.service.workspace.WORKSPACE_UPLOAD_MAX_BYTES", 4)
    token, conv_id, _ = alice_conv
    up = client.post(
        f"/api/conversations/{conv_id}/workspace/upload",
        files=[("files", ("big.bin", b"12345", "application/octet-stream"))],
        headers=_auth(token),
    )
    assert up.status_code == 200
    assert up.json()["results"][0]["code"] == "too_large"


def test_workspace_upload_sanitizes_filename(client, alice_conv):
    """A traversal-y filename is reduced to its basename — it can't escape."""
    token, conv_id, _ = alice_conv
    up = client.post(
        f"/api/conversations/{conv_id}/workspace/upload",
        files=[("files", ("../../escape.txt", b"z", "text/plain"))],
        headers=_auth(token),
    )
    assert up.status_code == 200
    assert up.json()["results"][0] == {"status": "ok", "name": "escape.txt", "size_bytes": 1}
    assert client.get(
        f"/api/conversations/{conv_id}/workspace/download",
        params={"path": "escape.txt"},
        headers=_auth(token),
    ).status_code == 200


def test_workspace_download_not_found(client, alice_conv):
    token, conv_id, _ = alice_conv
    resp = client.get(
        f"/api/conversations/{conv_id}/workspace/download",
        params={"path": "ghost.bin"},
        headers=_auth(token),
    )
    assert resp.status_code == 404


def test_workspace_upload_download_owner_scoped(client, alice_conv):
    token_a, conv_id, _ = alice_conv
    _make_user("bob", "pw")
    token_b = _login(client, "bob", "pw")
    files = [("files", ("x.txt", b"x", "text/plain"))]
    # Bob can neither push to nor pull from Alice's workspace.
    assert client.post(
        f"/api/conversations/{conv_id}/workspace/upload", files=files, headers=_auth(token_b)
    ).status_code == 403
    assert client.get(
        f"/api/conversations/{conv_id}/workspace/download",
        params={"path": "notes.md"},
        headers=_auth(token_b),
    ).status_code == 403
    # Auth required.
    assert client.post(
        f"/api/conversations/{conv_id}/workspace/upload", files=files
    ).status_code == 401
    assert client.get(
        f"/api/conversations/{conv_id}/workspace/download", params={"path": "notes.md"}
    ).status_code == 401


def test_filter_existing_validates(alice_conv):
    from jeanmichel.service import workspace as ws_svc

    _, _, folder = alice_conv  # workspace already seeded with notes.md
    kept = ws_svc.filter_existing(folder, ["notes.md", "ghost.md", "../escape", "notes.md"])
    assert kept == ["notes.md"]  # missing + traversal dropped, dedup, order kept


def test_workspace_zip_download(client, alice_conv):
    import io
    import zipfile

    token, conv_id, _ = alice_conv
    r = client.get(f"/api/conversations/{conv_id}/workspace/zip", headers=_auth(token))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers.get("content-disposition", "")
    assert "notes.md" in zipfile.ZipFile(io.BytesIO(r.content)).namelist()


def test_workspace_zip_empty_is_404(client):
    _make_user("bob", "pw")
    token = _login(client, "bob", "pw")
    conv_id = _create_conv(client, token)  # fresh, empty workspace
    assert client.get(f"/api/conversations/{conv_id}/workspace/zip", headers=_auth(token)).status_code == 404


def test_workspace_zip_owner_scoped(client, alice_conv):
    token_a, conv_id, _ = alice_conv
    _make_user("bob", "pw")
    token_b = _login(client, "bob", "pw")
    assert client.get(
        f"/api/conversations/{conv_id}/workspace/zip", headers=_auth(token_b)
    ).status_code == 403
    assert client.get(f"/api/conversations/{conv_id}/workspace/zip").status_code == 401


# ---- Workspace images (I1) ------------------------------------------------


def _make_png(path: Path, size: tuple[int, int] = (40, 30)) -> None:
    from PIL import Image

    Image.new("RGB", size, (200, 100, 50)).save(path, "PNG")


def test_workspace_image_original_mime(client, alice_conv):
    token, conv_id, folder = alice_conv
    from jeanmichel.tools._workspace import workspace_root_for

    _make_png(workspace_root_for(folder) / "pic.png")
    r = client.get(
        f"/api/conversations/{conv_id}/workspace/image",
        params={"path": "pic.png"},
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"  # real MIME, not octet-stream


def test_workspace_image_thumb_is_downscaled_webp(client, alice_conv):
    import io

    from PIL import Image

    token, conv_id, folder = alice_conv
    from jeanmichel.tools._workspace import workspace_root_for

    _make_png(workspace_root_for(folder) / "pic.png", size=(2000, 1500))
    r = client.get(
        f"/api/conversations/{conv_id}/workspace/image",
        params={"path": "pic.png", "thumb": "1"},
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/webp"
    assert max(Image.open(io.BytesIO(r.content)).size) <= 1024  # fit in 1024

    # The cache lives in a hidden .thumbs that must NOT show in the listing.
    names = {
        e["name"]
        for e in client.get(
            f"/api/conversations/{conv_id}/workspace", headers=_auth(token)
        ).json()["entries"]
    }
    assert ".thumbs" not in names


def test_workspace_image_svg_served_as_is(client, alice_conv):
    token, conv_id, folder = alice_conv
    from jeanmichel.tools._workspace import workspace_root_for

    (workspace_root_for(folder) / "logo.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8"
    )
    # thumb=1 but SVG isn't rasterizable → original svg, not webp.
    r = client.get(
        f"/api/conversations/{conv_id}/workspace/image",
        params={"path": "logo.svg", "thumb": "1"},
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert "svg" in r.headers["content-type"]


def test_workspace_image_owner_scoped(client, alice_conv):
    token_a, conv_id, folder = alice_conv
    from jeanmichel.tools._workspace import workspace_root_for

    _make_png(workspace_root_for(folder) / "pic.png")
    _make_user("bob", "pw")
    token_b = _login(client, "bob", "pw")
    base = f"/api/conversations/{conv_id}/workspace/image"
    assert client.get(base, params={"path": "pic.png"}, headers=_auth(token_b)).status_code == 403
    assert client.get(base, params={"path": "pic.png"}).status_code == 401
    assert client.get(base, params={"path": "ghost.png"}, headers=_auth(token_a)).status_code == 404


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
    _save_via_api(client, token, code="kiss")

    assert client.get("/api/memory", params={"scope": "user"}, headers=_auth(token)).status_code == 200
    assert client.get("/api/memory", params={"scope": "garbage"}, headers=_auth(token)).status_code == 400
    assert client.get("/api/memory/user/ghost", headers=_auth(token)).status_code == 404
    assert client.get("/api/memory").status_code == 401  # auth required


# ---- Memory mutations (S5) ------------------------------------------------


def _save_via_api(client, token, **fields):
    body = {"scope": "user", "code": "x", "title": "t", "description": "d", "content": "c"}
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
    assert _save_via_api(client, token, scope="garbage").status_code == 400  # invalid_scope
    assert _save_via_api(client, token, title="x" * 61).status_code == 400  # title_too_long
    # Pydantic rejects a missing required field before reaching the service.
    bad = client.post("/api/memory", json={"scope": "user", "code": "y"}, headers=_auth(token))
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
    body = {"scope": "user", "code": "x", "title": "t", "description": "d", "content": "c"}
    assert client.post("/api/memory", json=body).status_code == 401
    assert client.patch("/api/memory/user/x", json={"title": "n"}).status_code == 401
    assert client.delete("/api/memory/user/x").status_code == 401


def test_memory_api_equals_tool(client):
    """Acceptance : the API write path and the read-only tool share one store (same user)."""
    alice_id = _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    # API write (alice) -> visible to the read-only tool when scoped to alice.
    _save_via_api(client, token, code="shared", content="api-written")
    recalled = json.loads(
        memory_handler(action="recall", scope="user", code="shared", user_id=alice_id)
    )
    assert recalled["entry"]["content"] == "api-written"
    # A service write (the apply-on-approval path) is likewise visible to the API.
    with db_connect() as conn:
        memory.save(conn, scope="user", code="svc-side", title="t", description="d",
                    content="svc-written", user_id=alice_id)
    api_entry = client.get("/api/memory/user/svc-side", headers=_auth(token)).json()["entry"]
    assert api_entry["content"] == "svc-written"


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


# ---- Projects (owner-scoped) ----------------------------------------------


def test_project_crud_via_api(client):
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    # Create.
    resp = client.post("/api/projects", json={"code": "jm", "name": "Jean-Michel"}, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    pid = resp.json()["project"]["id"]
    # Duplicate code → 409.
    assert client.post("/api/projects", json={"code": "jm", "name": "x"}, headers=_auth(token)).status_code == 409
    # List.
    assert any(p["id"] == pid for p in client.get("/api/projects", headers=_auth(token)).json()["projects"])
    # Update.
    assert client.patch(f"/api/projects/{pid}", json={"status": "archived"}, headers=_auth(token)).status_code == 200
    # Delete.
    assert client.delete(f"/api/projects/{pid}", headers=_auth(token)).status_code == 200
    assert client.get(f"/api/projects/{pid}", headers=_auth(token)).status_code == 404


def test_projects_owner_scoped(client):
    _make_user("alice", "pw")
    _make_user("bob", "pw")
    tok_a = _login(client, "alice", "pw")
    tok_b = _login(client, "bob", "pw")
    pid = client.post("/api/projects", json={"code": "secret", "name": "S"}, headers=_auth(tok_a)).json()["project"]["id"]
    # Bob cannot see or touch Alice's project.
    assert client.get(f"/api/projects/{pid}", headers=_auth(tok_b)).status_code == 404
    assert client.patch(f"/api/projects/{pid}", json={"name": "n"}, headers=_auth(tok_b)).status_code == 404
    assert not client.get("/api/projects", headers=_auth(tok_b)).json()["projects"]
    assert client.get("/api/projects").status_code == 401  # auth required


def test_conversation_project_attach(client):
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    pid = client.post("/api/projects", json={"code": "p", "name": "P"}, headers=_auth(token)).json()["project"]["id"]
    # Create a conversation attached to the project.
    conv = client.post("/api/conversations", json={"mode": "chat", "project_id": pid}, headers=_auth(token))
    assert conv.status_code == 201
    conv_id = conv.json()["id"]
    assert client.get(f"/api/conversations/{conv_id}", headers=_auth(token)).json()["project_id"] == pid
    # Detach.
    assert client.put(f"/api/conversations/{conv_id}/project", json={"project_id": None}, headers=_auth(token)).status_code == 200
    assert client.get(f"/api/conversations/{conv_id}", headers=_auth(token)).json()["project_id"] is None
    # Attach to a foreign project → 404.
    _make_user("bob", "pw")
    tok_b = _login(client, "bob", "pw")
    bob_pid = client.post("/api/projects", json={"code": "b", "name": "B"}, headers=_auth(tok_b)).json()["project"]["id"]
    assert client.put(f"/api/conversations/{conv_id}/project", json={"project_id": bob_pid}, headers=_auth(token)).status_code == 404


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


# ---- STT endpoint (voice input ; faster-whisper mocked) -------------------


def test_stt_requires_auth(client):
    assert client.post(
        "/api/stt", files={"file": ("a.webm", b"audio", "audio/webm")}
    ).status_code == 401


def test_stt_returns_text(client, monkeypatch):
    from jeanmichel import stt

    monkeypatch.setattr(stt, "transcribe", lambda audio: {"text": "bonjour le monde", "language": "fr"})
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    r = client.post(
        "/api/stt", files={"file": ("a.webm", b"audio", "audio/webm")}, headers=_auth(token)
    )
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "bonjour le monde"
    assert body["language"] == "fr"


def test_stt_unavailable_returns_503(client, monkeypatch):
    from jeanmichel import stt

    monkeypatch.setattr(stt, "transcribe", lambda audio: None)
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    r = client.post(
        "/api/stt", files={"file": ("a.webm", b"x", "audio/webm")}, headers=_auth(token)
    )
    assert r.status_code == 503


def test_project_code_repo_roundtrip(client):
    """The project API round-trips code_repo/repo_kind (create + patch + validation)."""
    _make_user("alice", "pw")
    token = _login(client, "alice", "pw")
    r = client.post(
        "/api/projects",
        json={"code": "cloud", "name": "Cloud",
              "code_repo": "git@github.com:org/repo.git", "repo_kind": "ssh"},
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    proj = r.json()["project"]
    assert proj["code_repo"] == "git@github.com:org/repo.git" and proj["repo_kind"] == "ssh"

    r2 = client.patch(
        f"/api/projects/{proj['id']}",
        json={"code_repo": "/abs/local", "repo_kind": "local"}, headers=_auth(token),
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["project"]["code_repo"] == "/abs/local"

    bad = client.post(
        "/api/projects", json={"code": "bad", "name": "B", "repo_kind": "svn"},
        headers=_auth(token),
    )
    assert bad.status_code == 400
