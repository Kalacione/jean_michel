"""Tool: clock — returns current UTC time and local time for a given timezone."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ._base import ToolSpec
from ._errors import tool_error, tool_ok


def _handler(timezone: str = "UTC") -> str:
    try:
        tz = ZoneInfo(timezone)
    except Exception:
        return tool_error("unknown_timezone", f"Unknown timezone: {timezone}")
    now_utc = datetime.now(UTC)
    now_local = now_utc.astimezone(tz)
    utc_str = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    local_str = now_local.isoformat()
    return tool_ok(
        f"{timezone}: {local_str} (UTC {utc_str})",
        utc=utc_str,
        local=local_str,
        timezone=timezone,
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
