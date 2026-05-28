"""Tests for the vocal-mode TTS pipeline.

We mock the Piper API and the audio player subprocess so the tests stay
offline and silent (no speakers needed in CI).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jeanmichel import voice


@pytest.fixture(autouse=True)
def reset_voice_singleton():
    """Make sure each test starts with a clean PiperVoice cache."""
    voice._voice_singleton = None
    yield
    voice._voice_singleton = None


# ---- _load_voice --------------------------------------------------------


def test_load_voice_missing_model_returns_none(tmp_path, monkeypatch):
    fake_model = tmp_path / "nope.onnx"
    monkeypatch.setattr(voice.config, "VOICE_MODEL_PATH", fake_model)
    assert voice._load_voice() is None


def test_load_voice_caches_singleton(tmp_path, monkeypatch):
    fake_model = tmp_path / "fake.onnx"
    fake_model.write_bytes(b"x")
    monkeypatch.setattr(voice.config, "VOICE_MODEL_PATH", fake_model)

    fake_voice = MagicMock()
    fake_piper_voice_cls = MagicMock()
    fake_piper_voice_cls.load.return_value = fake_voice

    with patch.dict("sys.modules", {"piper": MagicMock(PiperVoice=fake_piper_voice_cls)}):
        v1 = voice._load_voice()
        v2 = voice._load_voice()

    assert v1 is fake_voice
    assert v2 is fake_voice
    # Loaded only once thanks to singleton caching
    fake_piper_voice_cls.load.assert_called_once()


# ---- _resolve_player ----------------------------------------------------


def test_resolve_player_uses_forced_command_when_present(monkeypatch):
    monkeypatch.setattr(voice.config, "VOICE_AUDIO_PLAYER", "myplayer")
    monkeypatch.setattr(voice.shutil, "which", lambda c: f"/usr/bin/{c}" if c == "myplayer" else None)
    assert voice._resolve_player() == ("myplayer", [])


def test_resolve_player_forced_command_missing_returns_none(monkeypatch):
    monkeypatch.setattr(voice.config, "VOICE_AUDIO_PLAYER", "doesnotexist")
    monkeypatch.setattr(voice.shutil, "which", lambda c: None)
    assert voice._resolve_player() is None


def test_resolve_player_auto_picks_first_available(monkeypatch):
    monkeypatch.setattr(voice.config, "VOICE_AUDIO_PLAYER", "")
    # Only aplay is present
    monkeypatch.setattr(
        voice.shutil, "which",
        lambda c: f"/usr/bin/{c}" if c == "aplay" else None,
    )
    cmd, args = voice._resolve_player()
    assert cmd == "aplay"
    assert "-q" in args


def test_resolve_player_no_player_available(monkeypatch):
    monkeypatch.setattr(voice.config, "VOICE_AUDIO_PLAYER", "")
    monkeypatch.setattr(voice.shutil, "which", lambda c: None)
    assert voice._resolve_player() is None


# ---- synthesize_to_wav --------------------------------------------------


def test_synthesize_returns_false_when_no_voice(tmp_path, monkeypatch):
    monkeypatch.setattr(voice.config, "VOICE_MODEL_PATH", tmp_path / "nope.onnx")
    out = tmp_path / "x.wav"
    assert voice.synthesize_to_wav("hello", out) is False


def _fake_chunk_iter(text):
    """Stand-in for PiperVoice.synthesize(text) : one short 16-bit-PCM chunk."""
    yield _FakeAudioChunk(
        audio_int16_bytes=b"\x00\x00" * 1102,  # ~50 ms of silence
        sample_rate=22050,
        sample_channels=1,
        sample_width=2,
    )


class _FakeAudioChunk:
    def __init__(self, audio_int16_bytes, sample_rate, sample_channels, sample_width):
        self.audio_int16_bytes = audio_int16_bytes
        self.sample_rate = sample_rate
        self.sample_channels = sample_channels
        self.sample_width = sample_width


def test_synthesize_returns_true_on_success(tmp_path, monkeypatch):
    fake_model = tmp_path / "ok.onnx"
    fake_model.write_bytes(b"x")
    monkeypatch.setattr(voice.config, "VOICE_MODEL_PATH", fake_model)

    fake_voice = MagicMock(synthesize=_fake_chunk_iter)
    fake_piper_voice_cls = MagicMock(load=MagicMock(return_value=fake_voice))
    out = tmp_path / "out.wav"

    with patch.dict("sys.modules", {"piper": MagicMock(PiperVoice=fake_piper_voice_cls)}):
        ok = voice.synthesize_to_wav("salut", out)

    assert ok is True
    assert out.exists() and out.stat().st_size > 0


def test_synthesize_swallows_runtime_errors(tmp_path, monkeypatch):
    fake_model = tmp_path / "ok.onnx"
    fake_model.write_bytes(b"x")
    monkeypatch.setattr(voice.config, "VOICE_MODEL_PATH", fake_model)

    fake_voice = MagicMock(synthesize_wav=MagicMock(side_effect=RuntimeError("boom")))
    fake_piper_voice_cls = MagicMock(load=MagicMock(return_value=fake_voice))

    with patch.dict("sys.modules", {"piper": MagicMock(PiperVoice=fake_piper_voice_cls)}):
        ok = voice.synthesize_to_wav("salut", tmp_path / "x.wav")
    assert ok is False


# ---- play_wav -----------------------------------------------------------


def test_play_wav_returns_false_when_no_player(tmp_path, monkeypatch):
    monkeypatch.setattr(voice.config, "VOICE_AUDIO_PLAYER", "")
    monkeypatch.setattr(voice.shutil, "which", lambda c: None)
    assert voice.play_wav(tmp_path / "x.wav") is False


def test_play_wav_invokes_player_with_wav(tmp_path, monkeypatch):
    monkeypatch.setattr(voice.config, "VOICE_AUDIO_PLAYER", "")
    monkeypatch.setattr(
        voice.shutil, "which",
        lambda c: f"/usr/bin/{c}" if c == "paplay" else None,
    )
    captured: list = []
    monkeypatch.setattr(
        voice.subprocess, "run",
        lambda *a, **kw: captured.append((a, kw)) or MagicMock(returncode=0),
    )
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"RIFF")
    ok = voice.play_wav(wav)
    assert ok is True
    assert captured
    cmd_argv = captured[0][0][0]
    assert cmd_argv[0] == "paplay"
    assert cmd_argv[-1] == str(wav)


def test_play_wav_handles_timeout(tmp_path, monkeypatch):
    import subprocess as sp

    monkeypatch.setattr(voice.config, "VOICE_AUDIO_PLAYER", "")
    monkeypatch.setattr(voice.shutil, "which", lambda c: "/usr/bin/aplay" if c == "aplay" else None)

    def _raise(*a, **kw):
        raise sp.TimeoutExpired(cmd=a, timeout=60)

    monkeypatch.setattr(voice.subprocess, "run", _raise)
    assert voice.play_wav(tmp_path / "x.wav") is False


# ---- speak --------------------------------------------------------------


def test_speak_empty_text_is_noop():
    assert voice.speak("") is True
    assert voice.speak("   ") is True


class _FakeChunk:
    """Stand-in for piper.AudioChunk : just needs `audio_int16_bytes`."""
    def __init__(self, n: int):
        self.audio_int16_bytes = b"\x00\x00" * n


class _FakePopen:
    """Stand-in for subprocess.Popen for streaming tests."""
    def __init__(self, *args, **kwargs):
        self.stdin = MagicMock()
        self.returncode = 0
        self.written: list[bytes] = []
        self.stdin.write = lambda b: self.written.append(b)

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        pass


def test_speak_streams_raw_pcm_to_player(tmp_path, monkeypatch):
    """speak() should pipe AudioChunks directly into the player's stdin."""
    fake_model = tmp_path / "ok.onnx"
    fake_model.write_bytes(b"x")
    monkeypatch.setattr(voice.config, "VOICE_MODEL_PATH", fake_model)
    monkeypatch.setattr(voice.config, "VOICE_AUDIO_PLAYER", "")
    # paplay is available, others are not — we expect the paplay raw command.
    monkeypatch.setattr(
        voice.shutil, "which",
        lambda c: "/usr/bin/paplay" if c == "paplay" else None,
    )

    chunks = [_FakeChunk(1000), _FakeChunk(500)]
    fake_voice = MagicMock(
        config=MagicMock(sample_rate=22050),
        synthesize=MagicMock(return_value=iter(chunks)),
    )
    fake_piper_voice_cls = MagicMock(load=MagicMock(return_value=fake_voice))

    captured_cmd: list = []
    def _track_popen(cmd, *args, **kwargs):
        captured_cmd.append(cmd)
        return _FakePopen()
    monkeypatch.setattr(voice.subprocess, "Popen", _track_popen)

    with patch.dict("sys.modules", {"piper": MagicMock(PiperVoice=fake_piper_voice_cls)}):
        ok = voice.speak("hello world")

    assert ok is True
    assert captured_cmd, "Popen should have been invoked once"
    cmd = captured_cmd[0]
    assert cmd[0] == "/usr/bin/paplay"
    assert "--raw" in cmd
    assert "--rate=22050" in cmd
    assert "--channels=1" in cmd


