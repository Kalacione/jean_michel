"""Tests for `jeanmichel.dispatcher` — Tier 0 classification + ALEXA execution."""

from __future__ import annotations

import json

import pytest

from jeanmichel.dispatcher import (
    _ALEXA_TOOLS,
    DispatchDecision,
    classify,
    detect_language,
    execute_alexa,
)
from jeanmichel.llm import MockClient
from jeanmichel.models import LLMResponse

# ---- classify : JSON parsing + schema validation -------------------------


def test_classify_alexa_clock_valid():
    mock = MockClient(
        script=[LLMResponse(thinking="", content='{"intent":"alexa","tool":"clock","args":{}}')]
    )
    decision = classify("What time is it?", mock)
    assert decision.intent == "alexa"
    assert decision.tool == "clock"
    assert decision.args == {}
    assert decision.confidence == "high"
    assert len(mock.calls_v2) == 1


def test_classify_alexa_weather_with_args():
    mock = MockClient(
        script=[
            LLMResponse(
                thinking="",
                content='{"intent":"alexa","tool":"weather","args":{"location":"Paris"}}',
            )
        ]
    )
    decision = classify("Weather in Paris?", mock)
    assert decision.intent == "alexa"
    assert decision.tool == "weather"
    assert decision.args == {"location": "Paris"}


def test_classify_alexa_wikipedia():
    mock = MockClient(
        script=[
            LLMResponse(
                thinking="",
                content='{"intent":"alexa","tool":"wikipedia_search","args":{"query":"Marie Curie"}}',
            )
        ]
    )
    decision = classify("Who was Marie Curie?", mock)
    assert decision.intent == "alexa"
    assert decision.tool == "wikipedia_search"
    assert decision.args == {"query": "Marie Curie"}


def test_classify_deep_intent():
    mock = MockClient(
        script=[LLMResponse(thinking="", content='{"intent":"deep","tool":null,"args":{}}')]
    )
    decision = classify("Compare Rust and Go for systems programming", mock)
    assert decision.intent == "deep"
    assert decision.tool is None
    assert decision.confidence == "high"


# ---- classify : invocation uses dispatcher contract (format=json, no thinking, model=DISPATCH_MODEL)


def test_classify_passes_format_json_and_no_thinking():
    from jeanmichel.config import DISPATCH_MODEL

    mock = MockClient(
        script=[LLMResponse(thinking="", content='{"intent":"deep","tool":null,"args":{}}')]
    )
    classify("hi", mock)
    call = mock.calls_v2[0]
    assert call["format"] == "json"
    assert call["thinking"] is False
    assert call["model"] == DISPATCH_MODEL
    assert call["temperature"] == 0.0


def test_classify_includes_system_and_user_messages():
    mock = MockClient(
        script=[LLMResponse(thinking="", content='{"intent":"deep","tool":null,"args":{}}')]
    )
    classify("test query", mock)
    msgs = mock.calls_v2[0]["messages"]
    assert msgs[0]["role"] == "system"
    assert "classify" in msgs[0]["content"].lower()
    assert msgs[1] == {"role": "user", "content": "test query"}


# ---- classify : retry on parse failure, then fallback DEEP --------------


def test_classify_invalid_json_retries_then_falls_back():
    mock = MockClient(
        script=[
            LLMResponse(thinking="", content="not json at all"),  # attempt 1
            LLMResponse(thinking="", content="still not json"),  # attempt 2
        ]
    )
    decision = classify("hi", mock)
    assert decision.intent == "deep"
    assert decision.tool is None
    assert decision.confidence == "low"
    assert len(mock.calls_v2) == 2  # retried once


def test_classify_invalid_then_valid_passes_on_second_try():
    mock = MockClient(
        script=[
            LLMResponse(thinking="", content="invalid"),
            LLMResponse(thinking="", content='{"intent":"alexa","tool":"clock","args":{}}'),
        ]
    )
    decision = classify("time?", mock)
    assert decision.intent == "alexa"
    assert decision.tool == "clock"
    assert decision.confidence == "high"


