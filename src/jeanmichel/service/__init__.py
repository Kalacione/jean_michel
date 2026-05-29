"""Transport-agnostic service layer.

Logic shared between the CLI and the web daemon. Neither rendering (Rich,
prompt_toolkit) nor transport (WebSocket, HTTP) lives here — the orchestrator
turn, conversation lifecycle and user-memory CRUD do.
"""
