"""Tier 0 dispatcher — entry point classifier for v2 (cf. §3 doc 06).

Two operations :

- `classify(user_text, llm)` → `DispatchDecision(intent, tool, args, confidence)`.
  Calls `DISPATCH_MODEL` with `format="json"` and the static
  `DISPATCH_SYSTEM_PROMPT`. One retry on parse failure, then fallback to
  `intent="deep"` with `confidence="low"`.

- `execute_alexa(decision, llm, user_lang)` → final user-facing string.
  Invokes the native Python handler for the chosen tool, then formats the
  result in the user's language. For `clock` / `weather` outputs already
  carry a usable summary, so if the user is in English we use it verbatim.
  Otherwise (and for `wikipedia_search` regardless of language), a short
  second granite call reformulates the JSON into 1-3 sentences in the
  detected language.

The dispatcher is stateless. It owns no conversation, no DB session, no
filesystem path. It only needs an `LLMClientV2` and the user_text.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .config import DISPATCH_MODEL
from .prompts import DISPATCH_SYSTEM_PROMPT
from .tools.clock import _handler as _clock_handler
from .tools.weather import _handler as _weather_handler
from .tools.wikipedia import _search_handler as _wiki_search_handler

_log = logging.getLogger(__name__)

# ---- Constants ------------------------------------------------------------

# The exact set of tools the dispatcher is allowed to route to in ALEXA mode.
# Mirrors §3 doc 06. Adding a tool here requires :
#   1. updating DISPATCH_SYSTEM_PROMPT in prompts.py,
#   2. routing it in `_invoke_tool`,
#   3. adding a test.
_ALEXA_TOOLS: frozenset[str] = frozenset({"clock", "weather", "wikipedia_search"})

# Tools whose JSON `summary` is human-readable English prose. For non-English
# users we still pipe through the granite formatter, but for `en` we can use
# the summary verbatim (saves an LLM round-trip).
_TEMPLATABLE_TOOLS: frozenset[str] = frozenset({"clock", "weather"})

# Mapping of ISO 639-1 codes → English language name used in the formatter
# prompt. Anything not in here falls back to "English" (so the formatter
# returns English prose — safer default than the wrong language).
_LANG_NAMES: dict[str, str] = {
    "fr": "French",
    "en": "English",
    "es": "Spanish",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
}


# ---- Language detection --------------------------------------------------

try:
    from langdetect import DetectorFactory as _LangDetectorFactory
    from langdetect import detect as _ld_detect

    # Deterministic seed — same text always returns the same language code.
    _LangDetectorFactory.seed = 0
except ImportError:  # pragma: no cover — langdetect is in pyproject.toml
    _ld_detect = None


def detect_language(text: str, fallback: str = "en") -> str:
    """Detect the language of `text`. Returns ISO 639-1 code (e.g. 'fr', 'en').

    Short texts and ambiguous strings may yield wrong results — langdetect is
    statistical. On any failure we return `fallback` rather than raising.
    """
    if _ld_detect is None or not text or not text.strip():
        return fallback
    try:
        return _ld_detect(text)
    except Exception:  # noqa: BLE001
        return fallback


# ---- Result type ---------------------------------------------------------


@dataclass
class DispatchDecision:
    """The classifier's verdict on a user request.

    Fields :
    - `intent` : "alexa" or "deep". Always one of these two — even when the
      LLM returned garbage, the fallback sets intent="deep".
    - `tool` : the ALEXA tool name when intent="alexa" (one of `_ALEXA_TOOLS`).
      `None` otherwise.
    - `args` : kwargs to pass to the tool handler. Empty dict for `deep`.
    - `confidence` : "high" when the LLM produced a clean parseable JSON
      matching the schema. "low" when we fell back (parse failure, unknown
      tool, etc.) — the caller may decide to ignore an ALEXA decision with
      low confidence and route to DEEP anyway.
    - `raw_response` : the LLM's raw output (for debugging / events).
    """
    intent: str  # "alexa" | "deep"
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    confidence: str = "high"  # "high" | "low"
    raw_response: str = ""


# ---- classify ------------------------------------------------------------


def classify(
    user_text: str,
    llm: Any,
    model: str | None = None,
    history: list[dict[str, Any]] | None = None,
    today: date | None = None,
    default_location: str | None = None,
) -> DispatchDecision:
    """Classify a user request via the dispatcher LLM.

    ``history`` (optional) is the prior turns of the conversation
    (system / user / assistant messages). When passed, the dispatcher sees
    the last 4 exchanges before the current ``user_text`` — that's enough
    for follow-ups like "et pour demain ?" to resolve to a clock/weather
    call with the previously-mentioned topic, instead of falling through
    to a low-confidence DEEP path.

    Tries up to twice on parse failure. On final failure, returns a `deep`
    fallback with `confidence="low"`. Never raises — the dispatcher is a
    safety net, not an enforcement gate.
    """
    chosen_model = model or DISPATCH_MODEL
    last_raw = ""
    messages = _build_dispatcher_messages(user_text, history, today, default_location)

    for attempt in (1, 2):
        try:
            resp = llm.chat_messages(
                messages=messages,
                tools=[],
                temperature=0.0,
                thinking=False,
                model=chosen_model,
                format="json",
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "Dispatcher LLM call failed (attempt %d): %s",
                attempt,
                exc,
            )
            last_raw = ""
            continue

        last_raw = resp.content or ""
        decision = _parse_response(last_raw)
        if decision is not None:
            decision.raw_response = last_raw
            return decision

        if attempt == 1:
            _log.warning(
                "Dispatcher returned unparseable JSON (attempt %d), retrying. "
                "Raw: %r",
                attempt,
                last_raw[:200],
            )

    # Fallback after both attempts failed.
    return DispatchDecision(
        intent="deep",
        tool=None,
        args={},
        confidence="low",
        raw_response=last_raw,
    )


_HISTORY_TURNS_FOR_DISPATCHER = 4  # last ~2 exchanges = enough for follow-ups


def _build_dispatcher_messages(
    user_text: str,
    history: list[dict[str, Any]] | None,
    today: date | None = None,
    default_location: str | None = None,
) -> list[dict[str, Any]]:
    """Prepend a small slice of conversation history to the dispatcher prompt.

    Strategy : keep the system prompt first, then a deterministic ## Context
    block (today's date + the user's default location — so the small no-thinking
    LLM never has to compute a date or guess a city), then the last N
    user/assistant pairs (skipping tool/system messages), then the current
    user_text. Tool noise would confuse the small 8b classifier.
    """
    system = DISPATCH_SYSTEM_PROMPT
    context_lines: list[str] = []
    if today is not None:
        context_lines.append(f"Today is {today.strftime('%A')}, {today.isoformat()}.")
    if default_location:
        context_lines.append(
            f"Default location: {default_location}. Use it whenever the user "
            "does not name a place (omit `location`)."
        )
    if context_lines:
        system = f"{system}\n\n## Context\n" + "\n".join(context_lines)

    out: list[dict[str, Any]] = [
        {"role": "system", "content": system},
    ]
    if history:
        # Keep only user + assistant turns (drop system, tool messages with
        # tool_calls or role=tool). Then take the tail.
        clean = [
            m for m in history
            if m.get("role") in ("user", "assistant")
            and m.get("content")
            and not m.get("tool_calls")
        ]
        for m in clean[-_HISTORY_TURNS_FOR_DISPATCHER:]:
            out.append({
                "role": m["role"],
                "content": str(m["content"])[:500],
            })
    out.append({"role": "user", "content": user_text})
    return out


def _parse_response(raw: str) -> DispatchDecision | None:
    """Validate the LLM's JSON output against the schema.

    Returns a `DispatchDecision` on success, or `None` if the output is
    malformed (caller will retry or fallback). When the LLM returns
    `intent="alexa"` with a hallucinated tool name or `tool=null`, we coerce
    to `intent="deep"` with `confidence="low"` per §3 doc 06.

    Tolerant coercions for the small dispatcher LLM (granite 8b confuses
    `intent` and `tool` regularly) :
      - intent equals a known tool name → infer alexa, use that tool
      - intent missing/garbled but tool is a known ALEXA tool → infer alexa
    """
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    intent = data.get("intent")
    tool = data.get("tool")
    args = data.get("args") or {}
    if not isinstance(args, dict):
        args = {}

    # Coercion #1 : intent contains a tool name (e.g. "wikipedia_search").
    if intent not in ("alexa", "deep") and intent in _ALEXA_TOOLS:
        tool = tool or intent
        intent = "alexa"

    # Coercion #2 : intent is null/garbled but tool is recognisable.
    elif intent not in ("alexa", "deep") and tool in _ALEXA_TOOLS:
        intent = "alexa"

    if intent not in ("alexa", "deep"):
        return None

    if intent == "alexa":
        if tool is None:
            # ALEXA but the LLM couldn't name a tool — per §3 doc 06,
            # treat as DEEP (the safe default is to reflect more).
            return DispatchDecision(
                intent="deep",
                tool=None,
                args={},
                confidence="low",
            )
        if tool not in _ALEXA_TOOLS:
            # The LLM invented a tool name. Same fallback.
            return DispatchDecision(
                intent="deep",
                tool=None,
                args={},
                confidence="low",
            )
        return DispatchDecision(
            intent="alexa",
            tool=tool,
            args=args,
            confidence="high",
        )

    # intent == "deep"
    return DispatchDecision(intent="deep", tool=None, args={}, confidence="high")


# ---- execute_alexa -------------------------------------------------------


def execute_alexa(
    decision: DispatchDecision,
    llm: Any,
    user_lang: str = "en",
    model: str | None = None,
    user_profile: Any = None,
) -> str:
    """Execute an ALEXA decision and return a formatted user-facing string.

    When the LLM emits ``clock`` with no args, the user's location is
    injected from ``user_profile`` (city + country) so the bare "what
    time is it ?" returns the user's local time rather than UTC. Pass
    ``user_profile`` = the loaded ``UserProfile`` instance ; ``None``
    disables the injection (used for tests).

    Side-effect-free apart from the (single) granite call for non-English
    formatting or wikipedia. Returns a string — never raises on tool errors
    (we surface the error message as the response).
    """
    if decision.intent != "alexa":
        raise ValueError(
            f"execute_alexa called with intent={decision.intent!r}. "
            "Only ALEXA decisions are executable here."
        )
    if not decision.tool or decision.tool not in _ALEXA_TOOLS:
        raise ValueError(f"Unknown ALEXA tool: {decision.tool!r}")

    # Inject profile-derived location for `clock`/`weather` when the LLM emitted
    # no location ("quelle heure est-il ?" / "la météo ?"). The `when` arg (an
    # English day phrase) is forwarded as-is — the weather tool resolves it.
    args = _enrich_args_from_profile(decision.tool, decision.args, user_profile)

    # --- Step 1 : invoke the native tool -----------------------------------
    try:
        result_json = _invoke_tool(decision.tool, args)
    except Exception as exc:  # noqa: BLE001
        _log.warning("ALEXA tool %r raised: %s", decision.tool, exc)
        return _localize_error(str(exc), user_lang)

    try:
        data = json.loads(result_json)
    except json.JSONDecodeError:
        # Tool handlers always return JSON ; this would be a bug.
        _log.warning("ALEXA tool %r returned non-JSON: %r", decision.tool, result_json[:200])
        data = {"error": "tool_output_invalid", "summary": result_json[:300]}

    if not isinstance(data, dict):
        data = {"error": "tool_output_invalid", "summary": str(data)[:300]}

    # --- Step 2 : format in user_lang --------------------------------------
    if "error" in data:
        # The tool's `summary` field, when present, already carries a
        # human-readable error description. For non-English users we still
        # translate via the formatter.
        summary = data.get("summary") or data.get("error") or "Tool error"
        if user_lang == "en":
            return summary
        return _format_via_llm(data, user_lang, llm, model)

    if decision.tool in _TEMPLATABLE_TOOLS and user_lang == "en":
        summary = data.get("summary", "")
        if summary:
            return summary
        # Should never happen — tool_ok always sets summary — but be safe.
        return json.dumps(data, ensure_ascii=False)

    # Wikipedia (any language) OR clock/weather in a non-English user_lang :
    # call granite to format.
    return _format_via_llm(data, user_lang, llm, model)


def _enrich_args_from_profile(
    tool: str,
    args: dict[str, Any],
    user_profile: Any,
) -> dict[str, Any]:
    """Pre-fill missing args from the user profile.

    Returns a NEW dict — does not mutate the original. When the dispatcher
    emits ``clock`` or ``weather`` without an explicit ``location`` (and,
    for clock, without ``timezone``), the user's own city / country is
    injected so the result is local to them by default. Without this, the
    LLM tends to hallucinate a location based on the request language (a
    French user asking for "la météo" would get Paris).
    """
    enriched = dict(args or {})

    if tool == "clock":
        has_loc = bool(enriched.get("location") or enriched.get("timezone"))
        if not has_loc and user_profile is not None:
            location = _profile_location(user_profile)
            if location:
                enriched["location"] = location

    elif tool == "weather":
        has_loc = bool(enriched.get("location"))
        if not has_loc and user_profile is not None:
            location = _profile_location(user_profile)
            if location:
                enriched["location"] = location

    return enriched


def _profile_location(user_profile: Any) -> str:
    """Format the user's city + country as a single 'City, Country' string."""
    city = (getattr(user_profile, "city", "") or "").strip()
    country = (getattr(user_profile, "country", "") or "").strip()
    parts = [p for p in (city, country) if p]
    return ", ".join(parts)