def test_classify_unknown_tool_falls_back_to_deep():
    mock = MockClient(
        script=[
            LLMResponse(
                thinking="",
                content='{"intent":"alexa","tool":"hallucinated_tool","args":{}}',
            )
        ]
    )
    decision = classify("hi", mock)
    assert decision.intent == "deep"
    assert decision.tool is None
    assert decision.confidence == "low"


def test_classify_alexa_with_null_tool_falls_back_to_deep():
    """ALEXA but no tool → §3 doc 06 says fallback to DEEP."""
    mock = MockClient(
        script=[LLMResponse(thinking="", content='{"intent":"alexa","tool":null,"args":{}}')]
    )
    decision = classify("hi", mock)
    assert decision.intent == "deep"
    assert decision.confidence == "low"


def test_classify_garbage_intent_falls_back():
    mock = MockClient(
        script=[
            LLMResponse(thinking="", content='{"intent":"medium","tool":null}'),
            LLMResponse(thinking="", content='{"intent":"medium","tool":null}'),
        ]
    )
    decision = classify("hi", mock)
    assert decision.intent == "deep"
    assert decision.confidence == "low"


def test_classify_coerces_tool_name_in_intent_field():
    """granite 8b confuses intent and tool — coerce when intent is a valid tool name."""
    mock = MockClient(
        script=[
            LLMResponse(
                thinking="",
                content='{"intent":"wikipedia_search","tool":"wikipedia_search","args":{"query":"inference"}}',
            ),
        ]
    )
    decision = classify("qu'est-ce qu'une inférence ?", mock)
    assert decision.intent == "alexa"
    assert decision.tool == "wikipedia_search"
    assert decision.args == {"query": "inference"}
    assert decision.confidence == "high"


def test_classify_coerces_tool_name_in_intent_when_tool_is_null():
    """If the LLM only set intent (to a tool name) and forgot the tool field."""
    mock = MockClient(
        script=[
            LLMResponse(
                thinking="",
                content='{"intent":"clock","tool":null,"args":{}}',
            ),
        ]
    )
    decision = classify("quelle heure ?", mock)
    assert decision.intent == "alexa"
    assert decision.tool == "clock"
    assert decision.confidence == "high"


def test_classify_coerces_garbled_intent_when_tool_valid():
    """intent is None/garbage but tool is a known ALEXA tool — accept it."""
    mock = MockClient(
        script=[
            LLMResponse(
                thinking="",
                content='{"intent":null,"tool":"weather","args":{"location":"Paris"}}',
            ),
        ]
    )
    decision = classify("météo Paris", mock)
    assert decision.intent == "alexa"
    assert decision.tool == "weather"
    assert decision.args == {"location": "Paris"}


# ---- history-aware dispatch (chat/vocal follow-ups) ---------------------


def test_classify_with_history_forwards_recent_turns_to_dispatcher():
    """Dispatcher should see the last user/assistant pairs from history."""
    sent_messages: list = []

    class CapturingClient:
        def chat_messages(self, **kwargs):
            sent_messages.extend(kwargs.get("messages", []))
            return LLMResponse(
                thinking="",
                content='{"intent":"alexa","tool":"weather","args":{}}',
            )

    history = [
        {"role": "system", "content": "you are jean-michel"},
        {"role": "user", "content": "météo de ce soir à Montréal"},
        {"role": "assistant", "content": "Il fera 12°C ce soir."},
    ]
    decision = classify("et pour demain ?", CapturingClient(), history=history)

    assert decision.intent == "alexa"
    assert decision.tool == "weather"
    # The system prompt + history-derived turns + current question are sent.
    roles = [m["role"] for m in sent_messages]
    assert roles[0] == "system"  # DISPATCH_SYSTEM_PROMPT
    assert roles[-1] == "user"  # current question
    # The Montreal reference is reachable in the prompt
    full_text = " ".join(m["content"] for m in sent_messages)
    assert "Montréal" in full_text
    assert "et pour demain" in full_text


