"""Shared temporal resolution — turn a relative day phrase (FR or EN) into a
day offset and an optional part-of-day, deterministically (no LLM).

The Tier-0 dispatcher LLM is small and has no thinking budget: it only *copies*
the time expression the user wrote (in their own language), it does not compute
dates and does not reliably translate. So this resolver accepts both French and
English input — we fix the tool rather than teaching the dumb LLM to work around
an English-only limit. English stays the convention for everything the tool
*emits* (window names, summaries); only the *input* tolerates the user's tongue.

A small normalization layer maps the colloquial bits dateparser can't handle on
its own ("ce soir"/"tonight" → today, "jeudi prochain"/"next monday" → bare
weekday, "week-end"/"weekend" → saturday, trailing time-of-day dropped), then
`dateparser` does the arithmetic.

Used by the weather tool (and reusable by clock) so EVERY caller — the ALEXA
dispatcher AND DEEP specialists — resolves relative days the same way, without
needing the current date in its prompt.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime

_log = logging.getLogger(__name__)

_WEEKDAY = (
    "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    "|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche"
)
# Order-insensitive here; part_of_day() handles the "après-midi contains midi"
# overlap by checking afternoon before midday.
_TIME_OF_DAY = (
    "morning|afternoon|evening|night|noon|midday"
    "|matin|après-midi|aprem|soir|soirée|midi|nuit"
)


def _normalize(phrase: str) -> str:
    """Reduce a colloquial FR/EN day phrase to something dateparser resolves."""
    w = phrase.strip().lower()
    # "now-ish today" colloquials → today / aujourd'hui
    w = re.sub(r"\btonight\b", "today", w)
    w = re.sub(rf"\bthis ({_TIME_OF_DAY})\b", "today", w)
    w = re.sub(r"\bce soir\b|\bcette nuit\b|\bce matin\b|\bce midi\b|\bcet après-midi\b", "today", w)
    # next <weekday> / <weekday> prochain → bare weekday (future-pref picks next)
    w = re.sub(rf"\bnext\s+({_WEEKDAY})\b", r"\1", w)
    w = re.sub(rf"\b({_WEEKDAY})\s+prochain\b", r"\1", w)
    # weekend → saturday anchor (EN saturday parses under languages=["fr","en"])
    w = re.sub(r"\b(this |next |ce |le )?week[- ]?end\b", "saturday", w)
    # drop a trailing/standalone time-of-day word ("jeudi soir" → "jeudi")
    w = re.sub(rf"\b({_TIME_OF_DAY})\b", "", w)
    return re.sub(r"\s+", " ", w).strip()


def resolve_when(phrase: str | None, base_date: date | None = None) -> int | None:
    """FR/EN relative day phrase → offset in days from ``base_date`` (today).

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
    normalized = _normalize(phrase)
    if not normalized:
        return None
    parsed = dateparser.parse(
        normalized,
        languages=["fr", "en"],
        settings={
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": datetime(base.year, base.month, base.day),
            "RETURN_AS_TIMEZONE_AWARE": False,
        },
    )
    if parsed is None:
        return None
    return (parsed.date() - base).days


# Part-of-day windows as local-hour ranges [start, end). `night` wraps midnight
# (23:00 → 05:00), so its start > end — callers must handle the wrap.
PART_WINDOWS: dict[str, tuple[int, int]] = {
    "morning": (6, 11),     # 06:00–10:59
    "midday": (11, 14),     # 11:00–13:59
    "afternoon": (14, 18),  # 14:00–17:59
    "evening": (18, 23),    # 18:00–22:59
    "night": (23, 5),       # 23:00–04:59 (wraps midnight)
}


def part_of_day(phrase: str | None) -> str | None:
    """Detect a FR/EN part-of-day in the phrase ("ce soir"/"this evening" →
    'evening', "demain matin"/"tomorrow morning" → 'morning', "tonight" →
    'evening'), else None. Afternoon is checked before midday because
    "après-midi" contains "midi"."""
    if not phrase:
        return None
    w = phrase.lower()
    if re.search(r"\b(tonight|evening|soir|soirée)\b", w):
        return "evening"
    if re.search(r"\b(afternoon|après-midi|aprem)\b", w):
        return "afternoon"
    if re.search(r"\b(noon|midday|midi)\b", w):
        return "midday"
    if re.search(r"\b(morning|matin)\b", w):
        return "morning"
    if re.search(r"\b(night|overnight|nuit)\b", w):
        return "night"
    return None
