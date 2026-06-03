"""Shared temporal resolution — turn an ENGLISH relative day phrase into a
day offset, deterministically (no LLM).

Internal mechanics are English (cf. the project's English-internal convention):
callers pass an English phrase ("tomorrow", "thursday evening", "next monday",
"in 3 days", "june 10"). A small normalization layer maps the colloquial bits
dateparser doesn't handle on its own ("tonight"/"this evening" → today,
"next <weekday>" → "<weekday>", "weekend" → saturday, trailing time-of-day
dropped), then `dateparser` does the arithmetic.

Used by the weather tool (and reusable by clock) so EVERY caller — the ALEXA
dispatcher AND DEEP specialists — resolves relative days the same way, without
needing the current date in its prompt.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime

_log = logging.getLogger(__name__)

_WEEKDAY = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_TIME_OF_DAY = "morning|afternoon|evening|night|noon|midday"


def _normalize_en(phrase: str) -> str:
    """Reduce a colloquial English day phrase to something dateparser resolves."""
    w = phrase.strip().lower()
    w = re.sub(r"\btonight\b", "today", w)
    w = re.sub(rf"\bthis ({_TIME_OF_DAY})\b", "today", w)
    w = re.sub(rf"\bnext\s+({_WEEKDAY})\b", r"\1", w)   # future-pref already picks "next"
    w = re.sub(r"\b(this |next )?weekend\b", "saturday", w)
    w = re.sub(rf"\b({_TIME_OF_DAY})\b", "", w)         # drop a trailing "evening" etc.
    return re.sub(r"\s+", " ", w).strip()


def resolve_when(phrase: str | None, base_date: date | None = None) -> int | None:
    """English relative day phrase → offset in days from ``base_date`` (today).

    0 = today, 1 = tomorrow, negative = past. ``None`` if the phrase is empty,
    unparseable, or ``dateparser`` is unavailable (caller falls back to current).
    """
    if not phrase or not phrase.strip():
        return None
    try:
        import dateparser
    except ImportError:  # soft dependency : degrade to "no resolution"
        _log.debug("dateparser not installed; `when` resolution disabled")
        return None
    base = base_date or date.today()
    normalized = _normalize_en(phrase)
    if not normalized:
        return None
    parsed = dateparser.parse(
        normalized,
        languages=["en"],
        settings={
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": datetime(base.year, base.month, base.day),
            "RETURN_AS_TIMEZONE_AWARE": False,
        },
    )
    if parsed is None:
        return None
    return (parsed.date() - base).days
