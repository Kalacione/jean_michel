"""Unit tests for src/jeanmichel/tools/."""

from __future__ import annotations

import json
from unittest.mock import patch

from jeanmichel.tools.clock import SPEC as CLOCK_SPEC
from jeanmichel.tools.conv_read_file import make_spec
from jeanmichel.tools.weather import SPEC as WEATHER_SPEC
from jeanmichel.tools.wikipedia import GET_PAGE_SPEC as WIKI_GET_PAGE
from jeanmichel.tools.wikipedia import SEARCH_SPEC as WIKI_SEARCH


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

    def test_local_date_present_in_current(self):
        mock_http, _ = _make_http_mock(_FAKE_GEO, _FAKE_CURRENT)
        with patch("jeanmichel.tools.weather._http_get_json", side_effect=mock_http):
            result = json.loads(WEATHER_SPEC.handler(location="Montreal"))
        assert "local_date" in result
        # UTC-4 offset (-14400s) → date must be a valid ISO date string
        import re
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", result["local_date"])

    def test_local_date_present_in_forecast(self):
        mock_http, _ = _make_http_mock(_FAKE_GEO, _FAKE_FORECAST)
        with patch("jeanmichel.tools.weather._http_get_json", side_effect=mock_http):
            result = json.loads(WEATHER_SPEC.handler(location="Montreal", mode="forecast", forecast_days=2))
        assert "local_date" in result


# ---------------------------------------------------------------------------
# Fake Wikipedia responses used across TestWikipedia
# ---------------------------------------------------------------------------

_FAKE_SEARCH_RESULTS = [
    "Leaning Tower of Pisa",
    "Pisa Cathedral",
    "Pisa",
]

_FAKE_PAGE_CONTENT = (
    "The Leaning Tower of Pisa (Italian: Torre pendente di Pisa) is the "
    "campanile, or freestanding bell tower, of Pisa Cathedral.\n\n"
    "== Tilt ==\n"
    "Construction of the tower occurred in three stages across 199 years. "
    "The tower's tilt began during construction in the 12th century, caused "
    "by an inadequate foundation on ground too soft on one side. "
    "As of 2020, the tower leans at an angle of 3.97 degrees. "
    "The top of the tower is displaced 3.9 metres (12 ft 10 in) from where "
    "it would stand if the structure were perfectly vertical."
)

_FAKE_PAGE = {
    "title": "Leaning Tower of Pisa",
    "url": "https://en.wikipedia.org/wiki/Leaning_Tower_of_Pisa",
    "summary": "The Leaning Tower of Pisa is the campanile of Pisa Cathedral.",
    "content": _FAKE_PAGE_CONTENT,
}


class TestWikipedia:
    def test_search_returns_titles(self):
        with patch("jeanmichel.tools.wikipedia._wiki_search", return_value=_FAKE_SEARCH_RESULTS):
            result = json.loads(WIKI_SEARCH.handler(query="Tower of Pisa"))
        assert result["query"] == "Tower of Pisa"
        assert "Leaning Tower of Pisa" in result["results"]

    def test_search_results_clamped_to_10(self):
        with patch("jeanmichel.tools.wikipedia._wiki_search", return_value=_FAKE_SEARCH_RESULTS) as m:
            WIKI_SEARCH.handler(query="Pisa", results=999)
        _called_results = m.call_args[1]["results"] if m.call_args[1] else m.call_args[0][1]
        assert _called_results <= 10

    def test_search_failure_returns_error(self):
        with patch("jeanmichel.tools.wikipedia._wiki_search", side_effect=OSError("timeout")):
            result = json.loads(WIKI_SEARCH.handler(query="anything"))
        assert "error" in result

    def test_search_retries_on_busy_error_then_succeeds(self):
        """Busy error on first attempt → retry → success on second attempt."""
        busy = Exception('An unknown error occured: "Search is currently too busy. Please try again later."')
        call_count = {"n": 0}

        def _side_effect(query, results):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise busy
            return _FAKE_SEARCH_RESULTS

        with patch("jeanmichel.tools.wikipedia._wiki_search", side_effect=_side_effect):
            with patch("jeanmichel.tools.wikipedia.time.sleep") as mock_sleep:
                result = json.loads(WIKI_SEARCH.handler(query="Nazism"))

        assert "results" in result
        assert "error" not in result
        assert call_count["n"] == 2
        mock_sleep.assert_called_once()

    def test_search_all_retries_exhausted_returns_error_with_hint(self):
        """Busy error on all attempts → error JSON with a hint to use get_page."""
        busy = Exception('Search is currently too busy. Please try again later.')

        with patch("jeanmichel.tools.wikipedia._wiki_search", side_effect=busy):
            with patch("jeanmichel.tools.wikipedia.time.sleep"):
                result = json.loads(WIKI_SEARCH.handler(query="Nazism"))

        assert "error" in result
        assert "hint" in result

    def test_get_page_returns_content(self):
        with patch("jeanmichel.tools.wikipedia._wiki_get_page", return_value=_FAKE_PAGE):
            result = json.loads(WIKI_GET_PAGE.handler(title="Leaning Tower of Pisa"))
        assert result["title"] == "Leaning Tower of Pisa"
        assert "3.97 degrees" in result["content"]
        assert "url" in result
        assert "summary" in result

    def test_get_page_not_found_returns_error(self):
        with patch("jeanmichel.tools.wikipedia._wiki_get_page", side_effect=Exception("Page not found")):
            result = json.loads(WIKI_GET_PAGE.handler(title="NonExistentXYZ"))
        assert "error" in result

    def test_get_page_disambiguation_returns_options(self):
        exc = Exception("Disambiguation")
        exc.options = ["Pisa", "Pisa Cathedral", "Leaning Tower of Pisa"]
        with patch("jeanmichel.tools.wikipedia._wiki_get_page", side_effect=exc):
            result = json.loads(WIKI_GET_PAGE.handler(title="Pisa"))
        assert "error" in result
        assert "options" in result
        assert "Leaning Tower of Pisa" in result["options"]

    def test_get_page_content_truncated(self):
        huge_page = dict(_FAKE_PAGE)
        huge_page["content"] = "x" * 20_000
        with patch("jeanmichel.tools.wikipedia._wiki_get_page", return_value=huge_page):
            result = json.loads(WIKI_GET_PAGE.handler(title="Leaning Tower of Pisa"))
        assert len(result["content"]) <= 12_000

    def test_tower_of_pisa_inclination_scenario(self):
        """Full scenario: search → get page → inclination angle extractable."""
        with patch("jeanmichel.tools.wikipedia._wiki_search", return_value=_FAKE_SEARCH_RESULTS):
            search_result = json.loads(WIKI_SEARCH.handler(query="Tour de Pise inclinaison"))
        # Specialist picks the most relevant title from results
        best_title = search_result["results"][0]
        assert best_title == "Leaning Tower of Pisa"

        with patch("jeanmichel.tools.wikipedia._wiki_get_page", return_value=_FAKE_PAGE):
            page_result = json.loads(WIKI_GET_PAGE.handler(title=best_title))
        # The inclination angle is present in the content
        assert "3.97 degrees" in page_result["content"]


