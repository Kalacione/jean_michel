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


def _fake_hourly(monkeypatch):
    """Stub hourly covering today + tomorrow, 24h each, so part-of-day windows
    (incl. the night wrap past midnight) have data to slice. Captures the query
    params so the test can assert start_date/end_date were requested."""
    base = date.today()
    captured: dict = {}
    times, t2m, pp, wc = [], [], [], []
    for d in (base, base + timedelta(days=1)):
        for h in range(24):
            times.append(f"{d.isoformat()}T{h:02d}:00")
            t2m.append(10 + h % 10)
            pp.append(h * 2 % 100)
            wc.append(2 if 18 <= h < 23 else 0)
    raw = {
        "utc_offset_seconds": -14400,
        "timezone": "America/Montreal",
        "hourly": {
            "time": times, "temperature_2m": t2m, "apparent_temperature": t2m,
            "precipitation_probability": pp, "weather_code": wc,
        },
    }

    def _capture(url):
        captured["url"] = url
        return raw

    monkeypatch.setattr(weather, "_geocode_openmeteo", lambda n: (45.5, -73.6, "Montreal"))
    monkeypatch.setattr(weather, "_http_get_json", _capture)
    return base, captured


def test_when_evening_slices_the_part_window(monkeypatch):
    base, captured = _fake_hourly(monkeypatch)
    out = json.loads(weather._handler(location="Montreal", when="this evening"))
    assert out["mode"] == "hourly"
    assert out["requested_window"] == "evening"
    assert out["requested_day"] == base.isoformat()
    hours = [int(t[11:13]) for t in out["hourly"]["time"]]
    assert hours == [18, 19, 20, 21, 22]              # evening window (18–22)
    assert "Montreal" in out["summary"]
    # same-day window → single-day fetch
    assert f"start_date={base.isoformat()}" in captured["url"]
    assert f"end_date={base.isoformat()}" in captured["url"]


def test_when_night_wraps_to_next_day(monkeypatch):
    base, captured = _fake_hourly(monkeypatch)
    out = json.loads(weather._handler(location="Montreal", when="tomorrow night"))
    assert out["requested_window"] == "night"
    tomorrow = (base + timedelta(days=1)).isoformat()
    day_after = (base + timedelta(days=2)).isoformat()
    assert out["requested_day"] == tomorrow
    # night wraps midnight → fetch spans tomorrow..day-after
    assert f"start_date={tomorrow}" in captured["url"]
    assert f"end_date={day_after}" in captured["url"]
    # only the 23:00 slot exists in the (2-day) stub, but it is the right one
    pairs = [(t[:10], int(t[11:13])) for t in out["hourly"]["time"]]
    assert (tomorrow, 23) in pairs
    assert all(d == tomorrow and h >= 23 for d, h in pairs)
