"""End-to-end smoke tests against a real Ollama instance.

Skipped by default. To run :

    JEANMICHEL_SMOKE_E2E=1 pytest tests/v2/test_smoke_e2e.py -v

Prerequisites :
- Ollama running locally (`ollama serve`).
- The configured DISPATCH_MODEL (default granite4.1:8b) and MAIN_MODEL
  (default gemma4:latest) pulled (`ollama pull <model>`).
- For the CODE scenario : CODE_MODEL (default qwen3:14b) and the code workers'
  model (qwen3-coder:latest) pulled, plus Docker running with the
  jeanmichel-sandbox:py-alpine image built (`./jm.sh --build-docker`).
- The v2 migrations applied to the test DB.

Three scenarios, mirroring the DoD of Phase 8 in 07 :

1. ALEXA path : "Quelle heure est-il ?" → tier 0 → clock → French answer.
2. DEEP path  : a question that requires the main loop → conclusion via an
   assistant turn without tool_calls.
3. CODE path  : mode='code', the qwen3:14b orchestrator decomposes a coding
   task and delegates to a qwen3-coder worker that writes + runs code.

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
    cfg.CLI_PROFILE_PATH = tmp_path / "cli_profile.toml"
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
    from jeanmichel.tools import build_registry

    main_llm = OllamaClient()

    user_text = "Explique en deux phrases ce qu'est la photosynthèse."

    # Force DEEP path (skip dispatcher classification for determinism).
    user_lang = dispatcher.detect_language(user_text)

    conv_folder = tmp_path / "conv_smoke"
    conv_folder.mkdir()

    with db.connect() as conn:
        agent = load_agent_spec_v2(
            conn,
            "jean-michel",
            mode="analyse",
            user_profile_text=UserProfile().render(),
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


# ---- CODE path -----------------------------------------------------------


@requires_ollama
def test_smoke_code_mode_factorial(v2_db_for_smoke, tmp_path):
    """CODE path : the orchestrator decomposes a coding task and delegates.

    This is the most fragile path : the qwen3:14b orchestrator drives a PDCA
    loop, the qwen3-coder workers run with thinking OFF (the model has no think
    channel), and code-runner executes in the Docker sandbox. Requires
    qwen3:14b + qwen3-coder:latest pulled and Docker up (see module docstring).

    Tolerant : we assert the loop terminates with a non-empty answer, the
    conversation persisted, AND the code machinery actually engaged (a TODO was
    written or a code worker was delegated to) — not the exact result.
    """
    import json

    from jeanmichel import db, dispatcher
    from jeanmichel.config import CODE_MODEL, MODE_ROUTER_MODEL, UserProfile
    from jeanmichel.llm import OllamaClient
    from jeanmichel.orchestrator_v2 import load_agent_spec_v2, run_main_loop
    from jeanmichel.tools import build_registry

    # The wiring under test : 'code' mode routes the main agent to CODE_MODEL.
    assert MODE_ROUTER_MODEL.get("code") == CODE_MODEL

    main_llm = OllamaClient()
    user_text = (
        "Écris une fonction Python factorial(n) qui calcule la factorielle, "
        "puis exécute-la dans le sandbox pour n=5 et donne le résultat."
    )
    user_lang = dispatcher.detect_language(user_text)

    conv_folder = tmp_path / "conv_code_smoke"
    conv_folder.mkdir()

    with db.connect() as conn:
        user_memory_block, _ = render_user_memory_index(conn)
        agent = load_agent_spec_v2(
            conn,
            "jean-michel",
            mode="code",
            user_profile_text=UserProfile().render(),
            user_memory_block=user_memory_block,
            user_language=user_lang,
        )
        # Mirror turn_runner : 'code' mode swaps the router onto CODE_MODEL.
        agent.model = MODE_ROUTER_MODEL["code"]
        # Sandbox whitelist = union of all agents' grants, so the code workers
        # reach bash_sandbox through the shared registry (spawn_subagent reuses
        # the same registry, filtered per-agent by the PreToolUse hook).
        sandbox_grants = sorted(
            {
                r["command"]
                for r in conn.execute("SELECT DISTINCT command FROM agent_sandbox_grants")
            }
        )

    def resolver(code):
        try:
            with db.connect() as conn:
                return load_agent_spec_v2(conn, code, mode="code", user_language=user_lang)
        except KeyError:
            return None

    tools_registry = build_registry(
        conv_folder=conv_folder,
        has_workspace_write=True,
        conv_id="code-smoke",
        request_id_provider=lambda: "main",
        sandbox_grants=sandbox_grants,
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
        max_iterations=40,
    )

    assert answer
    assert not answer.startswith("[Orchestrator aborted")
    assert (conv_folder / "messages.json").exists()
    assert (conv_folder / "events.jsonl").exists()

    # The code machinery engaged : a TODO was written (PDCA decomposition) or a
    # code worker was delegated to.
    todo_written = (conv_folder / "todo.json").exists()
    events = [
        json.loads(line)
        for line in (conv_folder / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    code_workers = {"code-runner", "code-runner-node"}
    delegated_to_code = any(
        e.get("type") == "DelegationStarted" and e.get("child_agent") in code_workers
        for e in events
    )
    assert todo_written or delegated_to_code, (
        "code mode did not engage: no todo.json and no delegation to a code worker"
    )
