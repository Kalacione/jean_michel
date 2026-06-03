"""Tests for the weather tool's `when` → forecast-day focusing (no network)."""

from __future__ import annotations

import json
from datetime import date, timedelta

from jeanmichel.tools import weather


def _fake_forecast(monkeypatch):
    """Stub geocoding + HTTP so the tool runs offline. Daily covers today..+3."""
    base = date.today()
    days = [(base + timedelta(days=d)).isoformat() for d in range(4)]
    raw = {
        "utc_offset_seconds": -14400,
        "timezone": "America/Montreal",
        "daily": {
            "time": days,
            "temperature_2m_min": [10, 11, 12, 13],
            "temperature_2m_max": [20, 21, 22, 23],
            "weather_code": [3, 61, 2, 0],
        },
        "daily_units": {"temperature_2m_max": "°C"},
    }
    monkeypatch.setattr(weather, "_geocode_openmeteo", lambda n: (45.5, -73.6, "Montreal"))
    monkeypatch.setattr(weather, "_http_get_json", lambda url: raw)
    return days


def test_when_focuses_the_requested_forecast_day(monkeypatch):
    days = _fake_forecast(monkeypatch)
    out = json.loads(weather._handler(location="Montreal", when="tomorrow"))
    assert out["mode"] == "forecast"
    assert out["requested_date"] == days[1]                  # tomorrow
    assert out["daily"]["time"] == [days[1]]                  # trimmed to that day
    assert out["daily"]["temperature_2m_max"] == [21]         # tomorrow's value, not today's
    assert "Montreal" in out["summary"]


def test_when_today_stays_current(monkeypatch):
    _fake_forecast(monkeypatch)
    out = json.loads(weather._handler(location="Montreal", when="today"))
    assert out["mode"] == "current"          # offset 0 → no forecast switch
    assert "requested_date" not in out


def test_no_when_is_unchanged(monkeypatch):
    _fake_forecast(monkeypatch)
    out = json.loads(weather._handler(location="Montreal"))
    assert out["mode"] == "current"
    assert "requested_date" not in out