def test_speak_falls_back_to_wav_when_no_streaming_player(tmp_path, monkeypatch):
    """When no known streaming player is found, speak() uses the WAV fallback."""
    fake_model = tmp_path / "ok.onnx"
    fake_model.write_bytes(b"x")
    monkeypatch.setattr(voice.config, "VOICE_MODEL_PATH", fake_model)
    monkeypatch.setattr(voice.config, "VOICE_AUDIO_PLAYER", "")
    # No streaming-capable player found.
    monkeypatch.setattr(voice.shutil, "which", lambda c: None)

    fake_voice = MagicMock(
        config=MagicMock(sample_rate=22050),
        synthesize=_fake_chunk_iter,
    )
    fake_piper_voice_cls = MagicMock(load=MagicMock(return_value=fake_voice))

    with patch.dict("sys.modules", {"piper": MagicMock(PiperVoice=fake_piper_voice_cls)}):
        ok = voice.speak("hi")

    # No streaming player AND no wav player either → fallback returns False.
    assert ok is False


def test_speak_returns_false_when_no_model(tmp_path, monkeypatch):
    """No model file → speak() exits cleanly with False, no exception."""
    monkeypatch.setattr(voice.config, "VOICE_MODEL_PATH", tmp_path / "nope.onnx")
    ok = voice.speak("hi")
    assert ok is False