def _invoke_tool(tool: str, args: dict[str, Any]) -> str:
    """Dispatch to the underlying native tool handler. Returns the tool's JSON string."""
    if tool == "clock":
        return _clock_handler(**args)
    if tool == "weather":
        return _weather_handler(**args)
    if tool == "wikipedia_search":
        return _wiki_search_handler(**args)
    raise ValueError(f"No handler for ALEXA tool: {tool!r}")


def _localize_error(msg: str, user_lang: str) -> str:
    """Format an unexpected tool error in user_lang (best effort, no LLM)."""
    if user_lang == "fr":
        return f"Erreur : {msg}"
    if user_lang == "es":
        return f"Error: {msg}"
    return f"Error: {msg}"


def _format_via_llm(
    data: dict[str, Any],
    user_lang: str,
    llm: Any,
    model: str | None,
) -> str:
    """Short LLM call (no thinking, no tools) to reformulate tool output in user_lang."""
    lang_name = _LANG_NAMES.get(user_lang, "English")
    chosen_model = model or DISPATCH_MODEL

    system = (
        f"You reformulate tool output into a direct, 1-3 sentence answer "
        f"in {lang_name}. No introduction, no preamble, no markdown — just "
        "the answer."
    )
    user = (
        "Here is the tool output as JSON. Produce the user-facing answer "
        f"in {lang_name} :\n\n"
        f"{json.dumps(data, ensure_ascii=False)}"
    )
    try:
        resp = llm.chat_messages(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            tools=[],
            temperature=0.0,
            thinking=False,
            model=chosen_model,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("ALEXA formatter LLM call failed: %s — falling back to raw summary", exc)
        return data.get("summary") or json.dumps(data, ensure_ascii=False)

    text = (resp.content or "").strip()
    if not text:
        return data.get("summary") or json.dumps(data, ensure_ascii=False)
    return text