# ---------------------------------------------------------------------------
# WebSearch
# ---------------------------------------------------------------------------

from jeanmichel.tools.web_search import SPEC as WEB_SEARCH_SPEC  # noqa: E402
import jeanmichel.tools.web_search as _ws_mod                     # noqa: E402

_FAKE_SEARXNG_RESULTS = [
    {"title": "Résultat A", "url": "https://example.com/a", "content": "Snippet A"},
    {"title": "Résultat B", "url": "https://example.com/b", "content": "Snippet B"},
]


class TestWebSearch:
    def test_returns_results_when_alive(self):
        with (
            patch.object(_ws_mod, "_is_alive", return_value=True),
            patch.object(_ws_mod, "_do_search", return_value=_FAKE_SEARXNG_RESULTS),
        ):
            result = json.loads(WEB_SEARCH_SPEC.handler("test query"))
        assert result["query"] == "test query"
        assert len(result["results"]) == 2
        assert result["results"][0]["url"] == "https://example.com/a"

    def test_starts_container_when_not_alive(self):
        started = []
        with (
            patch.object(_ws_mod, "_is_alive", return_value=False),
            patch.object(_ws_mod, "_docker_start", side_effect=lambda: started.append(1)),
            patch.object(_ws_mod, "_wait_until_alive", return_value=True),
            patch.object(_ws_mod, "_do_search", return_value=_FAKE_SEARXNG_RESULTS),
        ):
            result = json.loads(WEB_SEARCH_SPEC.handler("test"))
        assert started == [1]
        assert "results" in result

    def test_error_when_container_fails_to_start(self):
        with (
            patch.object(_ws_mod, "_is_alive", return_value=False),
            patch.object(_ws_mod, "_docker_start", return_value=None),
            patch.object(_ws_mod, "_wait_until_alive", return_value=False),
        ):
            result = json.loads(WEB_SEARCH_SPEC.handler("test"))
        assert "error" in result

    def test_results_capped_at_max(self):
        many = [{"title": f"R{i}", "url": f"https://x.com/{i}", "content": ""} for i in range(20)]
        with (
            patch.object(_ws_mod, "_is_alive", return_value=True),
            patch.object(_ws_mod, "_do_search", return_value=many),
        ):
            result = json.loads(WEB_SEARCH_SPEC.handler("test", results=100))
        assert len(result["results"]) <= _ws_mod._MAX_RESULTS

    def test_docker_start_error_returns_error(self):
        import subprocess
        with (
            patch.object(_ws_mod, "_is_alive", return_value=False),
            patch.object(_ws_mod, "_docker_start",
                         side_effect=subprocess.CalledProcessError(1, "docker", stderr=b"fail")),
        ):
            result = json.loads(WEB_SEARCH_SPEC.handler("test"))
        assert "error" in result
