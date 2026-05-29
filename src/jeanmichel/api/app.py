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
    from fastapi import Depends, FastAPI, HTTPException
    from pydantic import BaseModel

    from .. import db
    from ..service import conversation as conversation_svc
    from . import auth

    app = FastAPI(title="Jean-Michel API", version="0.1.0")

    class LoginRequest(BaseModel):
        username: str
        password: str

    class CreateConversationRequest(BaseModel):
        mode: str = "analyse"

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
    def get_conversation(
        conversation_id: str = Depends(auth.require_conversation_owner),
    ) -> dict[str, Any]:
        with db.connect() as conn:
            row = db.get_conversation(conn, conversation_id)
        # require_conversation_owner already 404s on unknown, so row is set.
        return {
            "id": row["id"],
            "mode": row["mode"],
            "status": row["status"],
            "user_language": row["user_language"],
        }

    return app


def run() -> None:
    """Entrypoint for ``jean-michel-serve`` / ``./jm.sh --serve``."""
    import uvicorn

    host = os.environ.get("JEANMICHEL_API_HOST", DEFAULT_HOST)
    port = int(os.environ.get("JEANMICHEL_API_PORT", DEFAULT_PORT))
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    run()
