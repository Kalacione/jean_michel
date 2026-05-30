"""FastAPI application factory + entrypoint.

S0 : health check. S1 : auth (login / me) + multi-user conversations
(create / list / get) scoped by owner. Each route delegates to the shared
service layer + ``auth``. FastAPI/pydantic are imported lazily inside
``create_app`` so importing this module stays cheap and the web extras stay
optional for the rest of the codebase.
"""
# NOTE: deliberately NO ``from __future__ import annotations`` here. FastAPI
# resolves route signatures via get_type_hints ; with stringized annotations it
# cannot resolve the Pydantic request models defined locally inside
# ``create_app`` (they aren't in module globals), and would treat the request
# body as a query param. Real annotations keep the models local AND working.

import os
from typing import Any

# Daemon binds the LAN by default (auth is the gate — cf. audit). Override via
# JEANMICHEL_API_HOST / JEANMICHEL_API_PORT.
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000


def create_app() -> Any:
    """Build the FastAPI app. Imports web deps lazily (optional dependency)."""
    from pathlib import Path

    from fastapi import (
        Depends,
        FastAPI,
        File,
        HTTPException,
        Response,
        UploadFile,
        WebSocket,
        WebSocketDisconnect,
    )
    from fastapi.responses import FileResponse
    from pydantic import BaseModel

    from .. import db, persistence
    from ..config import UserProfile
    from ..service import conversation as conversation_svc
    from ..service import memory as memory_svc
    from ..service import workspace as workspace_svc
    from . import auth, executor

    app = FastAPI(title="Jean-Michel API", version="0.1.0")

    class LoginRequest(BaseModel):
        username: str
        password: str

    class CreateConversationRequest(BaseModel):
        mode: str = "analyse"

    class ConversationRename(BaseModel):
        title: str

    class MemorySaveRequest(BaseModel):
        type: str
        code: str
        title: str
        description: str
        content: str

    class MemoryUpdateRequest(BaseModel):
        title: str | None = None
        description: str | None = None
        content: str | None = None

    class ProfileUpdate(BaseModel):
        name: str | None = None
        birthdate: str | None = None
        city: str | None = None
        country: str | None = None
        language: str | None = None
        interests: str | None = None
        notes: str | None = None

    # ---- health ----------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # ---- auth ------------------------------------------------------------

    @app.post("/api/auth/login")
    def login(body: LoginRequest) -> dict[str, Any]:
        user = auth.authenticate(body.username, body.password)
        if user is None:
            raise HTTPException(status_code=401, detail="invalid credentials")
        return {"token": auth.make_token(user), "user": user}

    @app.get("/api/auth/me")
    def me(user: dict = Depends(auth.current_user)) -> dict[str, Any]:
        return {"user": user}

    # ---- conversations (owner-scoped) ------------------------------------

    @app.post("/api/conversations", status_code=201)
    def create_conversation(
        body: CreateConversationRequest, user: dict = Depends(auth.current_user)
    ) -> dict[str, Any]:
        if body.mode not in ("analyse", "chat", "vocal"):
            raise HTTPException(status_code=422, detail="invalid mode")
        conv_id, _folder = conversation_svc.create_conversation(body.mode)
        with db.connect() as conn:
            db.associate_conversation_user(conn, user["id"], conv_id)
        return {"id": conv_id, "mode": body.mode, "status": "active"}

    @app.get("/api/conversations")
    def list_conversations(user: dict = Depends(auth.current_user)) -> dict[str, Any]:
        with db.connect() as conn:
            rows = db.list_conversations_for_user(conn, user["id"])
        return {"conversations": [dict(r) for r in rows]}

    @app.get("/api/conversations/{conversation_id}")
    def get_conversation(conv: Any = Depends(auth.require_conversation_owner)) -> dict[str, Any]:
        return {
            "id": conv["id"],
            "title": conv["title"],
            "mode": conv["mode"],
            "status": conv["status"],
            "user_language": conv["user_language"],
            "created_at": conv["created_at"],
            "modified_at": conv["modified_at"],
        }

    @app.patch("/api/conversations/{conversation_id}")
    def rename_conversation(
        body: ConversationRename, conv: Any = Depends(auth.require_conversation_owner)
    ) -> dict[str, Any]:
        title = body.title.strip()[:120]
        if not title:
            raise HTTPException(status_code=422, detail="title must not be empty")
        with db.connect() as conn:
            db.rename_conversation(conn, conv["id"], title)
        return {"id": conv["id"], "title": title}

    @app.delete("/api/conversations/{conversation_id}", status_code=204)
    def delete_conversation(conv: Any = Depends(auth.require_conversation_owner)) -> Response:
        conversation_svc.delete_conversation(conv["id"])
        return Response(status_code=204)

    @app.get("/api/conversations/{conversation_id}/messages")
    def get_messages(conv: Any = Depends(auth.require_conversation_owner)) -> dict[str, Any]:
        return {"messages": persistence.load_messages(Path(conv["folder_path"]))}

    @app.get("/api/conversations/{conversation_id}/events")
    def get_events(conv: Any = Depends(auth.require_conversation_owner)) -> dict[str, Any]:
        return {"events": persistence.load_events(Path(conv["folder_path"]))}

    @app.get("/api/conversations/{conversation_id}/state")
    def get_state(conv: Any = Depends(auth.require_conversation_owner)) -> dict[str, Any]:
        return {"state": persistence.load_state(Path(conv["folder_path"]))}

    @app.get("/api/conversations/{conversation_id}/workspace")
    def get_workspace(
        sub_path: str = "", conv: Any = Depends(auth.require_conversation_owner)
    ) -> dict[str, Any]:
        try:
            return workspace_svc.list_tree(Path(conv["folder_path"]), sub_path)
        except workspace_svc.WorkspaceError as exc:
            raise HTTPException(
                status_code=404 if exc.code == "not_found" else 400, detail=exc.message
            ) from exc

    @app.get("/api/conversations/{conversation_id}/workspace/file")
    def get_workspace_file(
        path: str, conv: Any = Depends(auth.require_conversation_owner)
    ) -> dict[str, Any]:
        try:
            return workspace_svc.read_file(Path(conv["folder_path"]), path)
        except workspace_svc.WorkspaceError as exc:
            status = {"not_found": 404, "not_utf8": 415}.get(exc.code, 400)
            raise HTTPException(status_code=status, detail=exc.message) from exc

    @app.get("/api/conversations/{conversation_id}/workspace/download")
    def download_workspace_file(
        path: str, conv: Any = Depends(auth.require_conversation_owner)
    ) -> Any:
        try:
            target = workspace_svc.resolve_download(Path(conv["folder_path"]), path)
        except workspace_svc.WorkspaceError as exc:
            raise HTTPException(
                status_code=404 if exc.code == "not_found" else 400, detail=exc.message
            ) from exc
        return FileResponse(
            target, filename=target.name, media_type="application/octet-stream"
        )

    @app.post("/api/conversations/{conversation_id}/workspace/upload")
    async def upload_workspace_files(
        files: list[UploadFile] = File(...),
        conv: Any = Depends(auth.require_conversation_owner),
    ) -> dict[str, Any]:
        # Per-file verdicts : a batch may partially succeed (some saved, some
        # rejected for size / conflict / quota). The whole call still returns 200.
        folder = Path(conv["folder_path"])
        results: list[dict[str, Any]] = []
        for f in files:
            data = await f.read()
            try:
                saved = workspace_svc.save_upload(folder, f.filename or "", data)
                results.append({"status": "ok", **saved})
            except workspace_svc.WorkspaceError as exc:
                results.append(
                    {
                        "status": "error",
                        "name": f.filename,
                        "code": exc.code,
                        "detail": exc.message,
                    }
                )
        return {"results": results}

    @app.get("/api/conversations/{conversation_id}/workspace/zip")
    def download_workspace_zip(conv: Any = Depends(auth.require_conversation_owner)) -> Any:
        from starlette.background import BackgroundTask

        zip_path = workspace_svc.zip_workspace(Path(conv["folder_path"]))
        if zip_path is None:
            raise HTTPException(status_code=404, detail="workspace is empty")
        return FileResponse(
            zip_path,
            filename="workspace.zip",
            media_type="application/zip",
            background=BackgroundTask(lambda: zip_path.unlink(missing_ok=True)),
        )

    # ---- user memory (read ; global, but auth-gated) ---------------------

    @app.get("/api/memory")
    def list_memory(
        type: str | None = None, user: dict = Depends(auth.current_user)
    ) -> dict[str, Any]:
        try:
            with db.connect() as conn:
                entries = memory_svc.list_(conn, user_id=user["id"], type_filter=type)
        except memory_svc.MemoryOpError as exc:
            raise HTTPException(status_code=400, detail=exc.message) from exc
        return {"entries": entries}

    @app.get("/api/memory/{type}/{code}")
    def recall_memory(
        type: str, code: str, user: dict = Depends(auth.current_user)
    ) -> dict[str, Any]:
        with db.connect() as conn:
            rows = memory_svc.recall(conn, user_id=user["id"], code=code)
        match = next((r for r in rows if r["type"] == type), None)
        if match is None:
            raise HTTPException(status_code=404, detail=f"no {type}/{code} entry")
        return {"entry": match}

    # Memory mutations — auth-gated but GLOBAL in v1 (shared across web users ;
    # documented limitation). CRUD here is equivalent to the manage_user_memory
    # tool (same service.memory functions, same validation + caps).
    def _memory_http(exc: memory_svc.MemoryOpError) -> HTTPException:
        status = {"already_exists": 409, "not_found": 404, "ambiguous": 409}.get(
            exc.code, 400
        )
        return HTTPException(status_code=status, detail=exc.message)

    @app.post("/api/memory", status_code=201)
    def save_memory(
        body: MemorySaveRequest, user: dict = Depends(auth.current_user)
    ) -> dict[str, Any]:
        try:
            with db.connect() as conn:
                saved = memory_svc.save(
                    conn,
                    user_id=user["id"],
                    type_=body.type,
                    code=body.code,
                    title=body.title,
                    description=body.description,
                    content=body.content,
                )
        except memory_svc.MemoryOpError as exc:
            raise _memory_http(exc) from exc
        return {"saved": saved}

    @app.patch("/api/memory/{type}/{code}")
    def update_memory(
        type: str,
        code: str,
        body: MemoryUpdateRequest,
        user: dict = Depends(auth.current_user),
    ) -> dict[str, Any]:
        try:
            with db.connect() as conn:
                target_id = memory_svc.update(
                    conn,
                    user_id=user["id"],
                    code=code,
                    type_=type,
                    title=body.title,
                    description=body.description,
                    content=body.content,
                )
        except memory_svc.MemoryOpError as exc:
            raise _memory_http(exc) from exc
        return {"updated_id": target_id}

    @app.delete("/api/memory/{type}/{code}")
    def delete_memory(
        type: str, code: str, user: dict = Depends(auth.current_user)
    ) -> dict[str, Any]:
        try:
            with db.connect() as conn:
                target_id = memory_svc.delete(conn, user_id=user["id"], code=code, type_=type)
        except memory_svc.MemoryOpError as exc:
            raise _memory_http(exc) from exc
        return {"deleted_id": target_id}

    # ---- user profile (structured ; filled at creation, editable by the user) -

    @app.get("/api/profile")
    def get_profile(user: dict = Depends(auth.current_user)) -> dict[str, Any]:
        with db.connect() as conn:
            row = db.get_web_user_by_id(conn, user["id"])
        return {
            "profile": {f: (row[f] if row is not None else "") for f in db.WEB_PROFILE_FIELDS}
        }

    @app.patch("/api/profile")
    def update_profile(
        body: ProfileUpdate, user: dict = Depends(auth.current_user)
    ) -> dict[str, Any]:
        patch = {k: v for k, v in body.model_dump().items() if v is not None}
        with db.connect() as conn:
            db.update_web_user_profile(conn, user["id"], **patch)
            row = db.get_web_user_by_id(conn, user["id"])
        return {"profile": {f: row[f] for f in db.WEB_PROFILE_FIELDS}}

    # ---- text-to-speech (vocal mode ; on-demand, off the turn critical path) -

    @app.get("/api/tts")
    def tts(text: str, user: dict = Depends(auth.current_user)) -> Response:
        from .. import voice

        wav = voice.synthesize_to_bytes(text)
        if wav is None:
            raise HTTPException(
                status_code=503,
                detail="tts unavailable (no voice model or synthesis failed)",
            )
        return Response(content=wav, media_type="audio/wav")

    # ---- turn WebSocket (live event stream) ------------------------------

    @app.websocket("/ws/conversations/{conversation_id}")
    async def ws_turn(
        websocket: WebSocket, conversation_id: str, token: str = ""
    ) -> None:
        # Auth + ownership happen BEFORE accept() so a failure rejects the
        # handshake (the client sees a refused connection).
        user = auth.verify_token(token)
        if user is None:
            await websocket.close(code=4401)
            return
        with db.connect() as conn:
            row = db.get_conversation(conn, conversation_id)
            owned = row is not None and db.user_owns_conversation(
                conn, user["id"], row["id"]
            )
        if row is None:
            await websocket.close(code=4404)
            return
        if not owned:
            await websocket.close(code=4403)
            return

        await websocket.accept()
        folder = Path(row["folder_path"])
        mode = row["mode"]
        # Per-user : the turn runs as the conversation's owner — its profile
        # comes from the web_users columns, its memory is scoped to its id.
        with db.connect() as conn:
            profile = UserProfile.from_row(db.get_web_user_by_id(conn, user["id"]))
        dispatch_llm, main_llm = executor.get_llm_clients()
        try:
            while True:
                data = await websocket.receive_json()
                if data.get("type") != "turn":
                    await websocket.send_json(
                        {"type": "error", "detail": "expected {type:'turn', text:...}"}
                    )
                    continue
                if executor.turn_lock.locked():
                    await websocket.send_json({"type": "queued"})
                async with executor.turn_lock:
                    await executor.run_turn_streaming(
                        websocket,
                        conv_id=conversation_id,
                        folder=folder,
                        user_text=data.get("text", ""),
                        mode=mode,
                        profile=profile,
                        dispatch_llm=dispatch_llm,
                        main_llm=main_llm,
                        memory_user_id=user["id"],
                        attachments=workspace_svc.filter_existing(folder, data.get("files") or []),
                    )
        except WebSocketDisconnect:
            return

    return app


def run() -> None:
    """Entrypoint for ``jean-michel-serve`` / ``./jm.sh --serve``."""
    import uvicorn

    host = os.environ.get("JEANMICHEL_API_HOST", DEFAULT_HOST)
    port = int(os.environ.get("JEANMICHEL_API_PORT", DEFAULT_PORT))
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    run()
