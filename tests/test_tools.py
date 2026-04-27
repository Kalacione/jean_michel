"""Unit tests for src/jeanmichel/tools/."""

from __future__ import annotations

import json
from unittest.mock import patch

from jeanmichel.tools.clock import SPEC as CLOCK_SPEC
from jeanmichel.tools.conv_read_file import make_spec
from jeanmichel.tools.weather import SPEC as WEATHER_SPEC


class TestClock:
    def test_returns_utc_and_local_keys(self):
        result = json.loads(CLOCK_SPEC.handler())
        assert "utc" in result
        assert "local" in result
        assert result["timezone"] == "UTC"

    def test_valid_timezone(self):
        result = json.loads(CLOCK_SPEC.handler(timezone="America/Montreal"))
        assert result["timezone"] == "America/Montreal"

    def test_invalid_timezone_returns_error(self):
        result = json.loads(CLOCK_SPEC.handler(timezone="Fake/Zone"))
        assert "error" in result


class TestConvReadFile:
    def test_reads_file(self, tmp_path):
        conv = tmp_path / "conv"
        conv.mkdir()
        (conv / "note.txt").write_text("hello world")
        spec = make_spec(conv)
        assert spec.handler("note.txt") == "hello world"

    def test_file_not_found(self, tmp_path):
        conv = tmp_path / "conv"
        conv.mkdir()
        result = json.loads(make_spec(conv).handler("missing.txt"))
        assert "error" in result

    def test_path_traversal_blocked(self, tmp_path):
        conv = tmp_path / "conv"
        conv.mkdir()
        result = json.loads(make_spec(conv).handler("../../etc/passwd"))
        assert "error" in result

    def test_max_bytes_truncates(self, tmp_path):
        conv = tmp_path / "conv"
        conv.mkdir()
        (conv / "big.txt").write_bytes(b"x" * 200)
        result = make_spec(conv).handler("big.txt", max_bytes=10)
        assert len(result) == 10

    def test_non_utf8_returns_error(self, tmp_path):
        conv = tmp_path / "conv"
        conv.mkdir()
        (conv / "bin.dat").write_bytes(b"\xff\xfe")
        result = json.loads(make_spec(conv).handler("bin.dat"))
        assert "error" in result


# ---------------------------------------------------------------------------
# Fake open-meteo responses used across WeatherTool tests
# ---------------------------------------------------------------------------

_FAKE_GEO = {"results": [{"latitude": 45.5088, "longitude": -73.5878,
                           "name": "Montréal", "country": "Canada"}]}

_FAKE_CURRENT = {
    "latitude": 45.5,
    "longitude": -73.5,
    "timezone": "America/Toronto",
    "utc_offset_seconds": -14400,
    "current": {
        "time": "2026-04-27T15:00",
        "temperature_2m": 12.4,
        "relative_humidity_2m": 60,
        "apparent_temperature": 9.1,
        "precipitation": 0.0,
        "weather_code": 3,
        "cloud_cover": 80,
        "wind_speed_10m": 18.2,
        "wind_direction_10m": 270,
        "wind_gusts_10m": 28.0,
        "is_day": 1,
    },
    "current_units": {"temperature_2m": "°C", "wind_speed_10m": "km/h"},
    "hourly": {
        "time": ["2026-04-27T00:00", "2026-04-27T01:00"],
        "temperature_2m": [10.0, 9.5],
        "precipitation_probability": [5, 10],
        "weather_code": [2, 3],
    },
    "hourly_units": {"temperature_2m": "°C"},
}

_FAKE_FORECAST = {
    "latitude": 45.5,
    "longitude": -73.5,
    "timezone": "America/Toronto",
    "utc_offset_seconds": -14400,
    "daily": {
        "time": ["2026-04-27"],
        "weather_code": [61],
        "temperature_2m_max": [14.0],
        "temperature_2m_min": [6.0],
        "precipitation_sum": [3.2],
    },
    "daily_units": {"temperature_2m_max": "°C", "precipitation_sum": "mm"},
}


def _make_http_mock(geo_data, weather_data):
    """Return (fake_http, calls) — calls accumulates every URL passed to the mock."""
    calls: list[str] = []

    def fake_http(url):
        calls.append(url)
        if "geocoding-api" in url:
            return geo_data
        return weather_data

    return fake_http, calls


