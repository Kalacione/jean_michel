"""Tests for the v2 LLM client API (chat_messages + MockClient extension)."""

from __future__ import annotations

import pytest

from jeanmichel.llm import MockClient
from jeanmichel.models import LLMResponse


@pytest.fixture(autouse=True)
def _clear_no_thinking_cache():
    """`_NO_THINKING_MODELS` is module-global; clear it around each test to avoid leakage."""
    from jeanmichel import llm
    llm._NO_THINKING_MODELS.clear()
    yield
    llm._NO_THINKING_MODELS.clear()


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


# ---- streamed OllamaClient : accumulation, num_ctx/keep_alive, eviction, watchdog --


def _text_chunk(content="", thinking="", tool_calls=None, done=False, pe=0, ev=0):
    """Build one streamed ChatResponse-shaped chunk (dict form)."""
    msg = {"role": "assistant", "content": content}
    if thinking:
        msg["thinking"] = thinking
    if tool_calls:
        msg["tool_calls"] = tool_calls
    chunk = {"message": msg, "done": done}
    if pe:
        chunk["prompt_eval_count"] = pe
    if ev:
        chunk["eval_count"] = ev
    return chunk


class _StreamFake:
    """Fake ollama Client whose chat() returns an ITERATOR of chunks (the client now
    streams). Records chat() kwargs and generate() (= unload) models. `raise_on_think`
    simulates a coder model rejecting `think`; `make_gen` overrides the chunk stream."""

    def __init__(self, chunks=None, raise_on_think=False, make_gen=None):
        self.chunks = chunks if chunks is not None else [_text_chunk("ok", done=True, ev=1)]
        self.raise_on_think = raise_on_think
        self.make_gen = make_gen
        self.chats: list[dict] = []
        self.unloads: list[str] = []

    def chat(self, **kwargs):
        self.chats.append(kwargs)
        if self.raise_on_think and kwargs.get("think"):
            raise RuntimeError('"x" does not support thinking (status code: 400)')
        if self.make_gen is not None:
            return self.make_gen()
        return iter(list(self.chunks))

    def generate(self, **kwargs):  # OllamaClient.unload → generate(keep_alive=0)
        self.unloads.append(kwargs.get("model"))
        return {"response": ""}


def _bare_ollama_client(fake):
    """Build an OllamaClient without touching ollama (bypass __init__)."""
    from jeanmichel.llm import OllamaClient
    c = OllamaClient.__new__(OllamaClient)
    c.model = "qwen3-coder:latest"
    c._client = fake
    c._last_model = None
    return c


def test_chat_messages_accumulates_streamed_chunks():
    """Streamed deltas (content + tool_calls + usage) fold into one LLMResponse."""
    fake = _StreamFake(chunks=[
        _text_chunk("Hel"),
        _text_chunk("lo"),
        _text_chunk("", tool_calls=[{"function": {"name": "todo_write", "arguments": {"goal": "g"}}}]),
        _text_chunk("", done=True, pe=12, ev=5),
    ])
    client = _bare_ollama_client(fake)
    resp = client.chat_messages(messages=[{"role": "user", "content": "hi"}], tools=[],
                                temperature=0.0, thinking=False)
    assert resp.content == "Hello"
    assert [tc.name for tc in resp.tool_calls] == ["todo_write"]
    assert resp.tool_calls[0].arguments == {"goal": "g"}
    assert resp.eval_count == 5 and resp.prompt_eval_count == 12


def test_chat_messages_streams_both_channels_tagged():
    """on_token receives (delta, channel) — thinking and content on SEPARATE channels
    so the GUI can render them in different places (block vs bubble)."""
    fake = _StreamFake(chunks=[
        _text_chunk("", thinking="hmm "),
        _text_chunk("Hel"),
        _text_chunk("lo", done=True),
    ])
    client = _bare_ollama_client(fake)
    seen: list[tuple[str, str]] = []
    resp = client.chat_messages(messages=[], tools=[], temperature=0.0, thinking=True,
                                on_token=lambda d, ch: seen.append((ch, d)))
    thinking = "".join(d for ch, d in seen if ch == "thinking")
    content = "".join(d for ch, d in seen if ch == "content")
    assert thinking == "hmm " and content == "Hello"
    assert resp.content == "Hello" and resp.thinking == "hmm "  # both accumulated


def test_chat_messages_retries_without_thinking_on_400():
    fake = _StreamFake(raise_on_think=True)
    client = _bare_ollama_client(fake)
    resp = client.chat_messages(
        messages=[{"role": "user", "content": "hi"}], tools=[], temperature=0.1, thinking=True,
    )
    assert resp.content == "ok"
    assert len(fake.chats) == 2          # first (with think) failed → retried
    assert "think" in fake.chats[0]      # first attempt requested thinking
    assert "think" not in fake.chats[1]  # retry dropped it


