"""Run one user turn, transport-agnostic.

Extracted from ``cli.run_one_turn`` + ``cli._run_deep_turn`` so the CLI and
the web daemon drive the orchestrator through the SAME code. The orchestrator
itself (``run_main_loop``) is unchanged — it already accepts ``event_emitter``
and ``ask_human_callback`` as injection points.

What the caller provides :
  - ``event_emitter``      : where orchestrator events go (CLI renders them ;
                             the daemon pushes them to a WebSocket).
  - ``ask_human_callback`` : how a human reply is obtained (CLI prompts the
                             terminal ; the daemon does a WS round-trip).
  - ``on_dispatch``        : optional hook called once the Tier-0 decision is
                             known, before execution (CLI prints the routing
                             line ; the daemon may emit a dispatch event).

What stays in the caller : the thinking spinner, the answer panel, TTS
playback — all of that is presentation, not orchestration.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import db, dispatcher
from ..config import UserProfile
from ..events import MemoryNearCapacity
from ..orchestrator_v2 import (
    AgentSpec,
    AskHumanCallback,
    EventEmitter,
    load_agent_spec_v2,
    run_main_loop,
)
from ..prompts import render_user_memory_index
from ..tools import build_registry

_log = logging.getLogger(__name__)

# Mirror the literal used by the CLI today (cf. config.USER_MEMORY_WARN_AT).
_MEMORY_WARN_AT = 90


def run_turn(
    *,
    user_text: str,
    conv_folder: Path,
    conv_id: str,
    mode: str,
    dispatch_llm: Any,
    main_llm: Any,
    profile: UserProfile,
    initial_messages: list[dict] | None = None,
    event_emitter: EventEmitter | None = None,
    ask_human_callback: AskHumanCallback | None = None,
    on_dispatch: Callable[[Any], None] | None = None,
) -> str:
    """Process one user turn end-to-end and return the user-facing answer.

    Tier 0 dispatch (ALEXA vs DEEP) then either a direct tool execution or the
    Tier 1 main loop. No I/O on the terminal — see module docstring.
    """
    user_lang = dispatcher.detect_language(user_text)

    # Update conversation language opportunistically.
    if user_lang and user_lang != "und":
        try:
            with db.connect() as conn:
                db.update_conversation_language(conn, conv_id, user_lang)
        except Exception as exc:  # noqa: BLE001
            _log.debug("update_conversation_language failed: %s", exc)

    # Tier 0 : dispatch. In chat / vocal modes the small dispatcher LLM sees
    # the conversation history to resolve follow-ups ("et pour demain ?").
    # In analyse mode each question is standalone — the documented contract.
    dispatcher_history = initial_messages if mode in ("chat", "vocal") else None
    decision = dispatcher.classify(user_text, dispatch_llm, history=dispatcher_history)

    if on_dispatch is not None:
        on_dispatch(decision)

    if decision.intent == "alexa":
        return dispatcher.execute_alexa(
            decision,
            dispatch_llm,
            user_lang=user_lang,
            user_profile=profile,
        )

    return _run_deep_turn(
        user_text=user_text,
        conv_folder=conv_folder,
        conv_id=conv_id,
        main_llm=main_llm,
        profile=profile,
        mode=mode,
        user_lang=user_lang,
        initial_messages=initial_messages,
        event_emitter=event_emitter,
        ask_human_callback=ask_human_callback,
    )


def _run_deep_turn(
    *,
    user_text: str,
    conv_folder: Path,
    conv_id: str,
    main_llm: Any,
    profile: UserProfile,
    mode: str,
    user_lang: str,
    initial_messages: list[dict] | None,
    event_emitter: EventEmitter | None,
    ask_human_callback: AskHumanCallback | None,
) -> str:
    """Engage Tier 1 : load jean-michel spec, build registry, run the main loop."""
    with db.connect() as conn:
        user_memory_block, count = render_user_memory_index(conn)
        if count >= _MEMORY_WARN_AT and event_emitter is not None:
            event_emitter(MemoryNearCapacity(current_count=count, limit=100))
        main_agent = load_agent_spec_v2(
            conn,
            "jean-michel",
            mode=mode,
            user_profile_text=profile.render(),
            user_memory_block=user_memory_block,
            user_language=user_lang,
        )

    def agent_resolver(code: str) -> AgentSpec | None:
        try:
            with db.connect() as conn:
                u_mem, _ = render_user_memory_index(conn)
                return load_agent_spec_v2(
                    conn,
                    code,
                    mode=mode,
                    user_profile_text=profile.render(),
                    user_memory_block=u_mem,
                    user_language=user_lang,
                )
        except Exception as exc:  # noqa: BLE001
            _log.warning("agent_resolver(%r) failed: %s", code, exc)
            return None

    # Permissive registry ; per-agent filtering is enforced by the PreToolUse
    # hook. The sandbox whitelist is the UNION of all agents' grants so the
    # bash_sandbox tool exists in the registry for any agent that may use it.
    with db.connect() as grants_conn:
        sandbox_grants = sorted(
            {
                r["command"]
                for r in grants_conn.execute(
                    "SELECT DISTINCT command FROM agent_sandbox_grants"
                )
            }
        )

    tools_registry = build_registry(
        conv_folder=conv_folder,
        has_workspace_write=True,
        conv_id=conv_id,
        request_id_provider=lambda: "main",
        sandbox_grants=sandbox_grants,
        sandbox_image=None,
        agent_role="router",
    )

    # On resume, re-render messages[0] with a fresh system prompt to pick up
    # user_memory updates.
    seeded_messages: list[dict] | None = None
    if initial_messages:
        seeded_messages = list(initial_messages)
        if seeded_messages and seeded_messages[0].get("role") == "system":
            seeded_messages[0] = {
                "role": "system",
                "content": main_agent.system_prompt,
            }

    return run_main_loop(
        conv_folder=conv_folder,
        agent=main_agent,
        tools_registry=tools_registry,
        llm_client=main_llm,
        user_text=user_text,
        initial_messages=seeded_messages,
        ask_human_callback=ask_human_callback,
        agent_resolver=agent_resolver,
        event_emitter=event_emitter,
    )
