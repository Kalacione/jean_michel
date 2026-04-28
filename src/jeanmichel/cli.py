"""CLI: chat loop rendered with `rich`. Consumes orchestrator events."""

from __future__ import annotations

import argparse
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
    ThoughtCaptured,
    ToolCallEmitted,
    ToolResponseRecorded,
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


def render_splash(console: Console, model: str) -> None:
    console.print(Text(SPLASH, style="bold cyan"))
    console.print(f"[dim]model: {model} • Enter=newline  Alt+Enter=send  Ctrl-D=quit[/]\n")


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


# ---- Main loop ------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jean-michel")
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL,
                        help="Ollama model tag (default: %(default)s).")
    parser.add_argument("--show-thoughts", action="store_true",
                        help="Display the agent's thought channel.")
    args = parser.parse_args(argv)

    console = Console()
    render_splash(console, args.model)

    profile = UserProfile.load()
    try:
        llm = OllamaClient(model=args.model)
    except RuntimeError as e:
        console.print(f"[{C_WARN}]{e}[/]")
        return 2

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

        orch = Orchestrator(llm=llm, profile=profile,
                            ask_human_callback=make_ask_human(console, session))
        try:
            render_events(console, orch.run(user_input), show_thoughts=args.show_thoughts)
        except Exception as e:  # noqa: BLE001
            console.print(f"[{C_WARN}]✖ orchestration failed: {e}[/]")
            return 1
        console.print()


if __name__ == "__main__":
    sys.exit(main())
