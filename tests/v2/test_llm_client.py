"""Tests for the v2 LLM client API (chat_messages + MockClient extension)."""

from __future__ import annotations

import pytest

from jeanmichel.llm import MockClient
from jeanmichel.models import LLMResponse

# ---- chat_messages basic behavior ----------------------------------------


def test_mock_chat_messages_returns_scripted_response():
    scripted = LLMResponse(thinking="thinking", content="hello", tool_calls=[])
    mock = MockClient(script=[scripted])

    resp = mock.chat_messages(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        temperature=0.2,
        thinking=True,
    )

    assert resp.content == "hello"
    assert resp.thinking == "thinking"


def test_mock_chat_messages_records_call_args():
    mock = MockClient(script=[LLMResponse(thinking="", content="ok")])

    mock.chat_messages(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"function": {"name": "clock"}}],
        temperature=0.0,
        thinking=False,
        model="granite4.1:8b",
        format="json",
    )

    assert len(mock.calls_v2) == 1
    call = mock.calls_v2[0]
    assert call["messages"] == [{"role": "user", "content": "hi"}]
    assert call["model"] == "granite4.1:8b"
    assert call["format"] == "json"
    assert call["temperature"] == 0.0
    assert call["thinking"] is False
    assert call["tools"] == [{"function": {"name": "clock"}}]


def test_mock_chat_messages_pops_script_in_order():
    mock = MockClient(
        script=[
            LLMResponse(thinking="", content="first"),
            LLMResponse(thinking="", content="second"),
            LLMResponse(thinking="", content="third"),
        ]
    )

    r1 = mock.chat_messages(messages=[], tools=[], temperature=0.0, thinking=False)
    r2 = mock.chat_messages(messages=[], tools=[], temperature=0.0, thinking=False)
    r3 = mock.chat_messages(messages=[], tools=[], temperature=0.0, thinking=False)

    assert (r1.content, r2.content, r3.content) == ("first", "second", "third")


def test_mock_chat_messages_exhausted_script_raises():
    mock = MockClient(script=[])
    with pytest.raises(RuntimeError, match="script exhausted"):
        mock.chat_messages(messages=[], tools=[], temperature=0.0, thinking=False)


# ---- Backward compat with legacy chat() ----------------------------------


def test_mock_chat_legacy_still_works():
    mock = MockClient(script=[LLMResponse(thinking="", content="legacy")])

    resp = mock.chat(system="sys", user="usr", tools=[], temperature=0.5, thinking=True)

    assert resp.content == "legacy"
    assert mock.calls == [("sys", "usr")]
    assert mock.calls_v2 == []  # legacy call doesn't fill v2 record


def test_mock_chat_and_chat_messages_share_script_queue():
    mock = MockClient(
        script=[
            LLMResponse(thinking="", content="legacy_first"),
            LLMResponse(thinking="", content="v2_second"),
        ]
    )

    r1 = mock.chat(system="s", user="u", tools=[], temperature=0.0, thinking=False)
    r2 = mock.chat_messages(messages=[], tools=[], temperature=0.0, thinking=False)

    assert r1.content == "legacy_first"
    assert r2.content == "v2_second"


# ---- Corruption retry path -----------------------------------------------


def test_chat_messages_retries_on_corrupted_output():
    # First response has a corruption marker; second is clean.
    corrupted = LLMResponse(thinking="", content="oh no <|im_end|> oops")
    clean = LLMResponse(thinking="", content="clean")
    mock = MockClient(script=[corrupted, clean])

    resp = mock.chat_messages(messages=[], tools=[], temperature=0.0, thinking=False)

    # Mock retries by popping the second item — final result is the clean one.
    assert resp.content == "clean"
    assert resp.corrupted is False
    # Both items were consumed from the script.
    assert len(mock.script) == 0


def test_chat_messages_marks_corrupted_after_double_failure():
    bad1 = LLMResponse(thinking="", content="<|im_end|>")
    bad2 = LLMResponse(thinking="", content="<|im_end|>")
    mock = MockClient(script=[bad1, bad2])

    resp = mock.chat_messages(messages=[], tools=[], temperature=0.0, thinking=False)

    assert resp.corrupted is True


# ---- v2 LLMResponse token usage fields ----------------------------------


def test_llm_response_has_token_usage_fields_defaulting_to_zero():
    resp = LLMResponse(thinking="", content="hi")
    assert resp.prompt_eval_count == 0
    assert resp.eval_count == 0