# ---- is_available -------------------------------------------------------


def test_is_available_true(tmp_path, monkeypatch):
    fake_model = tmp_path / "ok.onnx"
    fake_model.write_bytes(b"x")
    monkeypatch.setattr(voice.config, "VOICE_MODEL_PATH", fake_model)
    monkeypatch.setattr(voice.config, "VOICE_AUDIO_PLAYER", "")
    monkeypatch.setattr(voice.shutil, "which", lambda c: "/usr/bin/paplay" if c == "paplay" else None)
    assert voice.is_available() is True


def test_is_available_false_no_model(tmp_path, monkeypatch):
    monkeypatch.setattr(voice.config, "VOICE_MODEL_PATH", tmp_path / "nope.onnx")
    monkeypatch.setattr(voice.shutil, "which", lambda c: "/usr/bin/paplay" if c == "paplay" else None)
    assert voice.is_available() is False


def test_is_available_false_no_player(tmp_path, monkeypatch):
    fake_model = tmp_path / "ok.onnx"
    fake_model.write_bytes(b"x")
    monkeypatch.setattr(voice.config, "VOICE_MODEL_PATH", fake_model)
    monkeypatch.setattr(voice.config, "VOICE_AUDIO_PLAYER", "")
    monkeypatch.setattr(voice.shutil, "which", lambda c: None)
    assert voice.is_available() is False


# ---- async announcements ------------------------------------------------


@pytest.fixture(autouse=True)
def reset_announcement():
    """Each test starts with no in-flight announcement."""
    voice._active_announcement = None
    yield
    voice._active_announcement = None


