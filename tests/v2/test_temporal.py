"""Tests for the shared temporal resolver — ENGLISH relative day → day offset."""

from __future__ import annotations

import builtins
from datetime import date

from jeanmichel.tools._temporal import _normalize, part_of_day, resolve_when

TUE = date(2026, 6, 2)  # a Tuesday — fixed base for deterministic offsets


def test_resolve_relatives_and_weekdays_en():
    assert resolve_when("today", TUE) == 0
    assert resolve_when("tonight", TUE) == 0
    assert resolve_when("this evening", TUE) == 0
    assert resolve_when("tomorrow", TUE) == 1
    assert resolve_when("tomorrow morning", TUE) == 1
    assert resolve_when("thursday", TUE) == 2
    assert resolve_when("thursday evening", TUE) == 2
    assert resolve_when("next monday", TUE) == 6
    assert resolve_when("this weekend", TUE) == 4       # → saturday
    assert resolve_when("in 3 days", TUE) == 3
    assert resolve_when("june 10", TUE) == 8            # absolute date, free from dateparser


def test_resolve_relatives_and_weekdays_fr():
    # The dumb dispatcher copies the user's words verbatim — French is resolved
    # by the tool, not translated by the LLM. These are the real failing cases.
    assert resolve_when("ce soir", TUE) == 0
    assert resolve_when("cette nuit", TUE) == 0
    assert resolve_when("demain", TUE) == 1
    assert resolve_when("demain matin", TUE) == 1
    assert resolve_when("jeudi", TUE) == 2
    assert resolve_when("jeudi soir", TUE) == 2         # the user's real case
    assert resolve_when("jeudi prochain", TUE) == 2
    assert resolve_when("ce week-end", TUE) == 4        # → samedi/saturday
    assert resolve_when("dans 3 jours", TUE) == 3


def test_resolve_unparseable_and_empty():
    assert resolve_when("", TUE) is None
    assert resolve_when(None, TUE) is None
    assert resolve_when("gibberish xyz", TUE) is None


def test_normalize():
    assert _normalize("thursday evening") == "thursday"
    assert _normalize("tonight") == "today"
    assert _normalize("this evening") == "today"
    assert _normalize("next monday") == "monday"
    assert _normalize("this weekend") == "saturday"
    assert _normalize("jeudi soir") == "jeudi"
    assert _normalize("ce soir") == "today"
    assert _normalize("jeudi prochain") == "jeudi"
    assert _normalize("ce week-end") == "saturday"


def test_part_of_day():
    assert part_of_day("this evening") == "evening"
    assert part_of_day("tonight") == "evening"          # tonight → evening hours
    assert part_of_day("tomorrow morning") == "morning"
    assert part_of_day("thursday night") == "night"
    assert part_of_day("at noon") == "midday"
    assert part_of_day("this afternoon") == "afternoon"
    assert part_of_day("thursday") is None              # bare day → no window
    assert part_of_day("in 3 days") is None
    assert part_of_day(None) is None


def test_part_of_day_fr():
    assert part_of_day("ce soir") == "evening"
    assert part_of_day("en soirée") == "evening"
    assert part_of_day("demain matin") == "morning"
    assert part_of_day("cette nuit") == "night"
    assert part_of_day("ce midi") == "midday"
    assert part_of_day("cet après-midi") == "afternoon"  # not midday (contains "midi")
    assert part_of_day("jeudi") is None


def test_graceful_without_dateparser(monkeypatch):
    """Soft dependency : if `dateparser` is missing, resolve_when returns None
    (caller falls back to current weather) instead of raising."""
    real_import = builtins.__import__

    def _no_dateparser(name, *args, **kwargs):
        if name == "dateparser":
            raise ImportError("simulated missing dateparser")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_dateparser)
    assert resolve_when("tomorrow", TUE) is None