def test_llm_response_accepts_token_usage_fields():
    resp = LLMResponse(
        thinking="", content="hi", prompt_eval_count=100, eval_count=42
    )
    assert resp.prompt_eval_count == 100
    assert resp.eval_count == 42


# ---- Thinking-unsupported fallback (R5) ----------------------------------


class _FakeOllamaChat:
    """Fake ollama Client: raises 'does not support thinking' when `think` is
    passed, succeeds otherwise. Records each call's kwargs."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if "think" in kwargs:
            raise RuntimeError(
                '"qwen3-coder:latest" does not support thinking (status code: 400)'
            )
        return {"message": {"role": "assistant", "content": "ok"}}


def _bare_ollama_client(fake):
    """Build an OllamaClient without touching ollama (bypass __init__)."""
    from concurrent.futures import ThreadPoolExecutor

    from jeanmichel.llm import OllamaClient
    c = OllamaClient.__new__(OllamaClient)
    c.model = "qwen3-coder:latest"
    c._client = fake
    c._last_model = None
    c._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-ollama")
    return c


def test_chat_messages_retries_without_thinking_on_400():
    fake = _FakeOllamaChat()
    client = _bare_ollama_client(fake)
    resp = client.chat_messages(
        messages=[{"role": "user", "content": "hi"}],
        tools=[], temperature=0.1, thinking=True,
    )
    assert resp.content == "ok"
    assert len(fake.calls) == 2          # first (with think) failed → retried
    assert "think" in fake.calls[0]      # first attempt requested thinking
    assert "think" not in fake.calls[1]  # retry dropped it


def test_chat_messages_other_errors_propagate():
    import pytest

    class _Boom:
        def chat(self, **kwargs):
            raise RuntimeError("connection refused")

    client = _bare_ollama_client(_Boom())
    with pytest.raises(RuntimeError, match="connection refused"):
        client.chat_messages(messages=[], tools=[], temperature=0.0, thinking=True)


# ---- num_ctx + keep_alive + single-model eviction (VRAM firefight) --------


class _RecordingOllama:
    """Fake ollama Client recording chat() kwargs and generate() (= unload) models."""

    def __init__(self) -> None:
        self.chats: list[dict] = []
        self.unloads: list[str] = []

    def chat(self, **kwargs):
        self.chats.append(kwargs)
        return {"message": {"role": "assistant", "content": "ok"}}

    def generate(self, **kwargs):  # OllamaClient.unload calls generate(keep_alive=0)
        self.unloads.append(kwargs.get("model"))
        return {"response": ""}


def test_chat_messages_sets_num_ctx_to_budget_and_keep_alive():
    """num_ctx is pinned to model_context_window (Ollama otherwise auto-sizes context
    by VRAM → 256K → 45 GB coder), and keep_alive is sent (Fix A + config knob)."""
    from jeanmichel.config import model_context_window

    fake = _RecordingOllama()
    client = _bare_ollama_client(fake)
    client.chat_messages(
        messages=[{"role": "user", "content": "hi"}], tools=[], temperature=0.0,
        thinking=False, model="qwen3-coder:latest",
    )
    opts = fake.chats[0]["options"]
    assert opts["num_ctx"] == model_context_window("qwen3-coder:latest")
    assert fake.chats[0]["keep_alive"]  # set from OLLAMA_KEEP_ALIVE


def test_chat_messages_evicts_previous_model_only_on_switch():
    """Single-model-in-VRAM : unload the previous model only when switching to a
    DIFFERENT one (not when re-using the same in a row). Sequence m1,m1,m2,m1 →
    unloads m1 (→m2) then m2 (→m1)."""
    fake = _RecordingOllama()
    client = _bare_ollama_client(fake)
    for m in ("m1", "m1", "m2", "m1"):
        client.chat_messages(messages=[], tools=[], temperature=0.0, thinking=False, model=m)
    assert fake.unloads == ["m1", "m2"]


def test_mock_client_mirrors_eviction_on_switch():
    """MockClient applies the same eviction policy so integration tests see it."""
    mock = MockClient(script=[LLMResponse(thinking="", content="x") for _ in range(4)])
    for m in ("A", "A", "B", "A"):
        mock.chat_messages(messages=[], tools=[], temperature=0.0, thinking=False, model=m)
    assert mock.unloaded == ["A", "B"]
