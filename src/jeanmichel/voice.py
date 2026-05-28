"""Vocal mode — synthesize text to a WAV via Piper TTS and play it back.

Used by the CLI when ``--mode vocal`` is active : after rendering the
text response in the panel, the CLI calls ``speak(text)`` which writes
a temp WAV via the Piper Python API and feeds it to the first available
audio player (paplay / aplay / ffplay).

Degrades gracefully :
  - missing model file       → warning, text-only
  - piper import fails       → warning, text-only
  - audio player not found   → warning, text-only
  - synthesis exception      → warning, text-only

Configuration :
  - ``config.VOICE_MODEL_PATH`` : `.onnx` model (env JEANMICHEL_VOICE_MODEL)
  - ``config.VOICE_AUDIO_PLAYER`` : forced command, else auto-detect
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from . import config

_log = logging.getLogger(__name__)

# Player command + args. The WAV path is appended as the last argument.
# Order matters : first available is picked. paplay > aplay > ffplay.
_PLAYER_CANDIDATES: list[tuple[str, list[str]]] = [
    ("paplay", []),
    ("aplay", ["-q"]),
    ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "error"]),
]
# Wall-clock cap on playback. A pathological infinite loop on a corrupted
# wav must not freeze the CLI — 60 s is generous for any single reply.
_PLAYBACK_TIMEOUT_S = 60


_voice_singleton = None  # PiperVoice instance, lazy-loaded


def _load_voice():
    """Lazy-load the PiperVoice singleton. Returns None on failure."""
    global _voice_singleton
    if _voice_singleton is not None:
        return _voice_singleton

    model_path = config.VOICE_MODEL_PATH
    if not model_path.is_file():
        _log.info("Voice model not found at %s — vocal mode disabled.", model_path)
        return None

    try:
        from piper import PiperVoice  # type: ignore[import-not-found]
    except ImportError as exc:
        _log.warning("piper-tts not installed (%s) — vocal mode disabled.", exc)
        return None

    try:
        _voice_singleton = PiperVoice.load(str(model_path))
        return _voice_singleton
    except Exception as exc:  # noqa: BLE001
        _log.warning("Failed to load Piper voice from %s: %s", model_path, exc)
        return None


def _resolve_player() -> tuple[str, list[str]] | None:
    """Pick the audio player to use. Returns (command, args) or None."""
    forced = config.VOICE_AUDIO_PLAYER
    if forced:
        if shutil.which(forced):
            return forced, []
        _log.warning("JEANMICHEL_AUDIO_PLAYER=%r not found on PATH.", forced)
        return None

    for cmd, args in _PLAYER_CANDIDATES:
        if shutil.which(cmd):
            return cmd, args
    return None


def synthesize_to_wav(text: str, output_path: Path) -> bool:
    """Render ``text`` to a WAV file at ``output_path``. Returns success.

    On any failure (missing model, piper import error, runtime exception)
    the function returns False without raising — callers fall back to
    text-only output.
    """
    voice = _load_voice()
    if voice is None:
        return False
    try:
        with wave.open(str(output_path), "wb") as wav_file:
            voice.synthesize_wav(text, wav_file)
        return True
    except Exception as exc:  # noqa: BLE001
        _log.warning("Piper synthesis failed: %s", exc)
        return False


def play_wav(wav_path: Path) -> bool:
    """Play a WAV file via the resolved audio player. Returns success.

    Blocks until playback completes (or the timeout fires). Errors are
    swallowed — the function never raises.
    """
    player = _resolve_player()
    if player is None:
        _log.warning("No audio player available (paplay/aplay/ffplay).")
        return False
    cmd, args = player
    try:
        subprocess.run(
            [cmd, *args, str(wav_path)],
            timeout=_PLAYBACK_TIMEOUT_S,
            capture_output=True,
            check=False,
        )
        return True
    except subprocess.TimeoutExpired:
        _log.warning("Audio playback timed out after %ds.", _PLAYBACK_TIMEOUT_S)
        return False
    except Exception as exc:  # noqa: BLE001
        _log.warning("Audio playback failed: %s", exc)
        return False


def speak(text: str) -> bool:
    """Synthesize ``text`` and play it back. Returns True on success.

    Pipeline : Piper synthesis → temp WAV → audio player. The temp file
    is removed in `finally` regardless of outcome. Empty / whitespace-only
    text is a no-op (returns True). BLOCKS until playback completes.
    """
    text = (text or "").strip()
    if not text:
        return True

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)

    try:
        if not synthesize_to_wav(text, wav_path):
            return False
        return play_wav(wav_path)
    finally:
        try:
            wav_path.unlink(missing_ok=True)
        except OSError:
            pass


# ---- async announcement helpers ------------------------------------------
#
# Used by vocal mode to play short "filler" phrases while the LLM works
# ("Je cherche sur internet…", "Je consulte Wikipédia…"). These must NOT
# block the orchestrator — the LLM keeps running while the speaker speaks.
# Strategy : Popen the audio player and track the live process. If a new
# announcement arrives while the previous one is still playing, we skip
# (instead of queueing or killing) — that's the simplest UX and avoids
# announcement pile-ups when many tool calls fire in quick succession.

_active_announcement: subprocess.Popen | None = None


def _announcement_in_progress() -> bool:
    global _active_announcement
    if _active_announcement is None:
        return False
    if _active_announcement.poll() is None:
        return True
    _active_announcement = None
    return False


def speak_async(text: str) -> bool:
    """Synthesize + play ``text`` without blocking. Skips if one is in flight.

    Returns True if a new announcement was queued for playback, False if
    skipped (busy, no model, no player, synthesis failed). The temp WAV
    is leaked on the filesystem (in /tmp), trusting the OS to GC it ; we
    can't clean it up here because we don't wait for playback to end.
    """
    global _active_announcement
    text = (text or "").strip()
    if not text:
        return False
    if _announcement_in_progress():
        return False

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)

    if not synthesize_to_wav(text, wav_path):
        wav_path.unlink(missing_ok=True)
        return False

    player = _resolve_player()
    if player is None:
        wav_path.unlink(missing_ok=True)
        return False
    cmd, args = player
    try:
        _active_announcement = subprocess.Popen(
            [cmd, *args, str(wav_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        _log.warning("speak_async Popen failed: %s", exc)
        wav_path.unlink(missing_ok=True)
        return False


def wait_for_announcements(timeout: float = 30.0) -> None:
    """Block until any in-flight announcement finishes (bounded by timeout).

    Called by the CLI just before reading the next user input so the
    speaker isn't talking over the prompt.
    """
    global _active_announcement
    if _active_announcement is None:
        return
    try:
        _active_announcement.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _log.warning("Announcement playback did not finish within %.0fs.", timeout)
    _active_announcement = None


# ---- announcement phrases (French) ---------------------------------------
#
# Short, natural fillers played asynchronously when the orchestrator emits
# an event signalling that the LLM is going to take a moment (delegating
# to a specialist, calling a research tool from the router). The point is
# to make the user feel the assistant is working, not frozen.

_DELEGATION_PHRASES: dict[str, str] = {
    "web-search-specialist": "Je cherche sur internet.",
    "wikipedia-specialist": "Je consulte Wikipédia.",
    "news-specialist": "Je vais voir les actualités.",
    "weather-specialist": "Je vérifie la météo.",
    "code-fetcher": "Je vais chercher du code et de la documentation.",
    "code-runner": "Je vais écrire et tester ça dans le sandbox.",
    "document-builder": "Je rédige le document.",
    "summarizer": "Je résume.",
    "strategist": "Je réfléchis à la décomposition du problème.",
    "critical-thinker": "Je vérifie le raisonnement.",
    "comparator-specialist": "Je compare les options.",
    "meta-analyst": "Je m'analyse moi-même.",
    "workspace-manager": "Je regarde le workspace.",
    "synthesizer": "Je rassemble les résultats.",
}
_DELEGATION_DEFAULT_PHRASE = "Je consulte un spécialiste."

# Tools that the router may call directly (without delegation) and which
# justify an announcement. Tools not in this set are too quick to bother.
_TOOL_PHRASES: dict[str, str] = {
    "web_search": "Je cherche sur internet.",
    "wikipedia_search": "Je consulte Wikipédia.",
    "wikipedia_get_page": "Je lis l'article.",
    "web_fetch": "Je lis la page.",
    "news_latest": "Je vais voir les actualités.",
    "news_archive": "Je fouille les archives d'actualités.",
}


def announce_delegation(child_agent: str) -> bool:
    """Speak a short phrase introducing a delegation, asynchronously."""
    phrase = _DELEGATION_PHRASES.get(child_agent, _DELEGATION_DEFAULT_PHRASE)
    return speak_async(phrase)


def announce_tool_call(tool_name: str) -> bool:
    """Speak a short phrase when the router calls a research-class tool.

    No-op for tools not in the announcement set (clock, workspace_*, etc.) —
    those are too fast to deserve an announcement.
    """
    phrase = _TOOL_PHRASES.get(tool_name)
    if phrase is None:
        return False
    return speak_async(phrase)


def is_available() -> bool:
    """Quick check : can vocal mode actually produce sound right now ?

    Used by the CLI to display a friendly warning at startup when the user
    asks for ``--mode vocal`` but nothing is configured.
    """
    return (
        config.VOICE_MODEL_PATH.is_file()
        and _resolve_player() is not None
    )
