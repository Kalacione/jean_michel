"""v2 CLI for Jean-Michel.

Wires together the v2 stack : Tier 0 dispatcher (granite, JSON-forced) +
Tier 1 main loop (gemma, multi-turn) + nested subagents. The CLI is the
sole consumer of the orchestrator's event stream and the sole point where
human input is solicited.

Implements the §11 ter B contract of DevNotes/REVOLUCION/06_proposition_v2.md.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from . import bootstrap as bootstrap_mod
from . import db, mcp_client, persistence
from .config import (
    DISPATCH_MODEL,
    MAIN_MODEL,
    UserProfile,
    ensure_dirs,
)
from .events import (
    AgentThinking,
    DelegationCompleted,
    DelegationStarted,
    HookFired,
    LLMCallCompleted,
    LLMCallStarted,
    MemoryNearCapacity,
    RequestCompleted,
    RequestStarted,
    ToolCallCompleted,
    ToolCallStarted,
    WorkingBudgetUpdate,
)
from .llm import OllamaClient
from .orchestrator_v2 import AskHumanCallback
from .service import consolidation as consolidation_svc
from .service import conversation as conversation_svc
from .service import turn_runner

_log = logging.getLogger(__name__)


# ---- Style palette --------------------------------------------------------

C_USER = "bold cyan"
C_AGENT = "bold cyan"
C_TOOL = "yellow"
C_THOUGHT = "dim italic"
C_HUMAN_Q = "bold yellow"
C_FINAL = "bold green"
C_WARN = "bold red"


# ---- Splash ---------------------------------------------------------------

_SPLASH = r"""
     _                  __  __ _      _          _
    | | ___  __ _ _ __ |  \/  (_) ___| |__   ___| |
 _  | |/ _ \/ _` | '_ \| |\/| | |/ __| '_ \ / _ \ |
| |_| |  __/ (_| | | | | |  | | | (__| | | |  __/ |
 \___/ \___|\__,_|_| |_|_|  |_|_|\___|_| |_|\___|_|
"""


def render_splash(console: Console, main_model: str, dispatch_model: str, mode: str) -> None:
    console.print(Text(_SPLASH, style="bold cyan"))
    console.print(
        f"[dim]main={main_model} · dispatch={dispatch_model} · mode={mode} • "
        "Enter=newline  Ctrl+Enter=send  Ctrl-D=quit[/]\n"
    )


# ---- Event rendering -----------------------------------------------------


def _truncate(value: object, max_len: int = 80) -> str:
    s = str(value).replace("\n", " ")
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def render_event(
    console: Console,
    event: Any,
    *,
    mode: str = "analyse",
    show_thoughts: bool = False,
) -> None:
    """Render one v2 event live to the console.

    Each event type has a focused, line-oriented presentation. The final
    answer Panel is rendered by `run_one_turn` AFTER the loop returns, not
    here — `RequestCompleted` is just a thin rule marker.
    """
    if isinstance(event, RequestStarted):
        indent = "  " * max(0, event.depth)
        console.print(
            f"{indent}[{C_AGENT}]→ {event.agent}[/] [dim](depth={event.depth})[/]"
        )
        if event.briefing_summary:
            console.print(f"{indent}  [dim]{_truncate(event.briefing_summary, 100)}[/]")

    elif isinstance(event, LLMCallStarted):
        # Keep this quiet to avoid spamming during compaction-heavy runs.
        # Show only the working budget consumption.
        pass

    elif isinstance(event, LLMCallCompleted):
        # Quiet by default — token info is in the events.jsonl for audit.
        pass

    elif isinstance(event, ToolCallStarted):
        console.print(
            f"  [{C_TOOL}]🔧 {event.tool_name}[/] [dim]({event.args_summary})[/]"
        )

    elif isinstance(event, ToolCallCompleted):
        console.print(f"  [dim]↳ {event.tool_name} → {_truncate(event.result_summary, 120)}[/]")

    elif isinstance(event, DelegationStarted):
        console.print(
            f"  [{C_AGENT}]↳ delegating to {event.child_agent}[/] "
            f"[dim](depth={event.depth}, budget={event.child_working_budget})[/]"
        )

    elif isinstance(event, DelegationCompleted):
        confidence_color = {"high": "green", "medium": "yellow", "low": "red"}.get(
            event.confidence, "dim"
        )
        files = f" · {len(event.files_produced)} file(s)" if event.files_produced else ""
        console.print(
            f"  [{C_TOOL}]✓ {event.child_agent}[/] "
            f"[{confidence_color}]confidence={event.confidence}[/]{files} "
            f"[dim]{_truncate(event.summary, 100)}[/]"
        )

    elif isinstance(event, HookFired):
        console.print(
            f"  [{C_WARN}]⚠ {event.hook_name}: {event.action}[/] "
            f"[dim]{_truncate(event.reason, 120)}[/]"
        )

    elif isinstance(event, WorkingBudgetUpdate):
        labels = {1: "snip", 2: "microcompact", 3: "collapse", 4: "autocompact"}
        label = labels.get(event.compaction_level_triggered, f"L{event.compaction_level_triggered}")
        console.print(
            f"  [{C_WARN}]⏱ compaction · {label}[/] "
            f"[dim](working at {event.ratio * 100:.0f}%)[/]"
        )

    elif isinstance(event, MemoryNearCapacity):
        console.print(
            f"[{C_WARN}]⚠ user memory at {event.current_count}/{event.limit} — "
            "consider purging obsolete entries via manage_memory(action='delete')[/]"
        )

    elif isinstance(event, RequestCompleted):
        # Thin marker — the final answer is printed as a Panel by the caller.
        indent = ""
        console.print(Rule(Text(f"answer from {event.agent}", style="dim"), style="dim"))

    elif isinstance(event, AgentThinking):
        # Thought channel — quiet by default ; shown only with --show-thoughts.
        if show_thoughts:
            console.print(f"  [{C_THOUGHT}]💭 {_truncate(event.text, 200)}[/]")

    else:
        # Unknown event type — log it raw.
        console.print(f"  [dim]· {type(event).__name__}: {event!r}[/]")


# ---- ask_human callback --------------------------------------------------


def make_ask_human(console: Console, session: PromptSession) -> AskHumanCallback:
    """Build the ask_human callback that pauses the loop for a human reply."""

    def _ask(question: str, why: str, choices: list[str], multi: bool) -> str:
        body: list[Any] = [Text(why, style="dim italic"), Text(""), Text(question, style=C_HUMAN_Q)]
        if choices:
            body.append(Text(""))
            for i, choice in enumerate(choices, 1):
                body.append(Text(f"  {i}. {choice}", style=C_HUMAN_Q))
        console.print()
        console.print(Panel(Group(*body), title="Question", border_style="yellow", padding=(1, 2)))
        hint = (
            "your answer (numbers, comma-separated, or free text)"
            if multi else "your answer (a number, or free text)"
        ) if choices else "your answer"
        raw = session.prompt(
            HTML(f'<ansiyellow><b>{hint}</b></ansiyellow>: '),
            multiline=True,
            prompt_continuation=lambda width, line_number, wrap_count: " " * width,
        ).strip()
        # Map number(s) → choice label(s) ; anything else is free text (the "Other" escape).
        if choices:
            picked = _resolve_choice_numbers(raw, choices, multi)
            if picked is not None:
                return picked
        return raw

    return _ask


def _resolve_choice_numbers(raw: str, choices: list[str], multi: bool) -> str | None:
    """If `raw` is a number (or comma-separated numbers when multi), return the
    matching choice label(s) joined by ', '. Otherwise None (treat as free text)."""
    tokens = [t.strip() for t in raw.split(",")] if multi else [raw]
    labels: list[str] = []
    for tok in tokens:
        if not tok.isdigit():
            return None
        idx = int(tok)
        if not (1 <= idx <= len(choices)):
            return None
        labels.append(choices[idx - 1])
    return ", ".join(labels) if labels else None


# ---- Conversation lifecycle ---------------------------------------------


def _resolve_resume(
    resume_arg: str, console: Console
) -> tuple[str, Path, str] | tuple[None, None, None]:
    """Find the conversation to resume. Returns (id, folder, mode) or all-None."""
    with db.connect() as conn:
        if resume_arg == "__last__":
            rows = db.list_active_conversations(conn, limit=1)
            row = rows[0] if rows else None
        else:
            row = db.get_conversation(conn, resume_arg)
    if row is None:
        console.print("[red]Conversation not found or already closed.[/]")
        return None, None, None
    if row["status"] not in {"active", "awaiting_human"}:
        console.print(
            f"[red]Conversation {row['id'][:12]} is '{row['status']}' — cannot resume.[/]"
        )
        return None, None, None
    return row["id"], Path(row["folder_path"]), row["mode"]


# ---- One-turn processing -------------------------------------------------


def run_one_turn(
    *,
    user_text: str,
    conv_folder: Path,
    conv_id: str,
    dispatch_llm: OllamaClient,
    main_llm: OllamaClient,
    profile: UserProfile,
    mode: str,
    console: Console,
    ask_human_cb: AskHumanCallback,
    initial_messages: list[dict] | None,
    consolidate: bool = False,
) -> str:
    """Process one user turn end-to-end.

    Shows a "thinking…" spinner while the LLM is busy and pauses it for
    event rendering / ask_human prompts. Once the spinner is up the user
    can no longer type — the prompt session won't return to its prompt
    until we finish and call ``session.prompt()`` again.

    Returns the final user-facing answer (also printed as a Panel here).
    """
    # Spinner managed by hand so we can pause it during event rendering,
    # dispatch routing display and ask_human prompts. The orchestrator is
    # driven by ``turn_runner.run_turn`` ; here we only present its output.
    status = console.status("[dim]thinking…[/]", spinner="dots")
    status.start()

    def emitter(event: Any) -> None:
        status.stop()
        try:
            render_event(console, event, mode=mode)
            # Vocal mode : announce thinking start, delegations and direct
            # research tool calls via async TTS so the user hears progress
            # while the LLM works.
            if mode == "vocal":
                from . import voice
                phrase = voice.phrase_for_event(event)
                if phrase:
                    voice.speak_async(phrase)
        finally:
            status.start()

    # Pause the spinner while waiting on a human answer.
    def ask_human_with_pause(question: str, why: str, choices: list[str], multi: bool) -> str:
        status.stop()
        try:
            return ask_human_cb(question, why, choices, multi)
        finally:
            status.start()

    # Print the Tier-0 routing line once the dispatch decision is known.
    was_deep = {"v": False}

    def on_dispatch(decision: Any) -> None:
        status.stop()
        was_deep["v"] = decision.intent != "alexa"
        try:
            if decision.intent == "alexa":
                console.print(
                    f"[dim]→ tier 0 alexa · tool={decision.tool} · "
                    f"confidence={decision.confidence}[/]"
                )
            else:
                console.print(
                    f"[dim]→ tier 1 deep · confidence={decision.confidence}[/]"
                )
        finally:
            status.start()

    try:
        answer = turn_runner.run_turn(
            user_text=user_text,
            conv_folder=conv_folder,
            conv_id=conv_id,
            mode=mode,
            dispatch_llm=dispatch_llm,
            main_llm=main_llm,
            profile=profile,
            initial_messages=initial_messages,
            event_emitter=emitter,
            ask_human_callback=ask_human_with_pause,
            on_dispatch=on_dispatch,
        )
    finally:
        status.stop()

    # Final answer panel.
    console.print()
    console.print(
        Panel(
            Markdown(answer),
            title="Jean-Michel",
            border_style="green",
            padding=(1, 2),
        )
    )

    # Vocal mode : play the answer through the TTS pipeline. Failure is
    # non-fatal — the text panel already shows the response.
    if mode == "vocal":
        from . import voice
        # Wait for any async announcement still playing before starting the
        # final answer playback (otherwise they overlap).
        voice.wait_for_announcements(timeout=10.0)
        with console.status("[dim]🔊 speaking…[/]", spinner="dots"):
            ok = voice.speak(answer)
        if not ok:
            console.print(
                "[yellow]vocal output unavailable — "
                "set JEANMICHEL_VOICE_MODEL to a Piper .onnx and ensure "
                "paplay/aplay/ffplay is installed (see voice_models/README.md).[/]"
            )

    # Shadow consolidation : after the answer is shown (the user reads/thinks),
    # introspect in the background to propose durable memories. Non-blocking,
    # best-effort, DEEP turns only (ALEXA single-facts hold nothing to remember).
    # Results land in pending_memory.json ; the loop surfaces them at the next prompt.
    if consolidate and was_deep["v"]:
        threading.Thread(
            target=consolidation_svc.run_shadow,
            args=(conv_folder, conv_id),
            kwargs={"llm": main_llm},
            daemon=True,
        ).start()

    return answer


# ---- Argument parsing & main --------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jean-michel")
    parser.add_argument(
        "--main-model",
        default=MAIN_MODEL,
        help="Ollama model tag for the main agent (default: %(default)s).",
    )
    parser.add_argument(
        "--dispatch-model",
        default=DISPATCH_MODEL,
        help="Ollama model tag for the Tier 0 dispatcher (default: %(default)s).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Deprecated alias for --main-model.",
    )
    parser.add_argument(
        "--show-thoughts",
        action="store_true",
        help="Display the agent's thought channel (verbose).",
    )
    parser.add_argument(
        "--mode",
        choices=["analyse", "chat", "vocal", "code"],
        default="analyse",
        help="Conversation mode (default: analyse).",
    )
    parser.add_argument(
        "--project",
        metavar="CODE",
        default=None,
        help="Attach the new conversation to a project (by code). Its scope='project' "
        "memory is then injected each turn. Created on first use if absent.",
    )
    parser.add_argument(
        "--resume",
        nargs="?",
        const="__last__",
        default=None,
        metavar="CONV_ID",
        help="Resume a conversation. Without ID : the most recent active one.",
    )
    parser.add_argument(
        "--list-conv",
        action="store_true",
        help="List recent active conversations and exit.",
    )
    parser.add_argument(
        "--once",
        metavar="TEXT",
        help="Process a single prompt non-interactively then exit.",
    )
    return parser


def _build_prompt_session() -> PromptSession[str]:
    kb = KeyBindings()

    @kb.add("enter")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    # Ctrl+Enter submits. Most terminals (Linux/Mac terminals, Windows
    # Terminal, PowerShell) deliver Ctrl+Enter as Ctrl+J — Alt+Enter is
    # unreliable under Windows so we switched to Ctrl+J / c-j.
    @kb.add("c-j")
    def _submit(event):
        event.current_buffer.validate_and_handle()

    return PromptSession(history=InMemoryHistory(), key_bindings=kb)


def _list_conv_and_exit(console: Console) -> int:
    from rich.table import Table

    with db.connect() as conn:
        rows = db.list_active_conversations(conn)
    if not rows:
        console.print("[dim]No active conversation.[/]")
        return 0
    table = Table(title="Active conversations", show_lines=True)
    for col in ("conv_id (prefix)", "mode", "status", "lang", "created", "last activity"):
        table.add_column(col, no_wrap=True)
    for r in rows:
        table.add_row(
            r["id"][:12],
            r["mode"],
            r["status"],
            r["user_language"] or "?",
            r["created_at"][:16],
            r["modified_at"][:16],
        )
    console.print(table)
    return 0


def _resolve_cli_project(code: str, console: Console) -> int | None:
    """Resolve a project by ``code`` for the cli user, creating it if absent.

    Returns the project id, or None on any error (project attachment is
    best-effort — it must never block starting a conversation)."""
    from .service import project as project_svc

    try:
        with db.connect() as conn:
            uid = db.cli_user_id(conn)
            existing = db.get_project_by_code(conn, uid, code)
            if existing is not None:
                return existing["id"]
            proj = project_svc.create(conn, user_id=uid, code=code, name=code)
            console.print(f"[dim]Created project '{code}'.[/]")
            return proj["id"]
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]project '{code}' skipped: {exc}[/]")
        return None


def _memory_recap(console: Console, conv_folder: Path) -> None:
    """One-line nudge if the shadow pass stashed memory candidates to review."""
    pending = consolidation_svc.load_pending(conv_folder)
    if pending:
        console.print(
            f"[dim]💡 {len(pending)} élément(s) à mémoriser — tape [/]"
            f"[{C_TOOL}]/memo[/][dim] pour revoir.[/]"
        )


def review_pending(console: Console, session: PromptSession, conv_folder: Path) -> None:
    """Interactive review of pending memory candidates : accept / edit / extend / drop.

    Human-in-the-loop : nothing is written until the user confirms each item.
    Grounded candidates show their source quote ; existing matches are surfaced
    so the user can extend rather than duplicate."""
    pending = consolidation_svc.load_pending(conv_folder)
    if not pending:
        console.print("[dim]Aucune suggestion mémoire en attente.[/]")
        return

    with db.connect() as conn:
        uid = db.cli_user_id(conn)

    remaining: list[dict] = []
    for i, c in enumerate(pending):
        target = c.get("tool_code") or (f"project#{c['project_id']}" if c.get("project_id") else "")
        body = [
            f"[bold]\\[{c['scope']}] {c['code']}[/]" + (f"  ·  {target}" if target else ""),
            f"[bold]{c['title']}[/] — {c['description']}",
            "",
            c["content"],
            "",
            f"[dim]source : “{c['grounding_quote']}”[/]",
        ]
        if c.get("existing_matches"):
            sim = ", ".join(m["code"] for m in c["existing_matches"])
            body.append(f"[{C_WARN}]similaires existants : {sim}[/]")
        console.print(Panel(Group(*[Text.from_markup(x) if isinstance(x, str) else x for x in body]),
                            title=f"Suggestion {i + 1}/{len(pending)}", border_style="cyan"))

        default = "x" if c["suggested_action"] == "extend" else "s"
        choice = session.prompt(
            HTML(f"<ansiyellow>[s]auver / [e]diter / e[x]tendre / [d]rop / [q]uitter "
                 f"(défaut {default})</ansiyellow>: ")
        ).strip().lower() or default

        if choice == "q":
            remaining.extend(pending[i:])  # keep this one and the rest
            break
        if choice == "d":
            continue  # drop → not saved, not kept

        title = c["title"]
        description = c["description"]
        content = c["content"]
        if choice == "e":
            title = (session.prompt(HTML("<ansicyan>titre</ansicyan>: "), default=title) or title).strip()
            description = (session.prompt(HTML("<ansicyan>description</ansicyan>: "), default=description) or description).strip()
            content = (session.prompt(HTML("<ansicyan>contenu</ansicyan>: "), default=content, multiline=True) or content).strip()
        action = "extend" if choice == "x" else "save"
        try:
            with db.connect() as conn:
                consolidation_svc.apply_candidate(
                    conn, c, action=action, user_id=uid,
                    title=title, description=description, content=content,
                )
            console.print(f"[{C_FINAL}]✓ {action} {c['scope']}/{c['code']}[/]")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[{C_WARN}]✖ {exc}[/]")
            remaining.append(c)  # keep it so the user can retry

    consolidation_svc.save_pending(conv_folder, remaining)


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    # --model is a deprecated alias for --main-model.
    if args.model and args.main_model == MAIN_MODEL:
        args.main_model = args.model

    console = Console()

    if args.list_conv:
        return _list_conv_and_exit(console)

    ensure_dirs()

    # ----- LLM clients -----
    try:
        dispatch_llm = OllamaClient(model=args.dispatch_model)
        main_llm = OllamaClient(model=args.main_model)
    except RuntimeError as exc:
        console.print(f"[{C_WARN}]{exc}[/]")
        return 2

    # Connect to configured MCP servers (no-op when off/unconfigured).
    mcp_client.startup()

    # Vocal mode preflight : warn now if TTS isn't usable, so the user
    # knows responses will be text-only before the first turn runs.
    if args.mode == "vocal":
        from . import voice
        if not voice.is_available():
            console.print(
                "[yellow]⚠ vocal mode requested but TTS not ready : "
                "missing JEANMICHEL_VOICE_MODEL or no audio player. "
                "Responses will be text-only. See voice_models/README.md.[/]\n"
            )

    # ----- User profile + bootstrap user_memory -----
    profile = UserProfile.load()
    try:
        with db.connect() as conn:
            bootstrap_mod.bootstrap_user_memory_from_profile(conn, profile)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]bootstrap skipped: {exc}[/]")

    # ----- Conversation lifecycle -----
    initial_messages: list[dict] | None = None
    if args.resume is not None:
        conv_id, conv_folder, conv_mode = _resolve_resume(args.resume, console)
        if conv_id is None:
            return 1
        args.mode = conv_mode
        initial_messages = persistence.load_messages(conv_folder)
        console.print(f"[dim]Resumed conversation {conv_id[:12]} (mode: {conv_mode})[/]\n")
    else:
        project_id = _resolve_cli_project(args.project, console) if args.project else None
        conv_id, conv_folder = conversation_svc.create_conversation(
            args.mode, project_id=project_id
        )
        if project_id is not None:
            console.print(f"[dim]Project: {args.project}[/]\n")

    render_splash(console, args.main_model, args.dispatch_model, args.mode)

    session = _build_prompt_session()
    ask_human_cb = make_ask_human(console, session)

    def _close_conv() -> None:
        try:
            with db.connect() as conn:
                db.close_conversation(conn, conv_id)
        except Exception as exc:  # noqa: BLE001
            _log.debug("close_conversation failed: %s", exc)
        mcp_client.shutdown()  # tear down MCP sessions/loop (no-op when off)
        # Stop THIS conversation's sandbox/project containers (not a daemon's).
        try:
            from .tools.bash_sandbox import reap_sandboxes
            reap_sandboxes(conv_id=conv_id)
        except Exception as exc:  # noqa: BLE001
            _log.debug("reap_sandboxes failed: %s", exc)

    # ----- --once : single non-interactive turn -----
    if args.once:
        try:
            run_one_turn(
                user_text=args.once,
                conv_folder=conv_folder,
                conv_id=conv_id,
                dispatch_llm=dispatch_llm,
                main_llm=main_llm,
                profile=profile,
                mode=args.mode,
                console=console,
                ask_human_cb=ask_human_cb,
                initial_messages=initial_messages,
            )
        except Exception as exc:  # noqa: BLE001
            console.print(f"[{C_WARN}]✖ orchestration failed: {exc}[/]")
            return 1
        finally:
            _close_conv()
        return 0

    # ----- Interactive loop -----
    # try/finally wrapping ensures the conversation row is closed on every
    # exit path — including KeyboardInterrupt during a long LLM call, which
    # is NOT caught by `except Exception` and used to leak active rows in DB.
    try:
        while True:
            # End-of-turn nudge : surface any memory candidates the shadow pass
            # stashed (it ran while the user read the previous answer).
            _memory_recap(console, conv_folder)
            try:
                user_input = session.prompt(
                    HTML('<ansibrightcyan><b>you</b></ansibrightcyan>: '),
                    multiline=True,
                    prompt_continuation=lambda width, line_number, wrap_count: " " * width,
                )
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]bye.[/]")
                return 0
            cmd = user_input.strip().lower()
            if cmd in {"exit", "quit"}:
                console.print("[dim]bye.[/]")
                return 0
            if cmd in {"/memo", "/memory"}:
                review_pending(console, session, conv_folder)
                continue
            if not user_input.strip():
                continue

            try:
                run_one_turn(
                    user_text=user_input,
                    conv_folder=conv_folder,
                    conv_id=conv_id,
                    dispatch_llm=dispatch_llm,
                    main_llm=main_llm,
                    profile=profile,
                    mode=args.mode,
                    console=console,
                    ask_human_cb=ask_human_cb,
                    initial_messages=initial_messages,
                    consolidate=True,
                )
            except KeyboardInterrupt:
                # User aborted mid-turn (Ctrl-C). Close cleanly via the
                # outer finally, no error message.
                console.print("\n[dim]interrupted — bye.[/]")
                return 0
            except Exception as exc:  # noqa: BLE001
                console.print(f"[{C_WARN}]✖ orchestration failed: {exc}[/]")
                return 1

            # Reload the persisted messages for the next turn.
            initial_messages = persistence.load_messages(conv_folder)
            console.print()
    finally:
        _close_conv()


if __name__ == "__main__":
    sys.exit(main())
