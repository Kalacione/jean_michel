"""End-to-end smoke tests against a real Ollama instance.

Skipped by default. To run :

    JEANMICHEL_SMOKE_E2E=1 pytest tests/v2/test_smoke_e2e.py -v

Prerequisites :
- Ollama running locally (`ollama serve`).
- The configured DISPATCH_MODEL (default granite4.1:8b) and MAIN_MODEL
  (default gemma4:latest) pulled (`ollama pull <model>`).
- The v2 migrations applied to the test DB.

Two scenarios, mirroring the DoD of Phase 8 in 07 :

1. ALEXA path : "Quelle heure est-il ?" → tier 0 → clock → French answer.
2. DEEP path  : a question that requires the main loop → conclusion via an
   assistant turn without tool_calls.

The tests are best-effort and tolerant : Ollama latency, network blips, and
local model behaviour vary. We assert that the system returns *something*
plausible — not exact strings.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT / "src"))

requires_ollama = pytest.mark.skipif(
    os.environ.get("JEANMICHEL_SMOKE_E2E") != "1",
    reason=(
        "Set JEANMICHEL_SMOKE_E2E=1 to enable end-to-end smoke tests against "
        "a real Ollama instance."
    ),
)


@pytest.fixture()
def v2_db_for_smoke(tmp_path: Path, monkeypatch):
    """Fresh v2 DB with all migrations applied, in a temp path."""
    monkeypatch.setenv("JEANMICHEL_HOME", str(tmp_path))

    import jeanmichel.config as cfg
    cfg.REPO_ROOT = tmp_path
    cfg.DB_PATH = tmp_path / "jm_smoke.db"
    cfg.CONVERSATIONS_DIR = tmp_path / "conversations"
    cfg.USER_PROFILE_PATH = tmp_path / "user_profile.toml"
    cfg.CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

    # Phase 8 consolidation : schema.sql IS the v2 final state.
    conn = sqlite3.connect(cfg.DB_PATH)
    conn.executescript((_ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
    conn.commit()
    conn.close()

    yield cfg.DB_PATH


# ---- ALEXA path ----------------------------------------------------------


@requires_ollama
def test_smoke_alexa_clock_in_french(v2_db_for_smoke, tmp_path):
    """ALEXA path : a French time question → tier 0 → clock → French answer."""
    from jeanmichel import dispatcher
    from jeanmichel.llm import OllamaClient

    dispatch_llm = OllamaClient()  # uses DISPATCH_MODEL default
    user_text = "Quelle heure est-il ?"

    user_lang = dispatcher.detect_language(user_text)
    assert user_lang == "fr"

    decision = dispatcher.classify(user_text, dispatch_llm)
    assert decision.intent == "alexa"
    assert decision.tool == "clock"

    answer = dispatcher.execute_alexa(decision, dispatch_llm, user_lang=user_lang)
    # Best-effort assertions : we got some non-empty text in French-ish form.
    assert answer
    assert len(answer) > 5
    # The answer should mention a time-relevant token.
    lowered = answer.lower()
    assert any(
        marker in lowered
        for marker in ("heure", "h ", " : ", "utc", "minute")
    ), f"answer doesn't look time-related: {answer!r}"


@requires_ollama
def test_smoke_alexa_simple_english(v2_db_for_smoke):
    """ALEXA path : English time question → clock → English answer."""
    from jeanmichel import dispatcher
    from jeanmichel.llm import OllamaClient

    dispatch_llm = OllamaClient()

    decision = dispatcher.classify("What time is it?", dispatch_llm)
    assert decision.intent == "alexa"
    assert decision.tool == "clock"

    answer = dispatcher.execute_alexa(decision, dispatch_llm, user_lang="en")
    assert answer
    # Clock summary contains an ISO-ish timestamp ; should have a colon or T.
    assert ":" in answer or "T" in answer


# ---- DEEP path -----------------------------------------------------------


@requires_ollama
def test_smoke_deep_simple_question(v2_db_for_smoke, tmp_path):
    """DEEP path : a question that triggers the main loop, expects a final answer.

    We don't force a specific delegation : whether the model delegates or
    answers directly depends on its judgement. We only verify the loop
    terminates with a non-empty answer.
    """
    from jeanmichel import db, dispatcher
    from jeanmichel.config import UserProfile
    from jeanmichel.llm import OllamaClient
    from jeanmichel.orchestrator_v2 import load_agent_spec_v2, run_main_loop
    from jeanmichel.prompts import render_user_memory_index
    from jeanmichel.tools import build_registry

    main_llm = OllamaClient()

    user_text = "Explique en deux phrases ce qu'est la photosynthèse."

    # Force DEEP path (skip dispatcher classification for determinism).
    user_lang = dispatcher.detect_language(user_text)

    conv_folder = tmp_path / "conv_smoke"
    conv_folder.mkdir()

    with db.connect() as conn:
        user_memory_block, _ = render_user_memory_index(conn)
        agent = load_agent_spec_v2(
            conn,
            "jean-michel",
            mode="analyse",
            user_profile_text=UserProfile().render(),
            user_memory_block=user_memory_block,
            user_language=user_lang,
        )

    def resolver(code):
        try:
            with db.connect() as conn:
                return load_agent_spec_v2(conn, code, mode="analyse", user_language=user_lang)
        except KeyError:
            return None

    tools_registry = build_registry(
        conv_folder=conv_folder,
        has_workspace_write=True,
        conv_id="smoke",
        request_id_provider=lambda: "main",
        agent_role="router",
    )

    answer = run_main_loop(
        conv_folder=conv_folder,
        agent=agent,
        tools_registry=tools_registry,
        llm_client=main_llm,
        user_text=user_text,
        agent_resolver=resolver,
        event_emitter=None,
        max_iterations=20,
    )

    assert answer
    assert not answer.startswith("[Orchestrator aborted")
    # The conversation was persisted.
    assert (conv_folder / "messages.json").exists()
    assert (conv_folder / "state.json").exists()
    assert (conv_folder / "events.jsonl").exists()
