"""Shared response helpers for tools.

All tool handlers should return JSON produced by `tool_ok` or `tool_error`.
Both helpers guarantee a `summary` field — a short, human-readable, single-line
description of the outcome. The orchestrator's plan writer reads ONLY this
field, so individual tools own their own one-line story and we don't need
per-tool special cases anywhere downstream.
"""

from __future__ import annotations

import json

CRITICAL_ERROR_CODES = frozenset({
    "path_escape",
    "quota_exceeded",
    "file_not_found",
    "absolute_path",
})


def _truncate(s: str, n: int = 160) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def tool_ok(summary: str, **fields) -> str:
    """Standard success response. `summary` is the one-line plan entry."""
    payload: dict = {"summary": _truncate(summary, 200)}
    payload.update(fields)
    return json.dumps(payload)


def tool_error(code: str, message: str, **extra) -> str:
    payload: dict = {
        "error": message,
        "error_code": code,
        "summary": _truncate(f"error[{code}]: {message}", 200),
    }
    payload.update(extra)
    return json.dumps(payload)
