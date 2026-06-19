"""Voice input — local speech-to-text via faster-whisper (CTranslate2).

The STT twin of `voice.py` (Piper TTS) : a lazy-loaded model singleton + graceful
degradation. The model (a Whisper size name like "base"/"small", or a path) is
resolved from config and auto-downloaded ONCE to `config.STT_MODEL_DIR` ; from then
on transcription is fully local. Returns None on any failure so callers degrade to
"STT unavailable" rather than crashing — exactly like Piper for TTS.

`faster-whisper` is an OPTIONAL dependency (`pip install -e ".[audio]"`). When it's
absent the import fails and voice input is simply disabled.
"""

from __future__ import annotations

import importlib.util
import io
import logging

from . import config

_log = logging.getLogger(__name__)

_model_singleton = None  # faster_whisper.WhisperModel, lazy-loaded


def is_available() -> bool:
    """True if faster-whisper is importable (the [audio] extra is installed). Cheap :
    checks the import spec WITHOUT loading the model — lets the UI hide the mic button
    when voice input can't run."""
    return importlib.util.find_spec("faster_whisper") is not None


def _load_model():
    """Lazy-load the faster-whisper model singleton. Returns None on failure."""
    global _model_singleton
    if _model_singleton is not None:
        return _model_singleton
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        _log.warning("faster-whisper not installed (%s) — voice input disabled.", exc)
        return None
    try:
        config.STT_MODEL_DIR.mkdir(parents=True, exist_ok=True)
        _model_singleton = WhisperModel(
            config.STT_MODEL,
            device=config.STT_DEVICE,
            compute_type=config.STT_COMPUTE_TYPE,
            download_root=str(config.STT_MODEL_DIR),
        )
        return _model_singleton
    except Exception as exc:  # noqa: BLE001
        _log.warning("Failed to load STT model %r: %s", config.STT_MODEL, exc)
        return None


def transcribe(audio: bytes) -> dict[str, str] | None:
    """Transcribe in-memory audio bytes → {"text", "language"}, or None on failure.

    Accepts any container faster-whisper/PyAV can decode (webm/opus, ogg, wav…), so
    the browser's MediaRecorder blob feeds in directly. Lazy-loads the model.
    Synchronous + CPU-bound : call it OFF the event loop (a FastAPI *sync* endpoint
    runs in a threadpool, which is what `/api/stt` does)."""
    if not audio:
        return None
    model = _load_model()
    if model is None:
        return None
    try:
        segments, info = model.transcribe(io.BytesIO(audio), language=config.STT_LANGUAGE or None)
        text = "".join(seg.text for seg in segments).strip()  # consuming the generator runs inference
    except Exception as exc:  # noqa: BLE001
        _log.warning("STT transcription failed: %s", exc)
        return None
    return {"text": text, "language": getattr(info, "language", "") or ""}
