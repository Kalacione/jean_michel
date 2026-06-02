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
from collections.abc import Callable
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
from . import db, persistence
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
            f"[{C_WARN}]⚠ user_memory at {event.current_count}/{event.limit} — "
            "consider purging obsolete entries via manage_user_memory(action='delete')[/]"
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


def make_ask_human(console: Console, session: PromptSession) -> Callable[[str, str], str]:
    """Build the ask_human callback that pauses the loop for a human reply."""

    def _ask(question: str, why: str) -> str:
        console.print()
        console.print(
            Panel(
                Group(
                    Text(why, style="dim italic"),
                    Text(""),
                    Text(question, style=C_HUMAN_Q),
                ),
                title="Question",
                border_style="yellow",
                padding=(1, 2),
            )
        )
        answer = session.prompt(
            HTML('<ansiyellow><b>your answer</b></ansiyellow>: '),
            multiline=True,
            prompt_continuation=lambda width, line_number, wrap_count: " " * width,
        )
        return answer.strip()

    return _ask


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
    ask_human_cb: Callable[[str, str], str],
    initial_messages: list[dict] | None,
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
    def ask_human_with_pause(question: str, why: str) -> str:
        status.stop()
        try:
            return ask_human_cb(question, why)
        finally:
            status.start()

    # Print the Tier-0 routing line once the dispatch decision is known.
    def on_dispatch(decision: Any) -> None:
        status.stop()
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
        conv_id, conv_folder = conversation_svc.create_conversation(args.mode)

    render_splash(console, args.main_model, args.dispatch_model, args.mode)

    session = _build_prompt_session()
    ask_human_cb = make_ask_human(console, session)

    def _close_conv() -> None:
        try:
            with db.connect() as conn:
                db.close_conversation(conn, conv_id)
        except Exception as exc:  # noqa: BLE001
            _log.debug("close_conversation failed: %s", exc)

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
            try:
                user_input = session.prompt(
                    HTML('<ansibrightcyan><b>you</b></ansibrightcyan>: '),
                    multiline=True,
                    prompt_continuation=lambda width, line_number, wrap_count: " " * width,
                )
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]bye.[/]")
                return 0
            if user_input.strip().lower() in {"exit", "quit"}:
                console.print("[dim]bye.[/]")
                return 0
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
