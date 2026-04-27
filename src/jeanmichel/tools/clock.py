"""Tool: clock — returns current UTC time and local time for a given timezone."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ._base import ToolSpec


def _handler(timezone: str = "UTC") -> str:
    try:
        tz = ZoneInfo(timezone)
    except Exception:
        return f'{{"error": "Unknown timezone: {timezone}"}}'
    now_utc = datetime.now(UTC)
    now_local = now_utc.astimezone(tz)
    return (
        f'{{"utc": "{now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")}", '
        f'"local": "{now_local.isoformat()}", '
        f'"timezone": "{timezone}"}}'
    )


SPEC = ToolSpec(
    name="clock",
    description="Return current UTC time and the time in the given IANA timezone.",
    parameters={
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "IANA timezone name, e.g. 'America/Montreal'. Defaults to UTC.",
            },
        },
        "required": [],
    },
    handler=_handler,
)