def test_classify_without_history_uses_only_current_text():
    """No history → only [system, user] is sent."""
    sent_messages: list = []

    class CapturingClient:
        def chat_messages(self, **kwargs):
            sent_messages.extend(kwargs.get("messages", []))
            return LLMResponse(
                thinking="",
                content='{"intent":"deep","tool":null,"args":{}}',
            )

    classify("hello", CapturingClient(), history=None)
    assert len(sent_messages) == 2
    assert sent_messages[0]["role"] == "system"
    assert sent_messages[1]["role"] == "user"
    assert sent_messages[1]["content"] == "hello"


def test_classify_history_drops_tool_and_assistant_with_tool_calls():
    """History containing tool-call messages should not pollute the dispatcher."""
    sent_messages: list = []

    class CapturingClient:
        def chat_messages(self, **kwargs):
            sent_messages.extend(kwargs.get("messages", []))
            return LLMResponse(
                thinking="",
                content='{"intent":"deep","tool":null,"args":{}}',
            )

    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "trouve X"},
        {"role": "assistant", "content": "", "tool_calls": [{"name": "web_search"}]},
        {"role": "tool", "content": '{"summary": "8 hits"}'},
        {"role": "assistant", "content": "Voici les résultats."},
    ]
    classify("et maintenant ?", CapturingClient(), history=history)

    # Only "trouve X" + final assistant + current user_text expected (besides system)
    history_contents = [m["content"] for m in sent_messages]
    assert any("trouve X" in c for c in history_contents)
    assert any("Voici les résultats" in c for c in history_contents)
    assert not any('"summary": "8 hits"' in c for c in history_contents)


def test_classify_history_truncates_to_last_n_turns():
    """Older history beyond the cutoff is dropped (cap = 4 turns)."""
    sent_messages: list = []

    class CapturingClient:
        def chat_messages(self, **kwargs):
            sent_messages.extend(kwargs.get("messages", []))
            return LLMResponse(
                thinking="",
                content='{"intent":"deep","tool":null,"args":{}}',
            )

    history = [{"role": "user", "content": f"msg_{i}"} for i in range(10)]
    classify("now", CapturingClient(), history=history)
    contents = " ".join(m["content"] for m in sent_messages)
    # Last 4 msgs kept, earlier ones dropped
    assert "msg_9" in contents
    assert "msg_6" in contents
    assert "msg_5" not in contents
    assert "msg_0" not in contents


def test_classify_llm_exception_falls_back_to_deep():
    """If the LLM client raises (Ollama hung, etc.), dispatcher doesn't crash."""

    class FailingClient:
        def chat_messages(self, **kwargs):
            raise RuntimeError("Ollama down")

    decision = classify("hi", FailingClient())
    assert decision.intent == "deep"
    assert decision.confidence == "low"


def test_classify_empty_response_falls_back():
    mock = MockClient(
        script=[
            LLMResponse(thinking="", content=""),
            LLMResponse(thinking="", content=""),
        ]
    )
    decision = classify("hi", mock)
    assert decision.intent == "deep"
    assert decision.confidence == "low"


def test_classify_records_raw_response():
    raw = '{"intent":"alexa","tool":"clock","args":{}}'
    mock = MockClient(script=[LLMResponse(thinking="", content=raw)])
    decision = classify("time?", mock)
    assert decision.raw_response == raw


# ---- detect_language -----------------------------------------------------


def test_detect_language_french():
    assert detect_language("Bonjour, comment allez-vous aujourd'hui ?") == "fr"


def test_detect_language_english():
    assert detect_language("Hello, how are you doing today?") == "en"


def test_detect_language_fallback_on_empty():
    assert detect_language("") == "en"
    assert detect_language("   ") == "en"


def test_detect_language_custom_fallback():
    assert detect_language("", fallback="fr") == "fr"


# ---- execute_alexa : clock (English) — uses tool summary verbatim --------


def test_execute_alexa_clock_english_uses_summary_verbatim():
    decision = DispatchDecision(intent="alexa", tool="clock", args={})
    mock = MockClient(script=[])  # no LLM call expected for clock+en
    result = execute_alexa(decision, mock, user_lang="en")
    # Clock's summary looks like "UTC: 2026-... (UTC ...Z)"
    assert "UTC" in result
    # No LLM was called.
    assert len(mock.calls_v2) == 0


