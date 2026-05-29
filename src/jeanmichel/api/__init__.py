"""Web daemon for Jean-Michel (FastAPI + WebSocket).

Run with ``./jm.sh --serve`` (or ``jean-michel-serve``). Reuses the service
layer (``jeanmichel.service``) — no orchestration logic is reimplemented here.
The web dependencies are optional : install with ``pip install -e ".[web]"``.
"""
