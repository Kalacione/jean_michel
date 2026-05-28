"""Tool: clock — returns current UTC time and the local time at a place.

Accepts either an explicit IANA `timezone` (e.g. 'America/Montreal') or a
free-form `location` string (e.g. 'Paris, France') that is resolved to a
timezone via the open-meteo geocoding API. If both are provided, `location`
wins (it's the more user-friendly input). With neither, the tool returns
UTC.

The dispatcher in ``execute_alexa`` pre-fills `location` from the
``user_profile.toml`` (city + country) when the LLM emits ``clock`` with
empty args — so a bare "quelle heure est-il ?" always returns the user's
local time, never UTC by surprise.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ._base import ToolSpec
from ._errors import tool_error, tool_ok

_log = logging.getLogger(__name__)

_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"


def _resolve_timezone_from_location(location: str) -> tuple[str, str]:
    """Resolve a place name to (IANA timezone, display name) via open-meteo.

    Returns the timezone string (e.g. 'Europe/Paris') and a human-readable
    label combining the resolved place and country. Raises ``ValueError``
    when the API returns no result or no timezone.
    """
    params = urllib.parse.urlencode({
        "name": location, "count": 1, "language": "en", "format": "json",
    })
    url = f"{_GEO_URL}?{params}"
    with urllib.request.urlopen(url, timeout=8) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    results = data.get("results")
    if not results:
        raise ValueError(f"Location not found: {location!r}")
    r = results[0]
    tz = r.get("timezone")
    if not tz:
        raise ValueError(f"No timezone in geocoding result for {location!r}")
    name = r.get("name", location)
    country = r.get("country", "")
    display = f"{name}, {country}" if country else name
    return tz, display


def _handler(
    timezone: str | None = None,
    location: str | None = None,
) -> str:
    """Return current time.

    Args :
        timezone : IANA timezone name (e.g. 'America/Montreal').
        location : place name (e.g. 'Paris', 'Paris, France'). Resolved
            to a timezone via the open-meteo geocoding API.

    If both are provided, `location` wins. If neither, returns UTC.
    """
    resolved_tz = "UTC"
    resolved_label = "UTC"

    if location and location.strip():
        try:
            resolved_tz, resolved_label = _resolve_timezone_from_location(
                location.strip()
            )
        except Exception as exc:  # noqa: BLE001
            _log.info("clock: geocoding failed for %r: %s", location, exc)
            return tool_error(
                "geocoding_failed",
                f"Could not resolve location {location!r}: {exc}",
            )
    elif timezone and timezone.strip():
        resolved_tz = timezone.strip()
        resolved_label = resolved_tz

    try:
        tz = ZoneInfo(resolved_tz)
    except Exception:  # noqa: BLE001
        return tool_error("unknown_timezone", f"Unknown timezone: {resolved_tz}")

    now_utc = datetime.now(UTC)
    now_local = now_utc.astimezone(tz)
    utc_str = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    local_str = now_local.isoformat()
    return tool_ok(
        f"{resolved_label}: {local_str} (UTC {utc_str})",
        utc=utc_str,
        local=local_str,
        timezone=resolved_tz,
    )


SPEC = ToolSpec(
    name="clock",
    description=(
        "Return current UTC time and the local time at a place. "
        "Accepts either an explicit IANA `timezone` or a free-form "
        "`location` (city / country) — if both are given, `location` wins. "
        "With neither, returns UTC."
    ),
    parameters={
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": (
                    "IANA timezone name, e.g. 'America/Montreal'. "
                    "Optional. Use this only when you have a known IANA name."
                ),
            },
            "location": {
                "type": "string",
                "description": (
                    "Place name, e.g. 'Paris', 'Paris, France', 'Tokyo'. "
                    "Resolved to a timezone via geocoding. Preferred over "
                    "`timezone` when the user names a place."
                ),
            },
        },
        "required": [],
    },
    handler=_handler,
)