class TestWeather:
    def test_unknown_mode_returns_error(self):
        result = json.loads(WEATHER_SPEC.handler(location="Montreal", mode="bogus"))
        assert "error" in result

    def test_lat_lon_skips_geocoding(self):
        mock_http, calls = _make_http_mock({}, _FAKE_CURRENT)
        with patch("jeanmichel.tools.weather._http_get_json", side_effect=mock_http):
            result = json.loads(WEATHER_SPEC.handler(location="45.5,-73.5"))
        # geocoding endpoint must NOT have been called
        assert all("geocoding-api" not in u for u in calls)
        assert result["location"]["lat"] == 45.5
        assert result["location"]["lon"] == -73.5

    def test_current_mode_structure(self):
        mock_http, _ = _make_http_mock(_FAKE_GEO, _FAKE_CURRENT)
        with patch("jeanmichel.tools.weather._http_get_json", side_effect=mock_http):
            result = json.loads(WEATHER_SPEC.handler(location="Montreal"))
        assert result["mode"] == "current"
        assert "current" in result
        assert result["current"]["temperature_2m"] == 12.4
        assert "hourly" in result

    def test_wmo_descriptions_populated(self):
        mock_http, _ = _make_http_mock(_FAKE_GEO, _FAKE_CURRENT)
        with patch("jeanmichel.tools.weather._http_get_json", side_effect=mock_http):
            result = json.loads(WEATHER_SPEC.handler(location="Montreal"))
        assert "wmo_descriptions" in result
        assert "3" in result["wmo_descriptions"]
        assert result["wmo_descriptions"]["3"] == "Overcast"

    def test_forecast_mode_uses_daily(self):
        mock_http, calls = _make_http_mock(_FAKE_GEO, _FAKE_FORECAST)
        with patch("jeanmichel.tools.weather._http_get_json", side_effect=mock_http):
            result = json.loads(
                WEATHER_SPEC.handler(location="Montreal", mode="forecast", forecast_days=3)
            )
        assert result["mode"] == "forecast"
        assert "daily" in result
        weather_url = next(u for u in calls if "geocoding-api" not in u)
        assert "forecast_days=3" in weather_url

    def test_history_mode_uses_past_days(self):
        mock_http, calls = _make_http_mock(_FAKE_GEO, _FAKE_FORECAST)
        with patch("jeanmichel.tools.weather._http_get_json", side_effect=mock_http):
            result = json.loads(
                WEATHER_SPEC.handler(location="Montreal", mode="history", past_days=7)
            )
        assert result["mode"] == "history"
        weather_url = next(u for u in calls if "geocoding-api" not in u)
        assert "past_days=7" in weather_url
        assert "forecast_days=0" in weather_url

    def test_location_not_found_returns_error(self):
        with patch("jeanmichel.tools.weather._http_get_json") as mock_http:
            mock_http.return_value = {"results": []}  # geocoding returns nothing
            result = json.loads(WEATHER_SPEC.handler(location="NonExistentXYZ"))
        assert "error" in result

    def test_geocoding_failure_returns_error(self):
        exc = Exception("network error")
        with patch("jeanmichel.tools.weather._http_get_json", side_effect=exc):
            result = json.loads(WEATHER_SPEC.handler(location="Paris"))
        assert "error" in result

    def test_weather_api_failure_returns_error(self):
        def fail_on_weather(url):
            if "geocoding-api" in url:
                return _FAKE_GEO
            raise OSError("connection refused")

        with patch("jeanmichel.tools.weather._http_get_json", side_effect=fail_on_weather):
            result = json.loads(WEATHER_SPEC.handler(location="Paris"))
        assert "error" in result

    def test_response_truncated_when_too_large(self):
        huge_data = dict(_FAKE_CURRENT)
        huge_data["hourly"] = {"time": ["t"] * 500, "temperature_2m": [20.0] * 500}
        mock_http, _ = _make_http_mock(_FAKE_GEO, huge_data)
        with patch("jeanmichel.tools.weather._http_get_json", side_effect=mock_http):
            raw = WEATHER_SPEC.handler(location="Montreal")
        assert len(raw) <= 8_000 + 5  # small slack for closing chars

    def test_forecast_days_clamped_to_max(self):
        mock_http, calls = _make_http_mock(_FAKE_GEO, _FAKE_FORECAST)
        with patch("jeanmichel.tools.weather._http_get_json", side_effect=mock_http):
            WEATHER_SPEC.handler(location="Montreal", mode="forecast", forecast_days=999)
        weather_url = next(u for u in calls if "geocoding-api" not in u)
        assert "forecast_days=16" in weather_url

    def test_past_days_clamped_to_max(self):
        mock_http, calls = _make_http_mock(_FAKE_GEO, _FAKE_FORECAST)
        with patch("jeanmichel.tools.weather._http_get_json", side_effect=mock_http):
            WEATHER_SPEC.handler(location="Montreal", mode="history", past_days=999)
        weather_url = next(u for u in calls if "geocoding-api" not in u)
        assert "past_days=92" in weather_url