def test_speak_async_empty_text_is_noop():
    assert voice.speak_async("") is False
    assert voice.speak_async("   ") is False


def test_speak_async_skips_when_one_is_in_flight(tmp_path, monkeypatch):
    """If a previous announcement is still playing, the next one is dropped."""
    fake_proc = MagicMock(poll=MagicMock(return_value=None))  # still running
    voice._active_announcement = fake_proc

    fake_model = tmp_path / "ok.onnx"
    fake_model.write_bytes(b"x")
    monkeypatch.setattr(voice.config, "VOICE_MODEL_PATH", fake_model)
    fake_voice = MagicMock(synthesize=_fake_chunk_iter)
    fake_piper_voice_cls = MagicMock(load=MagicMock(return_value=fake_voice))

    with patch.dict("sys.modules", {"piper": MagicMock(PiperVoice=fake_piper_voice_cls)}):
        ok = voice.speak_async("salut")

    assert ok is False
    # The pending one was NOT touched
    fake_proc.kill.assert_not_called()


def test_speak_async_proceeds_when_previous_finished(tmp_path, monkeypatch):
    """A finished Popen (poll() returns non-None) should not block a new call."""
    finished_proc = MagicMock(poll=MagicMock(return_value=0))
    voice._active_announcement = finished_proc

    fake_model = tmp_path / "ok.onnx"
    fake_model.write_bytes(b"x")
    monkeypatch.setattr(voice.config, "VOICE_MODEL_PATH", fake_model)
    monkeypatch.setattr(voice.config, "VOICE_AUDIO_PLAYER", "")
    monkeypatch.setattr(voice.shutil, "which", lambda c: "/usr/bin/paplay" if c == "paplay" else None)

    fake_voice = MagicMock(synthesize=_fake_chunk_iter)
    fake_piper_voice_cls = MagicMock(load=MagicMock(return_value=fake_voice))
    new_proc = MagicMock()
    monkeypatch.setattr(voice.subprocess, "Popen", lambda *a, **kw: new_proc)

    with patch.dict("sys.modules", {"piper": MagicMock(PiperVoice=fake_piper_voice_cls)}):
        ok = voice.speak_async("salut")

    assert ok is True
    assert voice._active_announcement is new_proc


def test_announce_delegation_uses_specialised_phrase(tmp_path, monkeypatch):
    """Each known specialist has its own French filler."""
    captured: list[str] = []
    monkeypatch.setattr(voice, "speak_async", lambda text: captured.append(text) or True)
    voice.announce_delegation("wikipedia-specialist")
    voice.announce_delegation("news-specialist")
    voice.announce_delegation("unknown-agent")  # falls back to default
    assert captured[0] == "Je consulte Wikipédia."
    assert captured[1] == "Je vais voir les actualités."
    assert captured[2] == voice._DELEGATION_DEFAULT_PHRASE


def test_announce_tool_call_noop_for_unannounced(monkeypatch):
    """clock / workspace_view are too short to deserve an announcement."""
    captured: list[str] = []
    monkeypatch.setattr(voice, "speak_async", lambda text: captured.append(text) or True)
    assert voice.announce_tool_call("clock") is False
    assert voice.announce_tool_call("workspace_view") is False
    assert captured == []


def test_announce_tool_call_speaks_for_research_tools(monkeypatch):
    captured: list[str] = []
    monkeypatch.setattr(voice, "speak_async", lambda text: captured.append(text) or True)
    voice.announce_tool_call("web_search")
    voice.announce_tool_call("wikipedia_search")
    voice.announce_tool_call("news_latest")
    assert len(captured) == 3
    assert "internet" in captured[0]
    assert "Wikipédia" in captured[1]
    assert "actualités" in captured[2]


def test_wait_for_announcements_waits_then_clears():
    fake_proc = MagicMock()
    voice._active_announcement = fake_proc
    voice.wait_for_announcements(timeout=5.0)
    fake_proc.wait.assert_called_once_with(timeout=5.0)
    assert voice._active_announcement is None


def test_wait_for_announcements_noop_when_none():
    voice._active_announcement = None
    voice.wait_for_announcements()  # must not raise
