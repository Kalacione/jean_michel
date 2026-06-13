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

import asyncio
import contextlib
import os
from typing import Any

# Daemon binds the LAN by default (auth is the gate — cf. audit). Override via
# JEANMICHEL_API_HOST / JEANMICHEL_API_PORT.
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000


def _valid_commit(commit: str) -> bool:
    """Accept only a git SHA-ish hex string (7-40 chars) before passing to git."""
    return 7 <= len(commit) <= 40 and all(c in "0123456789abcdefABCDEF" for c in commit)


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

    from .. import db, mcp_client, persistence, snapshot
    from ..config import UserProfile
    from ..service import conversation as conversation_svc
    from ..service import memory as memory_svc
    from ..service import project as project_svc
    from ..service import workspace as workspace_svc
    from . import auth, executor, notifications, project_build

    @contextlib.asynccontextmanager
    async def _lifespan(_app):
        from ..tools.bash_sandbox import reap_sandboxes

        # Capture the serving loop so background threads (the project image build)
        # can push notifications over the per-user WS thread-safely.
        notifications.set_loop(asyncio.get_running_loop())
        # Sweep orphan sandbox/project containers left by a previous (possibly
        # crashed) run before serving. Best-effort.
        await asyncio.to_thread(reap_sandboxes)
        # Connect to MCP servers at startup (off-loop : startup() blocks on a
        # bounded connect). No-op when MCP is off/unconfigured. Best-effort.
        await asyncio.to_thread(mcp_client.startup)
        try:
            yield
        finally:
            await asyncio.to_thread(mcp_client.shutdown)
            # Stop all jm-sandbox-* / jm-repo-* containers on clean shutdown.
            await asyncio.to_thread(reap_sandboxes)

    app = FastAPI(title="Jean-Michel API", version="0.1.0", lifespan=_lifespan)

    class LoginRequest(BaseModel):
        username: str
        password: str

    class CreateConversationRequest(BaseModel):
        mode: str = "analyse"
        project_id: int | None = None

    class ConversationRename(BaseModel):
        title: str

    class ConversationProject(BaseModel):
        project_id: int | None = None  # None → detach

    class ProjectSaveRequest(BaseModel):
        code: str
        name: str
        description: str = ""
        code_repo: str = ""          # local path or ssh url (empty → no repo / no codebase in code mode)
        repo_kind: str = "local"     # 'local' | 'ssh'
        dockerfile: str = ""         # project sandbox Dockerfile (empty → repo-default bash+git)

    class ProjectUpdateRequest(BaseModel):
        name: str | None = None
        description: str | None = None
        status: str | None = None
        code_repo: str | None = None
        repo_kind: str | None = None
        dockerfile: str | None = None

    class SnapshotRef(BaseModel):
        commit: str

    class MemorySaveRequest(BaseModel):
        scope: str
        code: str
        title: str
        description: str
        content: str
        project_id: int | None = None
        tool_code: str | None = None

    class MemoryUpdateRequest(BaseModel):
        title: str | None = None
        description: str | None = None
        content: str | None = None
        project_id: int | None = None
        tool_code: str | None = None

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
        if body.mode not in ("analyse", "chat", "vocal", "code"):
            raise HTTPException(status_code=422, detail="invalid mode")
        if body.project_id is not None:
            # Only attach to a project the caller owns.
            with db.connect() as conn:
                try:
                    project_svc.get_owned(conn, user_id=user["id"], project_id=body.project_id)
                except project_svc.ProjectOpError as exc:
                    raise HTTPException(status_code=404, detail=exc.message) from exc
        conv_id, _folder = conversation_svc.create_conversation(
            body.mode, project_id=body.project_id
        )
        with db.connect() as conn:
            db.associate_conversation_user(conn, user["id"], conv_id)
        return {"id": conv_id, "mode": body.mode, "status": "active", "project_id": body.project_id}

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
            "project_id": conv["project_id"],
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

    @app.put("/api/conversations/{conversation_id}/project")
    def set_conversation_project_route(
        body: ConversationProject,
        conv: Any = Depends(auth.require_conversation_owner),
        user: dict = Depends(auth.current_user),
    ) -> dict[str, Any]:
        """Attach (project_id) or detach (null) a conversation to one of the
        caller's projects. Takes effect on the next turn's prompt."""
        with db.connect() as conn:
            if body.project_id is not None:
                try:
                    project_svc.get_owned(conn, user_id=user["id"], project_id=body.project_id)
                except project_svc.ProjectOpError as exc:
                    raise HTTPException(status_code=404, detail=exc.message) from exc
            db.set_conversation_project(conn, conv["id"], body.project_id)
        return {"id": conv["id"], "project_id": body.project_id}

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

    # ---- conversation snapshots (git per conversation) -------------------

    @app.get("/api/conversations/{conversation_id}/snapshots")
    def get_snapshots(conv: Any = Depends(auth.require_conversation_owner)) -> dict[str, Any]:
        return {"snapshots": snapshot.list_snapshots(Path(conv["folder_path"]))}

    @app.post("/api/conversations/{conversation_id}/revert")
    def revert_conversation_route(
        body: SnapshotRef, conv: Any = Depends(auth.require_conversation_owner)
    ) -> dict[str, Any]:
        commit = body.commit.strip()
        if not _valid_commit(commit):
            raise HTTPException(status_code=422, detail="invalid commit")
        # Don't rewind while any turn is writing to a conversation folder.
        if executor.turn_lock.locked():
            raise HTTPException(status_code=409, detail="a turn is in progress")
        if not conversation_svc.revert_conversation(conv["id"], commit):
            raise HTTPException(
                status_code=400, detail="revert failed (snapshots disabled or unknown commit)"
            )
        return {"status": "ok"}

    @app.post("/api/conversations/{conversation_id}/fork", status_code=201)
    def fork_conversation_route(
        body: SnapshotRef,
        conv: Any = Depends(auth.require_conversation_owner),
        user: dict = Depends(auth.current_user),
    ) -> dict[str, Any]:
        commit = body.commit.strip()
        if not _valid_commit(commit):
            raise HTTPException(status_code=422, detail="invalid commit")
        # Fork reads committed history (not the working tree) → safe mid-turn.
        try:
            new_id, _ = conversation_svc.fork_conversation(conv["id"], commit)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with db.connect() as conn:
            db.associate_conversation_user(conn, user["id"], new_id)
        return {"id": new_id}

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

    @app.get("/api/conversations/{conversation_id}/workspace/image")
    def get_workspace_image(
        path: str,
        thumb: bool = False,
        conv: Any = Depends(auth.require_conversation_owner),
    ) -> Any:
        # Serves an image with its real MIME ; with thumb=1, a cached ≤IMAGE_MAX_PX
        # WebP derivative (SVG / non-raster fall back to the original).
        try:
            target, media_type = workspace_svc.resolve_image(
                Path(conv["folder_path"]), path, thumb=thumb
            )
        except workspace_svc.WorkspaceError as exc:
            raise HTTPException(
                status_code=404 if exc.code == "not_found" else 400, detail=exc.message
            ) from exc
        return FileResponse(target, media_type=media_type)

    # ---- projects (owner-scoped) -----------------------------------------
    def _project_http(exc: project_svc.ProjectOpError) -> HTTPException:
        status = {"already_exists": 409, "not_found": 404}.get(exc.code, 400)
        return HTTPException(status_code=status, detail=exc.message)

    @app.get("/api/projects")
    def list_projects(
        include_archived: bool = True, user: dict = Depends(auth.current_user)
    ) -> dict[str, Any]:
        with db.connect() as conn:
            return {"projects": project_svc.list_(conn, user_id=user["id"], include_archived=include_archived)}

    @app.post("/api/projects", status_code=201)
    def create_project(
        body: ProjectSaveRequest, user: dict = Depends(auth.current_user)
    ) -> dict[str, Any]:
        try:
            with db.connect() as conn:
                proj = project_svc.create(
                    conn, user_id=user["id"], code=body.code, name=body.name,
                    description=body.description, code_repo=body.code_repo, repo_kind=body.repo_kind,
                    dockerfile=body.dockerfile,
                )
        except project_svc.ProjectOpError as exc:
            raise _project_http(exc) from exc
        # Build the project sandbox image in the background ; result toasts via WS.
        project_build.trigger_image_build(proj, user["id"])
        return {"project": proj}

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: int, user: dict = Depends(auth.current_user)) -> dict[str, Any]:
        try:
            with db.connect() as conn:
                return {"project": project_svc.get_owned(conn, user_id=user["id"], project_id=project_id)}
        except project_svc.ProjectOpError as exc:
            raise _project_http(exc) from exc

    @app.patch("/api/projects/{project_id}")
    def update_project(
        project_id: int, body: ProjectUpdateRequest, user: dict = Depends(auth.current_user)
    ) -> dict[str, Any]:
        try:
            with db.connect() as conn:
                proj = project_svc.update(
                    conn, user_id=user["id"], project_id=project_id,
                    name=body.name, description=body.description, status=body.status,
                    code_repo=body.code_repo, repo_kind=body.repo_kind, dockerfile=body.dockerfile,
                )
        except project_svc.ProjectOpError as exc:
            raise _project_http(exc) from exc
        # If the Dockerfile was (re)set, rebuild the sandbox image in the background.
        if body.dockerfile is not None:
            project_build.trigger_image_build(proj, user["id"])
        return {"project": proj}

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: int, user: dict = Depends(auth.current_user)) -> dict[str, Any]:
        try:
            with db.connect() as conn:
                deleted = project_svc.delete(conn, user_id=user["id"], project_id=project_id)
        except project_svc.ProjectOpError as exc:
            raise _project_http(exc) from exc
        return {"deleted_id": deleted}

    # ---- long-term memory (scope-aware ; auth-gated) ---------------------
    #
    # Same service.memory functions as the manage_memory tool (single validation
    # + SQL source). Target resolution : user-scope pins to the caller ; project
    # / tool scopes take an explicit key (query param or body).
    def _memory_http(exc: memory_svc.MemoryOpError) -> HTTPException:
        status = {"already_exists": 409, "not_found": 404, "no_project": 409}.get(exc.code, 400)
        return HTTPException(status_code=status, detail=exc.message)

    def _mem_target(
        scope: str, user_id: int, project_id: int | None, tool_code: str | None
    ) -> dict[str, Any]:
        if scope == "world":
            return {}
        if scope == "user":
            return {"user_id": user_id}
        if scope == "project":
            if project_id is None:
                raise HTTPException(status_code=400, detail="project_id required for scope=project")
            return {"project_id": project_id}
        if scope == "tool":
            if not tool_code:
                raise HTTPException(status_code=400, detail="tool_code required for scope=tool")
            return {"tool_code": tool_code}
        raise HTTPException(status_code=400, detail=f"invalid scope '{scope}'")

    @app.get("/api/memory")
    def list_memory(
        scope: str | None = None,
        project_id: int | None = None,
        tool_code: str | None = None,
        user: dict = Depends(auth.current_user),
    ) -> dict[str, Any]:
        try:
            with db.connect() as conn:
                if scope is None:
                    # Default browse : the caller's own facts.
                    entries = memory_svc.list_(conn, scope="user", user_id=user["id"])
                else:
                    target = _mem_target(scope, user["id"], project_id, tool_code)
                    entries = memory_svc.list_(conn, scope=scope, **target)
        except memory_svc.MemoryOpError as exc:
            raise _memory_http(exc) from exc
        return {"entries": entries}

    @app.get("/api/memory/search")
    def search_memory(
        q: str,
        scope: str | None = None,
        project_id: int | None = None,
        tool_code: str | None = None,
        limit: int = memory_svc.DEFAULT_SEARCH_LIMIT,
        user: dict = Depends(auth.current_user),
    ) -> dict[str, Any]:
        try:
            with db.connect() as conn:
                target = (
                    _mem_target(scope, user["id"], project_id, tool_code) if scope else {}
                )
                if scope is None:
                    target = {"user_id": user["id"]}
                    scope = "user"
                results = memory_svc.search(conn, query=q, scope=scope, limit=limit, **target)
        except memory_svc.MemoryOpError as exc:
            raise _memory_http(exc) from exc
        return {"results": results}

    @app.get("/api/memory/{scope}/{code}")
    def recall_memory(
        scope: str,
        code: str,
        project_id: int | None = None,
        tool_code: str | None = None,
        user: dict = Depends(auth.current_user),
    ) -> dict[str, Any]:
        try:
            with db.connect() as conn:
                target = _mem_target(scope, user["id"], project_id, tool_code)
                row = memory_svc.recall(conn, scope=scope, code=code, **target)
        except memory_svc.MemoryOpError as exc:
            raise _memory_http(exc) from exc
        if row is None:
            raise HTTPException(status_code=404, detail=f"no {scope}/{code} entry")
        return {"entry": row}

    @app.post("/api/memory", status_code=201)
    def save_memory(
        body: MemorySaveRequest, user: dict = Depends(auth.current_user)
    ) -> dict[str, Any]:
        try:
            with db.connect() as conn:
                target = _mem_target(body.scope, user["id"], body.project_id, body.tool_code)
                saved = memory_svc.save(
                    conn,
                    scope=body.scope,
                    code=body.code,
                    title=body.title,
                    description=body.description,
                    content=body.content,
                    **target,
                )
        except memory_svc.MemoryOpError as exc:
            raise _memory_http(exc) from exc
        return {"saved": saved}

    @app.patch("/api/memory/{scope}/{code}")
    def update_memory(
        scope: str,
        code: str,
        body: MemoryUpdateRequest,
        user: dict = Depends(auth.current_user),
    ) -> dict[str, Any]:
        try:
            with db.connect() as conn:
                target = _mem_target(scope, user["id"], body.project_id, body.tool_code)
                target_id = memory_svc.update(
                    conn,
                    scope=scope,
                    code=code,
                    title=body.title,
                    description=body.description,
                    content=body.content,
                    **target,
                )
        except memory_svc.MemoryOpError as exc:
            raise _memory_http(exc) from exc
        return {"updated_id": target_id}

    @app.delete("/api/memory/{scope}/{code}")
    def delete_memory(
        scope: str,
        code: str,
        project_id: int | None = None,
        tool_code: str | None = None,
        user: dict = Depends(auth.current_user),
    ) -> dict[str, Any]:
        try:
            with db.connect() as conn:
                target = _mem_target(scope, user["id"], project_id, tool_code)
                target_id = memory_svc.delete(conn, scope=scope, code=code, **target)
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
                # PLAN mode is only meaningful where execution happens (code) or
                # multi-step delegation (analyse) ; ignored in chat/vocal.
                plan_mode = bool(data.get("plan_mode")) and mode in ("code", "analyse")
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
                        plan_mode=plan_mode,
                    )
        except WebSocketDisconnect:
            return

    # ---- notifications WebSocket (per-user push : build results, …) -------
    @app.websocket("/ws/notifications")
    async def ws_notifications(websocket: WebSocket, token: str = "") -> None:
        user = auth.verify_token(token)
        if user is None:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        notifications.register(user["id"], websocket)
        try:
            while True:
                await websocket.receive_text()  # keepalive ; we only push here
        except WebSocketDisconnect:
            pass
        finally:
            notifications.unregister(user["id"], websocket)

    return app


def run() -> None:
    """Entrypoint for ``jean-michel-serve`` / ``./jm.sh --serve``."""
    import uvicorn

    host = os.environ.get("JEANMICHEL_API_HOST", DEFAULT_HOST)
    port = int(os.environ.get("JEANMICHEL_API_PORT", DEFAULT_PORT))
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    run()
