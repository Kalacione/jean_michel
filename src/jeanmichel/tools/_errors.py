"""Shared error helpers for workspace tools."""

from __future__ import annotations

import json

CRITICAL_ERROR_CODES = frozenset({
    "path_escape",
    "quota_exceeded",
    "file_not_found",
    "absolute_path",
})


def tool_error(code: str, message: str, **extra) -> str:
    payload: dict = {"error": message, "error_code": code}
    payload.update(extra)
    return json.dumps(payload)