def test_execute_alexa_clock_with_timezone_arg():
    decision = DispatchDecision(intent="alexa", tool="clock", args={"timezone": "Europe/Paris"})
    mock = MockClient(script=[])
    result = execute_alexa(decision, mock, user_lang="en")
    assert "Europe/Paris" in result


# ---- execute_alexa : non-English → LLM formatter called -----------------


def test_execute_alexa_clock_french_invokes_formatter():
    decision = DispatchDecision(intent="alexa", tool="clock", args={})
    mock = MockClient(
        script=[LLMResponse(thinking="", content="Il est 2026-05-28 à 01:49 UTC.")]
    )
    result = execute_alexa(decision, mock, user_lang="fr")
    assert "Il est" in result
    # One LLM call to the formatter.
    assert len(mock.calls_v2) == 1
    # The formatter system prompt mentions the target language.
    sys_msg = mock.calls_v2[0]["messages"][0]["content"]
    assert "French" in sys_msg


# ---- execute_alexa : wikipedia → ALWAYS LLM formatter --------------------


def test_execute_alexa_wikipedia_invokes_formatter_even_in_english(monkeypatch):
    fake_wiki_json = json.dumps(
        {
            "summary": "Found 3 articles for 'Marie Curie'",
            "results": ["Marie Curie", "Pierre Curie", "Curie (unit)"],
        }
    )
    monkeypatch.setattr(
        "jeanmichel.dispatcher._wiki_search_handler",
        lambda **kwargs: fake_wiki_json,
    )

    decision = DispatchDecision(
        intent="alexa", tool="wikipedia_search", args={"query": "Marie Curie"}
    )
    mock = MockClient(
        script=[LLMResponse(thinking="", content="Marie Curie was a physicist.")]
    )
    result = execute_alexa(decision, mock, user_lang="en")
    assert "Marie Curie" in result
    # LLM was called for wikipedia even though user_lang=en.
    assert len(mock.calls_v2) == 1


def test_execute_alexa_wikipedia_french(monkeypatch):
    fake_wiki_json = json.dumps(
        {
            "summary": "Found articles for 'Paris'",
            "results": ["Paris", "Paris (France)"],
        }
    )
    monkeypatch.setattr(
        "jeanmichel.dispatcher._wiki_search_handler",
        lambda **kwargs: fake_wiki_json,
    )

    decision = DispatchDecision(
        intent="alexa", tool="wikipedia_search", args={"query": "Paris"}
    )
    mock = MockClient(
        script=[LLMResponse(thinking="", content="Paris est la capitale de la France.")]
    )
    result = execute_alexa(decision, mock, user_lang="fr")
    assert "Paris" in result
    assert "French" in mock.calls_v2[0]["messages"][0]["content"]


# ---- execute_alexa : weather (English) — uses tool summary verbatim -----


def test_execute_alexa_weather_english_uses_summary(monkeypatch):
    fake_weather_json = json.dumps(
        {
            "summary": "Paris: 18°C, partly cloudy, wind 12 km/h.",
            "location": "Paris",
            "temperature": 18,
        }
    )
    monkeypatch.setattr(
        "jeanmichel.dispatcher._weather_handler",
        lambda **kwargs: fake_weather_json,
    )

    decision = DispatchDecision(
        intent="alexa", tool="weather", args={"location": "Paris"}
    )
    mock = MockClient(script=[])  # no LLM for en + weather
    result = execute_alexa(decision, mock, user_lang="en")
    assert "Paris" in result
    assert "18" in result
    assert len(mock.calls_v2) == 0


# ---- execute_alexa : error path ------------------------------------------


def test_execute_alexa_tool_error_english_uses_summary(monkeypatch):
    error_json = json.dumps(
        {"error": "unknown_timezone", "summary": "Unknown timezone: Mars/Olympus"}
    )
    monkeypatch.setattr(
        "jeanmichel.dispatcher._clock_handler",
        lambda **kwargs: error_json,
    )

    decision = DispatchDecision(
        intent="alexa", tool="clock", args={"timezone": "Mars/Olympus"}
    )
    mock = MockClient(script=[])
    result = execute_alexa(decision, mock, user_lang="en")
    assert "Unknown timezone" in result
    assert len(mock.calls_v2) == 0


