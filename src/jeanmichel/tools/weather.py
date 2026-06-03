"""Tool: weather — current conditions, forecast, and past weather via open-meteo.

Geocoding is handled by the open-meteo Geocoding API (no extra dependency).
If the location is already in 'lat,lon' decimal form it is used directly.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta

from ._base import ToolSpec
from ._errors import tool_error

# ---------------------------------------------------------------------------
# WMO weather interpretation codes (WW)
# ---------------------------------------------------------------------------

_WMO_CODES: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog",
    51: "Drizzle: light", 53: "Drizzle: moderate", 55: "Drizzle: dense",
    56: "Freezing drizzle: light", 57: "Freezing drizzle: dense",
    61: "Rain: slight", 63: "Rain: moderate", 65: "Rain: heavy",
    66: "Freezing rain: light", 67: "Freezing rain: heavy",
    71: "Snowfall: slight", 73: "Snowfall: moderate", 75: "Snowfall: heavy",
    77: "Snow grains",
    80: "Rain showers: slight", 81: "Rain showers: moderate", 82: "Rain showers: violent",
    85: "Snow showers: slight", 86: "Snow showers: heavy",
    95: "Thunderstorm: slight or moderate",
    96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}

_BASE_URL = "https://api.open-meteo.com/v1/forecast"
_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
_LAT_LON_RE = re.compile(r"^(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)$")
_MAX_RESPONSE_CHARS = 8_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _geocode_openmeteo(name: str) -> tuple[float, float, str]:
    """Resolve a place name to (lat, lon, display_name) via open-meteo geocoding."""
    params = urllib.parse.urlencode({"name": name, "count": 1, "language": "en", "format": "json"})
    url = f"{_GEO_URL}?{params}"
    with urllib.request.urlopen(url, timeout=8) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    results = data.get("results")
    if not results:
        raise ValueError(f"Location not found: {name!r}")
    r = results[0]
    display = r.get("name", name)
    country = r.get("country", "")
    if country:
        display = f"{display}, {country}"
    return float(r["latitude"]), float(r["longitude"]), display


def _http_get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _extract_wmo_descriptions(data: dict) -> dict[str, str]:
    codes: set[int] = set()
    for section in ("current", "hourly", "daily"):
        obj = data.get(section, {})
        wc = obj.get("weather_code")
        if isinstance(wc, list):
            codes.update(int(c) for c in wc if c is not None)
        elif wc is not None:
            codes.add(int(wc))
    return {str(c): _WMO_CODES.get(c, "Unknown WMO code") for c in sorted(codes)}


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def _focus_day(result: dict, target_iso: str, display_name: str) -> str:
    """Trim result['daily'] to the requested day (match by date ; fall back to the
    nearest available day), set result['requested_date'], return a focused summary."""
    daily = result.get("daily") or {}
    times = daily.get("time") or []
    if not times:
        return f"{display_name}: forecast for {target_iso}"
    if target_iso in times:
        idx = times.index(target_iso)
    else:  # tz off-by-one or out of window → nearest available day
        idx = min(
            range(len(times)),
            key=lambda i: abs((date.fromisoformat(times[i]) - date.fromisoformat(target_iso)).days),
        )
    result["daily"] = {
        k: ([v[idx]] if isinstance(v, list) and idx < len(v) else v)
        for k, v in daily.items()
    }
    day_iso = times[idx]
    result["requested_date"] = day_iso
    one = {k: (v[0] if isinstance(v, list) and v else None) for k, v in result["daily"].items()}
    wc = one.get("weather_code")
    cond = _WMO_CODES.get(int(wc), "") if wc is not None else ""
    tmin, tmax = one.get("temperature_2m_min"), one.get("temperature_2m_max")
    bits = [f"{date.fromisoformat(day_iso).strftime('%A')} {day_iso}"]
    if tmin is not None and tmax is not None:
        bits.append(f"{tmin}–{tmax}°C")
    if cond:
        bits.append(cond)
    return f"{display_name}: " + ", ".join(bits)


def _handler(
    location: str,
    mode: str = "current",
    forecast_days: int = 1,
    past_days: int = 1,
    timezone: str = "auto",
    when: str | None = None,
) -> str:
    # --- Resolve a relative day word ("tomorrow", "thursday", "in 3 days") --
    # English only (internal mechanics are English) → a forecast day to focus.
    target_date: str | None = None
    if when:
        from ._temporal import resolve_when
        offset = resolve_when(when)
        if offset is not None and 1 <= offset <= 15:
            mode = "forecast"
            forecast_days = max(forecast_days, offset + 1)
            target_date = (date.today() + timedelta(days=offset)).isoformat()
        # offset 0 (today) / out of range / unparseable → leave mode as current

    # --- Resolve coordinates ------------------------------------------------
    m = _LAT_LON_RE.match(location.strip())
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        display_name = location.strip()
    else:
        try:
            lat, lon, display_name = _geocode_openmeteo(location.strip())
        except ValueError as e:
            return tool_error("location_not_found", str(e))
        except Exception as e:
            return tool_error("geocoding_failed", f"Geocoding failed: {e}")

    # --- Build API params ---------------------------------------------------
    params: dict[str, object] = {
        "latitude": lat,
        "longitude": lon,
        "timezone": timezone,
    }

    if mode == "current":
        params["current"] = ",".join([
            "temperature_2m", "relative_humidity_2m", "apparent_temperature",
            "precipitation", "weather_code", "cloud_cover",
            "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "is_day",
        ])
        params["hourly"] = "temperature_2m,precipitation_probability,weather_code"
        params["forecast_hours"] = 12
        params["past_days"] = 0

    elif mode == "forecast":
        params["daily"] = ",".join([
            "weather_code", "temperature_2m_max", "temperature_2m_min",
            "apparent_temperature_max", "apparent_temperature_min",
            "precipitation_sum", "precipitation_probability_max",
            "wind_speed_10m_max", "wind_gusts_10m_max", "sunrise", "sunset",
        ])
        params["forecast_days"] = max(1, min(int(forecast_days), 16))

    elif mode == "history":
        params["daily"] = ",".join([
            "weather_code", "temperature_2m_max", "temperature_2m_min",
            "precipitation_sum", "wind_speed_10m_max",
        ])
        params["past_days"] = max(1, min(int(past_days), 92))
        params["forecast_days"] = 0

    else:
        return tool_error(
            "unknown_mode",
            f"Unknown mode: {mode!r}. Use 'current', 'forecast' or 'history'.",
        )

    # --- Fetch weather ------------------------------------------------------
    try:
        url = _BASE_URL + "?" + urllib.parse.urlencode(params)
        raw = _http_get_json(url)
    except Exception as e:
        return tool_error("open_meteo_failed", f"open-meteo request failed: {e}")

    # --- Assemble result ----------------------------------------------------
    utc_offset_seconds = raw.get("utc_offset_seconds", 0)
    local_now = datetime.now(UTC) + timedelta(seconds=utc_offset_seconds)
    result: dict = {
        "location": {"name": display_name, "lat": lat, "lon": lon},
        "timezone": raw.get("timezone"),
        "utc_offset_seconds": utc_offset_seconds,
        "local_date": local_now.strftime("%Y-%m-%d"),
        "mode": mode,
    }
    for key in ("current", "current_units", "hourly", "hourly_units", "daily", "daily_units"):
        if key in raw:
            result[key] = raw[key]
    result["wmo_descriptions"] = _extract_wmo_descriptions(raw)

    # Build a short human summary for the plan log.
    if target_date and isinstance(result.get("daily"), dict):
        # `when` requested a specific day → trim the forecast to it.
        summary = _focus_day(result, target_date, display_name)
    elif mode == "current" and "current" in raw:
        cur = raw["current"]
        temp = cur.get("temperature_2m")
        wc = cur.get("weather_code")
        cond = _WMO_CODES.get(int(wc), "") if wc is not None else ""
        summary = f"{display_name}: {temp}°C {cond}".strip()
    elif mode == "forecast":
        days = len(raw.get("daily", {}).get("time", []))
        summary = f"{display_name}: {days}-day forecast"
    elif mode == "history":
        days = len(raw.get("daily", {}).get("time", []))
        summary = f"{display_name}: {days}-day history"
    else:
        summary = f"{display_name}: {mode}"

    serialized = json.dumps({"summary": summary, **result})
    if len(serialized) > _MAX_RESPONSE_CHARS:
        serialized = serialized[:_MAX_RESPONSE_CHARS] + '"}'
    return serialized


# ---------------------------------------------------------------------------
# ToolSpec
# ---------------------------------------------------------------------------

SPEC = ToolSpec(
    name="weather",
    description=(
        "Fetch weather data for a location using the open-meteo API. "
        "Three modes: "
        "'current' returns present conditions plus a 12-hour hourly preview; "
        "'forecast' returns daily aggregates for up to 16 days ahead; "
        "'history' returns daily aggregates for up to 92 past days. "
        "location can be a city name ('Montreal', 'Paris, France') or decimal "
        "coordinates ('45.51,-73.59'). "
        "Returns JSON with measured values, units, and WMO code descriptions."
    ),
    parameters={
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": (
                    "City name or 'lat,lon' decimal string. "
                    "Examples: 'Montreal', 'Paris, France', '48.85,2.35'."
                ),
            },
            "mode": {
                "type": "string",
                "enum": ["current", "forecast", "history"],
                "description": (
                    "'current': conditions now + 12h hourly preview (default). "
                    "'forecast': daily summary for N days ahead. "
                    "'history': daily summary for N past days."
                ),
            },
            "forecast_days": {
                "type": "integer",
                "description": "Days ahead to forecast (1-16). Only used with mode='forecast'.",
            },
            "past_days": {
                "type": "integer",
                "description": (
                    "Days in the past to retrieve (1-92). Only used with mode='history'."
                ),
            },
            "timezone": {
                "type": "string",
                "description": (
                    "IANA timezone name (e.g. 'America/Montreal'). "
                    "Default 'auto' resolves from coordinates."
                ),
            },
            "when": {
                "type": "string",
                "description": (
                    "An ENGLISH relative day phrase for a single forecast day: "
                    "'tomorrow', 'tonight', 'thursday', 'next monday', 'this weekend', "
                    "'in 3 days', 'june 10'. Resolved to a date internally (no date "
                    "math needed by the caller). Omit for current conditions. "
                    "Express it in English even if the user spoke another language."
                ),
            },
        },
        "required": ["location"],
    },
    handler=_handler,
)
