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

from .. import db, dispatcher, persistence, snapshot
from ..config import MAIN_MODEL, MODE_ROUTER_MODEL, UserProfile
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
from .workspace import is_image, normalized_image_b64

_log = logging.getLogger(__name__)

# Mirror the literal used by the CLI today (cf. config.USER_MEMORY_WARN_AT).
_MEMORY_WARN_AT = 90


def _default_title(text: str, limit: int = 60) -> str:
    """Cheap default conversation title : the first user message, whitespace-
    collapsed and truncated. No LLM call (KISS) ; the user can edit it later."""
    line = " ".join((text or "").split())
    if not line:
        return "Conversation"
    return line[:limit] + ("…" if len(line) > limit else "")


def _persist_alexa_turn(conv_folder, user_text: str, answer: str) -> None:
    """Append a tier-0 (ALEXA) exchange to messages.json so it is first-class
    history : it survives reload, feeds the next turn's context, and gets its
    own end-of-turn snapshot — exactly like a DEEP turn (which persists via
    run_main_loop). ALEXA wrote nothing to disk before. Best-effort."""
    try:
        messages = persistence.load_messages(conv_folder)
        messages.append({"role": "user", "content": user_text})
        messages.append({"role": "assistant", "content": answer})
        persistence.save_messages(conv_folder, messages)
    except Exception as exc:  # noqa: BLE001
        _log.debug("alexa turn persistence failed: %s", exc)


def _attachment_note(attachments: list[str] | None, mode: str) -> str:
    """Reference line naming workspace files the user attached to the message.

    The agent reads them on demand with workspace_view — we reference (not
    inline) so it stays binary-safe and scales to large files. Appended to the
    user message so it is both seen by the LLM and persisted with the turn.
    """
    if not attachments:
        return ""
    listed = ", ".join(f"`{p}`" for p in attachments)
    note = f"\n\nFichiers joints du workspace : {listed}."
    # chat/vocal read images via the analyze_image tool (A) ; analyse mode gets
    # the image in-context (B) so it needs no tool hint.
    if mode in ("chat", "vocal", "code") and any(is_image(p) for p in attachments):
        note += " Pour analyser une image, utilise l'outil analyze_image(path, question)."
    return note


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
    memory_user_id: int | None = None,
    attachments: list[str] | None = None,
) -> str:
    """Process one user turn end-to-end and return the user-facing answer.

    Tier 0 dispatch (ALEXA vs DEEP) then either a direct tool execution or the
    Tier 1 main loop. No I/O on the terminal — see module docstring.
    """
    user_lang = dispatcher.detect_language(user_text)

    # Conversation metadata : opportunistic language + a cheap default title
    # seeded from the first user message (user-editable later). Computed on the
    # clean text, before the attachment note is folded in below — also reused as
    # the end-of-turn snapshot commit label.
    turn_label = _default_title(user_text)
    try:
        with db.connect() as conn:
            if user_lang and user_lang != "und":
                db.update_conversation_language(conn, conv_id, user_lang)
            db.set_title_if_empty(conn, conv_id, turn_label)
    except Exception as exc:  # noqa: BLE001
        _log.debug("conversation metadata update failed: %s", exc)

    # Fold any attached workspace files into the message so the dispatcher, the
    # main loop AND the persisted turn all reference them (computed after the
    # title/metadata above, which intentionally use the clean text).
    user_text = user_text + _attachment_note(attachments, mode)

    # Tier 0 : dispatch. In chat / vocal modes the small dispatcher LLM sees
    # the conversation history to resolve follow-ups ("et pour demain ?").
    # In analyse mode each question is standalone — the documented contract.
    dispatcher_history = initial_messages if mode in ("chat", "vocal", "code") else None
    decision = dispatcher.classify(user_text, dispatch_llm, history=dispatcher_history)

    if on_dispatch is not None:
        on_dispatch(decision)

    # An attached image needs the multimodal main agent (the granite dispatcher
    # is text-only) → never take the ALEXA shortcut when one is present.
    has_image = any(is_image(p) for p in (attachments or []))

    if decision.intent == "alexa" and not has_image:
        answer = dispatcher.execute_alexa(
            decision,
            dispatch_llm,
            user_lang=user_lang,
            user_profile=profile,
        )
        # Persist the exchange so every turn — not just DEEP ones — is real
        # history and gets a snapshot at end-of-turn.
        _persist_alexa_turn(conv_folder, user_text, answer)
    else:
        # Vision B : in `analyse` mode, feed attached images IN-CONTEXT (transient
        # base64 from the normalized workspace derivative). chat/vocal rely on the
        # analyze_image tool instead, to keep the conversation context light.
        image_b64 = (
            [b64 for p in (attachments or []) if (b64 := normalized_image_b64(conv_folder, p))]
            if mode == "analyse" and has_image
            else []
        )
        answer = _run_deep_turn(
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
            memory_user_id=memory_user_id,
            images=image_b64,
        )

    # Mark last interaction LAST so it wins over any modified_at writes made
    # during the turn and stays format-consistent for sort.
    try:
        with db.connect() as conn:
            db.touch_conversation(conn, conv_id)
    except Exception as exc:  # noqa: BLE001
        _log.debug("touch_conversation failed: %s", exc)

    # End-of-turn snapshot : single chokepoint for both CLI and API. By here
    # messages.json/state.json/events.jsonl + workspace/ are all flushed. No-op
    # unless CONVERSATION_SNAPSHOT_ENABLED ; never raises (best-effort).
    snapshot.commit_turn(conv_folder, f"turn: {turn_label}")
    return answer


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
    memory_user_id: int | None,
    images: list[str] | None = None,
) -> str:
    """Engage Tier 1 : load jean-michel spec, build registry, run the main loop."""
    with db.connect() as conn:
        user_memory_block, count = render_user_memory_index(conn, memory_user_id)
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
                u_mem, _ = render_user_memory_index(conn, memory_user_id)
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
        memory_user_id=memory_user_id,
        vision_client=main_llm,
    )

    # On resume, re-render messages[0] with a fresh system prompt to pick up
    # user_memory updates. A history that has no leading system message (e.g. a
    # conversation whose first turns were ALEXA — those persist [user,assistant]
    # only) gets the system prompt prepended so the main agent keeps its prompt.
    seeded_messages: list[dict] | None = None
    if initial_messages:
        seeded_messages = list(initial_messages)
        system_msg = {"role": "system", "content": main_agent.system_prompt}
        if seeded_messages and seeded_messages[0].get("role") == "system":
            seeded_messages[0] = system_msg
        else:
            seeded_messages = [system_msg, *seeded_messages]

    # Router model by interaction mode (config-driven): 'code' uses a stronger
    # model for methodical decomposition; other modes keep the agent default
    # (gemma4, vision-capable). In-context vision always forces the vision model.
    mode_model = MODE_ROUTER_MODEL.get(mode)
    if mode_model:
        main_agent.model = mode_model
    if images:
        main_agent.model = MAIN_MODEL

    return run_main_loop(
        conv_folder=conv_folder,
        agent=main_agent,
        tools_registry=tools_registry,
        llm_client=main_llm,
        user_text=user_text,
        images=images,
        initial_messages=seeded_messages,
        ask_human_callback=ask_human_callback,
        agent_resolver=agent_resolver,
        event_emitter=event_emitter,
    )
