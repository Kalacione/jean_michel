"""Tests for the shared temporal resolver — ENGLISH relative day → day offset."""

from __future__ import annotations

import builtins
from datetime import date

from jeanmichel.tools._temporal import _normalize_en, resolve_when

TUE = date(2026, 6, 2)  # a Tuesday — fixed base for deterministic offsets


def test_resolve_relatives_and_weekdays():
    assert resolve_when("today", TUE) == 0
    assert resolve_when("tonight", TUE) == 0
    assert resolve_when("this evening", TUE) == 0
    assert resolve_when("tomorrow", TUE) == 1
    assert resolve_when("tomorrow morning", TUE) == 1
    assert resolve_when("thursday", TUE) == 2
    assert resolve_when("thursday evening", TUE) == 2   # the user's real case (← "jeudi soir")
    assert resolve_when("next monday", TUE) == 6
    assert resolve_when("this weekend", TUE) == 4       # → saturday
    assert resolve_when("in 3 days", TUE) == 3
    assert resolve_when("june 10", TUE) == 8            # absolute date, free from dateparser


def test_resolve_unparseable_empty_and_french():
    assert resolve_when("", TUE) is None
    assert resolve_when(None, TUE) is None
    assert resolve_when("gibberish xyz", TUE) is None
    # French is NOT accepted — internal mechanics are English ; the LLM must
    # normalize to English before passing `when`.
    assert resolve_when("jeudi soir", TUE) is None


def test_normalize_en():
    assert _normalize_en("thursday evening") == "thursday"
    assert _normalize_en("tonight") == "today"
    assert _normalize_en("this evening") == "today"
    assert _normalize_en("next monday") == "monday"
    assert _normalize_en("this weekend") == "saturday"


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
