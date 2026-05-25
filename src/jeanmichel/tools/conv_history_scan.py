"""Tool: conv_history_scan — analyse historique des conversations passées.

Lit les summary.md (ou conversation.md) des N conversations les plus récentes.
Conçu pour que meta-analyst puisse détecter des patterns, des échecs récurrents
et formuler des propositions d'amélioration.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .. import config
from ._base import ToolSpec
from ._errors import tool_ok


def _handler(limit: int = 10, status: str = "all") -> str:
    limit = max(1, min(50, int(limit)))

    status_clause = ""
    params: list = [limit]
    if status in ("completed", "failed"):
        status_clause = "WHERE status = ?"
        params = [status, limit]

    db_path = config.DB_PATH

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT id, folder_path, title, mode, created_at, status "
            f"FROM conversations {status_clause} "
            f"ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()

    results = []
    for r in rows:
        folder = Path(r["folder_path"])
        summary_path = folder / "summary.md"
        journal_path = folder / "conversation.md"

        content_text: str | None = None
        content_source: str | None = None
        if summary_path.exists():
            content_text = summary_path.read_text(encoding="utf-8")[:3000]
            content_source = "summary.md"
        elif journal_path.exists():
            content_text = journal_path.read_text(encoding="utf-8")[:1500]
            content_source = "conversation.md"

        results.append({
            "conversation_id": r["id"][:12],
            "folder": folder.name,
            "created_at": r["created_at"],
            "mode": r["mode"],
            "status": r["status"],
            "title": r["title"],
            "content_source": content_source,
            "content": content_text,
        })

    return tool_ok(
        f"{len(results)} conversations scanned",
        conversations=results,
        count=len(results),
    )


SPEC = ToolSpec(
    name="conv_history_scan",
    description=(
        "Scan recent conversation summaries for pattern analysis. "
        "Returns the content of summary.md (or conversation.md) for the N most "
        "recent conversations, ordered newest first. "
        "Use this to identify recurring failures, user patterns, under-used agents, "
        "and improvement opportunities. Output is a document — not an inline response."
    ),
    parameters={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Number of conversations to return (default 10, max 50).",
            },
            "status": {
                "type": "string",
                "enum": ["all", "completed", "failed"],
                "description": "Filter by status (default 'all').",
            },
        },
        "required": [],
    },
    handler=_handler,
)
