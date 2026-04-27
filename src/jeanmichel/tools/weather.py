"""Tool: weather — current conditions, forecast, and past weather via open-meteo.

Geocoding is handled by the open-meteo Geocoding API (no extra dependency).
If the location is already in 'lat,lon' decimal form it is used directly.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

from ._base import ToolSpec

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

def _handler(
    location: str,
    mode: str = "current",
    forecast_days: int = 1,
    past_days: int = 1,
    timezone: str = "auto",
) -> str:
    # --- Resolve coordinates ------------------------------------------------
    m = _LAT_LON_RE.match(location.strip())
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        display_name = location.strip()
    else:
        try:
            lat, lon, display_name = _geocode_openmeteo(location.strip())
        except ValueError as e:
            return json.dumps({"error": str(e)})
        except Exception as e:
            return json.dumps({"error": f"Geocoding failed: {e}"})

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
        return json.dumps(
            {"error": f"Unknown mode: {mode!r}. Use 'current', 'forecast' or 'history'."}
        )

    # --- Fetch weather ------------------------------------------------------
    try:
        url = _BASE_URL + "?" + urllib.parse.urlencode(params)
        raw = _http_get_json(url)
    except Exception as e:
        return json.dumps({"error": f"open-meteo request failed: {e}"})

    # --- Assemble result ----------------------------------------------------
    result: dict = {
        "location": {"name": display_name, "lat": lat, "lon": lon},
        "timezone": raw.get("timezone"),
        "utc_offset_seconds": raw.get("utc_offset_seconds"),
        "mode": mode,
    }
    for key in ("current", "current_units", "hourly", "hourly_units", "daily", "daily_units"):
        if key in raw:
            result[key] = raw[key]
    result["wmo_descriptions"] = _extract_wmo_descriptions(raw)

    serialized = json.dumps(result)
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
        },
        "required": ["location"],
    },
    handler=_handler,
)
