"""CLI: chat loop rendered with `rich`. Consumes orchestrator events."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from . import db
from .config import DEFAULT_OLLAMA_MODEL, UserProfile
from .llm import OllamaClient
from .orchestrator import (
    AgentStarted,
    ConversationStarted,
    DelegationStarted,
    FinalAnswer,
    HumanAnswerReceived,
    HumanQuestionAsked,
    OrchestrationFailed,
    Orchestrator,
    RecursionLimitReached,
    SummaryUpdated,
    ThoughtCaptured,
    ToolCallEmitted,
    ToolResponseRecorded,
    TurnStarted,
    WallClockExceeded,
)

# ---- Style palette --------------------------------------------------------

C_USER     = "bold cyan"
C_AGENT    = "bold magenta"
C_TOOL     = "yellow"
C_THOUGHT  = "dim italic"
C_HUMAN_Q  = "bold yellow"
C_FINAL    = "bold green"
C_WARN     = "bold red"


# ---- Splash ---------------------------------------------------------------

SPLASH = r"""
     _                  __  __ _      _          _
    | | ___  __ _ _ __ |  \/  (_) ___| |__   ___| |
 _  | |/ _ \/ _` | '_ \| |\/| | |/ __| '_ \ / _ \ |
| |_| |  __/ (_| | | | | |  | | | (__| | | |  __/ |
 \___/ \___|\__,_|_| |_|_|  |_|_|\___|_| |_|\___|_|
"""


def render_splash(console: Console, model: str, mode: str = "analyse") -> None:
    console.print(Text(SPLASH, style="bold cyan"))
    console.print(f"[dim]model: {model} · mode: {mode} • Enter=newline  Alt+Enter=send  Ctrl-D=quit[/]\n")


# ---- Event renderer -------------------------------------------------------

def render_events(console: Console, events: Iterable[object],
                  show_thoughts: bool) -> None:
    gen = iter(events)
    awaiting_human = False  # True right after HumanQuestionAsked — skip spinner
    while True:
        try:
            if awaiting_human:
                ev = next(gen)
            else:
                with console.status("[dim]thinking…[/]", spinner="dots"):
                    ev = next(gen)
        except StopIteration:
            break
        awaiting_human = False

        if isinstance(ev, ConversationStarted):
            console.print(Rule(
                Text(f"conversation {ev.conversation_id} • lang={ev.user_language}",
                     style="dim"),
                style="dim",
            ))

        elif isinstance(ev, AgentStarted):
            indent = "  " * ev.depth
            console.print(
                f"{indent}[{C_AGENT}]→ {ev.agent_code}[/] "
                f"[dim](depth={ev.depth})[/]"
            )

        elif isinstance(ev, ThoughtCaptured):
            if show_thoughts:
                console.print(Panel(
                    Text(ev.text.strip(), style=C_THOUGHT),
                    title=f"thought · {ev.agent_code}",
                    border_style="dim",
                    padding=(0, 1),
                ))

        elif isinstance(ev, ToolCallEmitted):
            args_preview = ", ".join(f"{k}={_truncate(v)}"
                                     for k, v in ev.arguments.items())
            console.print(
                f"  [{C_TOOL}]🔧 {ev.tool_name}[/] [dim]({args_preview})[/]"
            )

        elif isinstance(ev, ToolResponseRecorded):
            if ev.tool_name in ("workspace_create_file", "workspace_str_replace"):
                try:
                    rdata = json.loads(ev.response)
                    if "error" not in rdata:
                        fpath = rdata.get("path", "?")
                        if ev.tool_name == "workspace_create_file":
                            nb = rdata.get("bytes_written", "?")
                            console.print(
                                f"  [green]📄 workspace/{fpath}[/] [dim]({nb} bytes)[/]"
                            )
                        else:
                            console.print(
                                f"  [green]✏ workspace/{fpath}[/] [dim]modifié[/]"
                            )
                        continue
                except Exception:  # noqa: BLE001
                    pass
            console.print(
                f"  [dim]↳ {ev.tool_name} → {_truncate(ev.response, 100)}[/]"
            )

        elif isinstance(ev, DelegationStarted):
            console.print(
                f"  [{C_AGENT}]↳ delegating to {ev.child_agent}[/] "
                f"[dim]: {_truncate(ev.briefing, 80)}[/]"
            )

        elif isinstance(ev, HumanQuestionAsked):
            # The callback will run on the very next next(gen) call — skip the
            # spinner so Rich doesn't fight prompt_toolkit for the terminal.
            awaiting_human = True

        elif isinstance(ev, HumanAnswerReceived):
            console.print("  [dim]↳ human answered.[/]")

        elif isinstance(ev, TurnStarted):
            console.print(Rule(
                Text(f"turn {ev.turn_index}", style="dim"),
                style="dim",
            ))

        elif isinstance(ev, SummaryUpdated):
            console.print("[dim]· summary updated[/]")

        elif isinstance(ev, WallClockExceeded):
            console.print(
                f"[{C_WARN}]⏱ Wall-clock exceeded ({ev.scope}) — "
                f"{ev.elapsed_seconds:.1f}s · {ev.agent_code}[/]"
            )

        elif isinstance(ev, RecursionLimitReached):
            console.print(
                f"[{C_WARN}]⚠ recursion limit reached at depth {ev.depth} "
                f"({ev.agent_code}).[/]"
            )

        elif isinstance(ev, OrchestrationFailed):
            console.print(f"[{C_WARN}]✖ {ev.reason}[/]")

        elif isinstance(ev, FinalAnswer):
            console.print()
            console.print(Panel(
                Markdown(ev.text),
                title="Jean-Michel",
                border_style="green",
                padding=(1, 2),
            ))


def _truncate(value: object, max_len: int = 60) -> str:
    s = str(value).replace("\n", " ")
    return s if len(s) <= max_len else s[:max_len - 1] + "…"


# ---- ask_human callback ---------------------------------------------------

def make_ask_human(console: Console, session: PromptSession):
    def _ask(question: str, why: str) -> str:
        console.print()
        console.print(Panel(
            Group(
                Text(why, style="dim italic"),
                Text(""),
                Text(question, style=C_HUMAN_Q),
            ),
            title="Question",
            border_style="yellow",
            padding=(1, 2),
        ))
        answer = session.prompt(
            HTML('<ansiyellow><b>your answer</b></ansiyellow>: '),
            multiline=True,
            prompt_continuation=lambda width, line_number, wrap_count: " " * width,
        )
        return answer.strip()
    return _ask


# ---- Pre-warm -------------------------------------------------------------

def _prewarm(llm: OllamaClient, model: str, console: Console) -> None:
    console.print(f"[dim]warming up {model}\u2026[/]", end="")
    try:
        llm.chat(system="You are a warmup probe.", user="ok",
                 tools=[], temperature=0.0, thinking=False)
        console.print(" [dim]ready.[/]")
    except Exception as e:  # noqa: BLE001
        console.print(f" [yellow]warmup failed: {e}[/]")


# ---- Main loop ------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jean-michel")
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL,
                        help="Ollama model tag (default: %(default)s).")
    parser.add_argument("--show-thoughts", action="store_true",
                        help="Display the agent's thought channel.")
    parser.add_argument("--mode", choices=["analyse", "chat", "vocal"], default="analyse",
                        help="Conversation mode (default: analyse).")
    parser.add_argument("--resume", nargs="?", const="__last__", default=None,
                        metavar="CONV_ID",
                        help="Resume a conversation. Without CONV_ID: resumes the "
                             "most recent active conversation.")
    parser.add_argument("--list-conv", action="store_true",
                        help="List recent active conversations and exit.")
    parser.add_argument("--once", metavar="TEXT",
                        help="Process a single prompt non-interactively then exit.")
    args = parser.parse_args(argv)

    console = Console()

    # ---- --list-conv (no LLM needed) -------------------------------------
    if args.list_conv:
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
                r["id"][:12], r["mode"], r["status"],
                r["user_language"] or "?",
                r["created_at"][:16], r["modified_at"][:16],
            )
        console.print(table)
        return 0

    render_splash(console, args.model, args.mode)

    profile = UserProfile.load()
    try:
        llm = OllamaClient(model=args.model)
    except RuntimeError as e:
        console.print(f"[{C_WARN}]{e}[/]")
        return 2

    if args.mode in {"chat", "vocal"}:
        _prewarm(llm, args.model, console)

    kb = KeyBindings()

    @kb.add("enter")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    @kb.add("escape", "enter")   # Meta+Enter / Alt+Enter
    def _submit(event):
        event.current_buffer.validate_and_handle()

    session: PromptSession[str] = PromptSession(
        history=InMemoryHistory(),
        key_bindings=kb,
    )

    # ---- --resume --------------------------------------------------------
    if args.resume is not None:
        with db.connect() as conn:
            if args.resume == "__last__":
                rows = db.list_active_conversations(conn, limit=1)
                row = rows[0] if rows else None
            else:
                row = db.get_conversation(conn, args.resume)
        if row is None:
            console.print("[red]Conversation not found or already closed.[/]")
            return 1
        if row["status"] not in {"active", "awaiting_human"}:
            console.print(f"[red]Conversation {row['id'][:12]} is '{row['status']}' — cannot resume.[/]")
            return 1
        orch = Orchestrator(llm=llm, profile=profile, mode=row["mode"],
                            conv_id=row["id"],
                            ask_human_callback=make_ask_human(console, session))
        orch.resume_conversation(
            folder_path=row["folder_path"],
            user_language=row["user_language"] or "und",
        )
        args.mode = row["mode"]
        console.print(f"[dim]Resumed conversation {row['id'][:12]} (mode: {row['mode']})[/]\n")
    else:
        # New conversation — bootstrap the folder before first input.
        orch = Orchestrator(llm=llm, profile=profile, mode=args.mode,
                            ask_human_callback=make_ask_human(console, session))
        orch.bootstrap_conversation()

    # --once: non-interactive single-turn mode (used by jm.sh --meta-analysis etc.)
    if args.once:
        try:
            render_events(console, orch.run(args.once), show_thoughts=args.show_thoughts)
        except Exception as e:  # noqa: BLE001
            console.print(f"[{C_WARN}]\u2716 orchestration failed: {e}[/]")
        finally:
            orch.close_conversation()
        return 0

    while True:
        try:
            user_input = session.prompt(
                HTML('<ansibrightcyan><b>you</b></ansibrightcyan>: '),
                multiline=True,
                prompt_continuation=lambda width, line_number, wrap_count: " " * width,
            )
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye.[/]")
            orch.close_conversation()
            return 0
        if user_input.strip().lower() in {"exit", "quit"}:
            console.print("[dim]bye.[/]")
            orch.close_conversation()
            return 0
        if not user_input.strip():
            continue

        try:
            render_events(console, orch.run(user_input), show_thoughts=args.show_thoughts)
        except Exception as e:  # noqa: BLE001
            console.print(f"[{C_WARN}]\u2716 orchestration failed: {e}[/]")
            orch.cleanup_sandbox()
            return 1

        console.print()

if __name__ == "__main__":
    sys.exit(main())