def test_chat_messages_caches_no_thinking_model():
    """After a 400, the model is remembered so we don't think+400+retry every call."""
    fake = _StreamFake(raise_on_think=True)
    client = _bare_ollama_client(fake)
    # 1st call : think → 400 → retry (2 chat() calls), model cached.
    client.chat_messages(messages=[], tools=[], temperature=0.0, thinking=True)
    assert len(fake.chats) == 2
    # 2nd call : think requested again, but cache skips it → single clean call, no 400.
    client.chat_messages(messages=[], tools=[], temperature=0.0, thinking=True)
    assert len(fake.chats) == 3          # only ONE extra call, not two
    assert "think" not in fake.chats[2]  # thinking skipped from the start


def test_chat_messages_other_errors_propagate():
    import pytest
    client = _bare_ollama_client(_StreamFake(make_gen=lambda: (_ for _ in ()).throw(
        RuntimeError("connection refused"))))
    with pytest.raises(RuntimeError, match="connection refused"):
        client.chat_messages(messages=[], tools=[], temperature=0.0, thinking=True)


def test_chat_messages_sets_num_ctx_to_budget_and_keep_alive():
    """num_ctx pinned to model_context_window (Ollama otherwise auto-sizes by VRAM →
    256K → 45 GB coder) ; keep_alive sent ; stream=True."""
    from jeanmichel.config import model_context_window
    fake = _StreamFake()
    client = _bare_ollama_client(fake)
    client.chat_messages(messages=[{"role": "user", "content": "hi"}], tools=[],
                         temperature=0.0, thinking=False, model="qwen3-coder:latest")
    sent = fake.chats[0]
    assert sent["options"]["num_ctx"] == model_context_window("qwen3-coder:latest")
    assert sent["keep_alive"] and sent["stream"] is True


def test_chat_messages_evicts_previous_model_only_on_switch():
    """Single-model-in-VRAM : unload the previous model only when switching to a
    DIFFERENT one. Sequence m1,m1,m2,m1 → unloads m1 (→m2) then m2 (→m1)."""
    fake = _StreamFake()
    client = _bare_ollama_client(fake)
    for m in ("m1", "m1", "m2", "m1"):
        client.chat_messages(messages=[], tools=[], temperature=0.0, thinking=False, model=m)
    assert fake.unloads == ["m1", "m2"]


def test_stream_stall_aborts_and_unloads(monkeypatch):
    """No token for LLM_STALL_TIMEOUT → LLMTimeoutError + best-effort unload (the
    watchdog : a hung model is caught without guessing a total timeout)."""
    import time

    import pytest

    from jeanmichel import llm
    monkeypatch.setattr(llm, "LLM_STALL_TIMEOUT_SECONDS", 0.3)

    def gen():
        yield _text_chunk("partial ")
        time.sleep(0.6)  # > stall timeout → queue.get times out
        yield _text_chunk("never read", done=True)

    fake = _StreamFake(make_gen=gen)
    client = _bare_ollama_client(fake)
    with pytest.raises(llm.LLMTimeoutError, match="stalled"):
        client.chat_messages(messages=[], tools=[], temperature=0.0, thinking=False, model="m1")
    assert "m1" in fake.unloads  # GPU-free requested


def test_stream_hard_cap_aborts(monkeypatch):
    """A call that keeps trickling past the hard cap is bounded (backstop)."""
    import time

    import pytest

    from jeanmichel import llm
    monkeypatch.setattr(llm, "LLM_STALL_TIMEOUT_SECONDS", 5)   # stall must NOT fire
    monkeypatch.setattr(llm, "LLM_CALL_TIMEOUT_SECONDS", 0.3)  # cap fires

    def gen():
        for _ in range(12):
            time.sleep(0.05)
            yield _text_chunk("x")

    fake = _StreamFake(make_gen=gen)
    client = _bare_ollama_client(fake)
    with pytest.raises(llm.LLMTimeoutError, match="hard cap"):
        client.chat_messages(messages=[], tools=[], temperature=0.0, thinking=False, model="m1")
    assert "m1" in fake.unloads


def test_stream_dumps_slop_to_conversation_folder(tmp_path):
    """The streamed text is teed to <conv>/llm_streams/*.txt for later debugging."""
    fake = _StreamFake(chunks=[_text_chunk("hello "), _text_chunk("world", done=True)])
    client = _bare_ollama_client(fake)
    client.chat_messages(messages=[], tools=[], temperature=0.0, thinking=False,
                         model="code-runner", stream_log_dir=tmp_path, stream_log_label="code-runner")
    files = list((tmp_path / "llm_streams").glob("*.txt"))
    assert len(files) == 1
    assert "code-runner" in files[0].name
    assert files[0].read_text(encoding="utf-8") == "hello world"


def test_mock_client_mirrors_eviction_on_switch():
    """MockClient applies the same eviction policy so integration tests see it."""
    mock = MockClient(script=[LLMResponse(thinking="", content="x") for _ in range(4)])
    for m in ("A", "A", "B", "A"):
        mock.chat_messages(messages=[], tools=[], temperature=0.0, thinking=False, model=m)
    assert mock.unloaded == ["A", "B"]
