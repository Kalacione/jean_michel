"""FastAPI application factory + entrypoint (S0 skeleton).

Only a health check for now. Endpoints (auth, conversations, memory, the turn
WebSocket) land in the following sprints, each delegating to
``jeanmichel.service``. FastAPI/uvicorn are imported lazily so importing this
module stays cheap and the web extras stay optional.
"""

from __future__ import annotations

from typing import Any

# Daemon binds the LAN by default (auth is the gate — cf. audit). Override via
# JEANMICHEL_API_HOST / JEANMICHEL_API_PORT.
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000


def create_app() -> Any:
    """Build the FastAPI app. Imports FastAPI lazily (optional dependency)."""
    from fastapi import FastAPI

    app = FastAPI(title="Jean-Michel API", version="0.1.0")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def run() -> None:
    """Entrypoint for ``jean-michel-serve`` / ``./jm.sh --serve``."""
    import os

    import uvicorn

    host = os.environ.get("JEANMICHEL_API_HOST", DEFAULT_HOST)
    port = int(os.environ.get("JEANMICHEL_API_PORT", DEFAULT_PORT))
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    run()