def test_execute_alexa_tool_handler_raises(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr("jeanmichel.dispatcher._clock_handler", boom)

    decision = DispatchDecision(intent="alexa", tool="clock", args={})
    mock = MockClient(script=[])
    result = execute_alexa(decision, mock, user_lang="en")
    assert "Error" in result
    assert "network down" in result


# ---- execute_alexa : guard rails -----------------------------------------


def test_execute_alexa_rejects_deep_decision():
    decision = DispatchDecision(intent="deep", tool=None, args={})
    mock = MockClient(script=[])
    with pytest.raises(ValueError, match="intent='deep'"):
        execute_alexa(decision, mock)


def test_execute_alexa_rejects_unknown_tool():
    decision = DispatchDecision(intent="alexa", tool="not_a_tool", args={})
    mock = MockClient(script=[])
    with pytest.raises(ValueError, match="Unknown ALEXA tool"):
        execute_alexa(decision, mock)


# ---- End-to-end : "quelle heure est-il ?" path ---------------------------


def test_e2e_french_time_question_routes_to_clock_and_formats_french():
    """DoD : a French time question goes alexa → clock → French reply, end-to-end via mocks."""
    mock = MockClient(
        script=[
            # Step 1 : dispatcher classify
            LLMResponse(thinking="", content='{"intent":"alexa","tool":"clock","args":{}}'),
            # Step 2 : formatter LLM (because user_lang=fr)
            LLMResponse(thinking="", content="Il est 03 h 49 (heure de Paris)."),
        ]
    )

    user_text = "Quelle heure est-il ?"
    decision = classify(user_text, mock)
    assert decision.intent == "alexa"
    assert decision.tool == "clock"

    user_lang = detect_language(user_text)
    assert user_lang == "fr"

    answer = execute_alexa(decision, mock, user_lang=user_lang)
    assert "Il est" in answer
    # Total : 1 classify call + 1 formatter call.
    assert len(mock.calls_v2) == 2


# ---- _ALEXA_TOOLS is stable ----------------------------------------------


def test_alexa_tools_set_matches_doc_spec():
    """The ALEXA tool set is exactly what §3 doc 06 declares — no drift."""
    assert {"clock", "weather", "wikipedia_search"} == _ALEXA_TOOLS


# ---- profile-driven default location for clock --------------------------


def test_execute_alexa_clock_injects_profile_location_when_no_args(monkeypatch):
    """Bare 'quelle heure est-il ?' → clock with no args → uses profile city/country."""
    from jeanmichel.config import UserProfile

    captured: dict = {}

    def fake_clock_handler(**kwargs):
        captured.update(kwargs)
        return json.dumps({"summary": "ok", "utc": "x", "local": "y", "timezone": "Europe/Paris"})

    monkeypatch.setattr("jeanmichel.dispatcher._clock_handler", fake_clock_handler)

    profile = UserProfile(name="Jeremy", city="Montréal", country="Canada")
    decision = DispatchDecision(intent="alexa", tool="clock", args={})
    mock = MockClient(script=[])

    execute_alexa(decision, mock, user_lang="en", user_profile=profile)

    assert captured.get("location") == "Montréal, Canada"


def test_execute_alexa_clock_does_not_override_explicit_location(monkeypatch):
    """If the LLM gave a location, profile-injection must NOT overwrite it."""
    from jeanmichel.config import UserProfile

    captured: dict = {}
    monkeypatch.setattr(
        "jeanmichel.dispatcher._clock_handler",
        lambda **k: (captured.update(k), '{"summary":"ok"}')[1],
    )

    profile = UserProfile(city="Montréal", country="Canada")
    decision = DispatchDecision(
        intent="alexa", tool="clock", args={"location": "Tokyo, Japan"}
    )
    mock = MockClient(script=[])

    execute_alexa(decision, mock, user_lang="en", user_profile=profile)

    assert captured.get("location") == "Tokyo, Japan"


def test_execute_alexa_clock_no_profile_no_args_falls_through_to_utc(monkeypatch):
    """Without a profile and no args, clock falls through to UTC."""
    captured: dict = {}
    monkeypatch.setattr(
        "jeanmichel.dispatcher._clock_handler",
        lambda **k: (captured.update(k), '{"summary":"ok"}')[1],
    )

    decision = DispatchDecision(intent="alexa", tool="clock", args={})
    mock = MockClient(script=[])

    execute_alexa(decision, mock, user_lang="en", user_profile=None)

    # No location injected — clock will use UTC by default.
    assert captured.get("location") is None
    assert captured.get("timezone") is None


def test_execute_alexa_clock_partial_profile_uses_what_it_has(monkeypatch):
    """Profile with only `city` (no country) still injects the city."""
    from jeanmichel.config import UserProfile

    captured: dict = {}
    monkeypatch.setattr(
        "jeanmichel.dispatcher._clock_handler",
        lambda **k: (captured.update(k), '{"summary":"ok"}')[1],
    )

    profile = UserProfile(city="Tokyo")
    decision = DispatchDecision(intent="alexa", tool="clock", args={})

    execute_alexa(decision, MockClient(script=[]), user_lang="en", user_profile=profile)

    assert captured.get("location") == "Tokyo"


# ---- profile-driven default location for weather (mirror of clock cases) ----


def test_execute_alexa_weather_injects_profile_location_when_no_args(monkeypatch):
    """Bare 'quelle est la météo ?' → weather with no args → uses profile city/country."""
    from jeanmichel.config import UserProfile

    captured: dict = {}

    def fake_weather_handler(**kwargs):
        captured.update(kwargs)
        return json.dumps({"summary": "Montréal: 12°C", "current": {}, "wmo_descriptions": {}})

    monkeypatch.setattr("jeanmichel.dispatcher._weather_handler", fake_weather_handler)

    profile = UserProfile(name="Jeremy", city="Montréal", country="Canada")
    decision = DispatchDecision(intent="alexa", tool="weather", args={})
    mock = MockClient(script=[])

    execute_alexa(decision, mock, user_lang="fr", user_profile=profile)

    assert captured.get("location") == "Montréal, Canada"


def test_execute_alexa_weather_does_not_override_explicit_location(monkeypatch):
    """If the LLM gave a location, profile-injection must NOT overwrite it."""
    from jeanmichel.config import UserProfile

    captured: dict = {}
    monkeypatch.setattr(
        "jeanmichel.dispatcher._weather_handler",
        lambda **k: (captured.update(k), '{"summary":"ok"}')[1],
    )

    profile = UserProfile(city="Montréal", country="Canada")
    decision = DispatchDecision(
        intent="alexa", tool="weather", args={"location": "Tokyo, Japan"}
    )

    execute_alexa(decision, MockClient(script=[]), user_lang="en", user_profile=profile)

    assert captured.get("location") == "Tokyo, Japan"


def test_execute_alexa_weather_no_profile_no_args_no_injection(monkeypatch):
    """Without a profile and no args, weather receives no location (handler will error)."""
    captured: dict = {}
    monkeypatch.setattr(
        "jeanmichel.dispatcher._weather_handler",
        lambda **k: (captured.update(k), '{"summary":"ok"}')[1],
    )

    decision = DispatchDecision(intent="alexa", tool="weather", args={})

    execute_alexa(decision, MockClient(script=[]), user_lang="en", user_profile=None)

    assert captured.get("location") is None


def test_execute_alexa_weather_partial_profile_uses_what_it_has(monkeypatch):
    """Profile with only `city` (no country) still injects the city."""
    from jeanmichel.config import UserProfile

    captured: dict = {}
    monkeypatch.setattr(
        "jeanmichel.dispatcher._weather_handler",
        lambda **k: (captured.update(k), '{"summary":"ok"}')[1],
    )

    profile = UserProfile(city="Reykjavík")
    decision = DispatchDecision(intent="alexa", tool="weather", args={})

    execute_alexa(decision, MockClient(script=[]), user_lang="en", user_profile=profile)

    assert captured.get("location") == "Reykjavík"
