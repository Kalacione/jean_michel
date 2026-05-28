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
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
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
from . import db
from . import dispatcher
from . import persistence
from .config import (
    CONVERSATIONS_DIR,
    DISPATCH_MODEL,
    MAIN_MODEL,
    UserProfile,
    ensure_dirs,
)
from .events import (
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
from .orchestrator_v2 import (
    AgentSpec,
    load_agent_spec_v2,
    run_main_loop,
)
from .prompts import render_user_memory_index
from .tools import build_registry

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
        "Enter=newline  Alt+Enter=send  Ctrl-D=quit[/]\n"
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


# ---- Pre-warm ------------------------------------------------------------


def _prewarm(console: Console, llm_clients: dict[str, OllamaClient]) -> None:
    """Best-effort warm-up of one or more Ollama models.

    Each model is probed with a tiny no-thinking call. Failures are logged
    but do not stop the CLI — the v2 architecture degrades gracefully when
    the dispatcher model is missing (everything falls through to DEEP).
    """
    for label, llm in llm_clients.items():
        console.print(f"[dim]warming up {label}={llm.model}…[/]", end="")
        try:
            llm.chat_messages(
                messages=[{"role": "user", "content": "ok"}],
                tools=[],
                temperature=0.0,
                thinking=False,
            )
            console.print(" [dim]ready.[/]")
        except Exception as exc:  # noqa: BLE001
            console.print(f" [yellow]warmup failed: {exc}[/]")


# ---- Conversation lifecycle ---------------------------------------------


def _make_conv_folder(conv_id: str) -> Path:
    name = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M") + f"_{conv_id}"
    folder = CONVERSATIONS_DIR / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _create_new_conversation(mode: str) -> tuple[str, Path]:
    conv_id = uuid.uuid4().hex
    conv_folder = _make_conv_folder(conv_id)
    with db.connect() as conn:
        db.create_conversation(
            conn,
            conv_id=conv_id,
            folder_path=str(conv_folder),
            user_language=None,
            mode=mode,
        )
    return conv_id, conv_folder


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

    Returns the final user-facing answer (also printed as a Panel here).
    """
    user_lang = dispatcher.detect_language(user_text)

    # Update conversation language opportunistically.
    if user_lang and user_lang != "und":
        try:
            with db.connect() as conn:
                db.update_conversation_language(conn, conv_id, user_lang)
        except Exception as exc:  # noqa: BLE001
            _log.debug("update_conversation_language failed: %s", exc)

    # --- Tier 0 : dispatch ---
    decision = dispatcher.classify(user_text, dispatch_llm)

    if decision.intent == "alexa":
        console.print(
            f"[dim]→ tier 0 alexa · tool={decision.tool} · "
            f"confidence={decision.confidence}[/]"
        )
        answer = dispatcher.execute_alexa(decision, dispatch_llm, user_lang=user_lang)
    else:
        console.print(
            f"[dim]→ tier 1 deep · confidence={decision.confidence}[/]"
        )
        answer = _run_deep_turn(
            user_text=user_text,
            conv_folder=conv_folder,
            conv_id=conv_id,
            main_llm=main_llm,
            profile=profile,
            mode=mode,
            user_lang=user_lang,
            console=console,
            ask_human_cb=ask_human_cb,
            initial_messages=initial_messages,
        )

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
    return answer


def _run_deep_turn(
    *,
    user_text: str,
    conv_folder: Path,
    conv_id: str,
    main_llm: OllamaClient,
    profile: UserProfile,
    mode: str,
    user_lang: str,
    console: Console,
    ask_human_cb: Callable[[str, str], str],
    initial_messages: list[dict] | None,
) -> str:
    """Engage Tier 1 : load jean-michel spec, build registry, run the main loop."""
    with db.connect() as conn:
        user_memory_block, count = render_user_memory_index(conn)
        if count >= 90:  # cf. USER_MEMORY_WARN_AT
            render_event(
                console,
                MemoryNearCapacity(current_count=count, limit=100),
                mode=mode,
            )
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
        except (KeyError, Exception) as exc:  # noqa: BLE001
            _log.warning("agent_resolver(%r) failed: %s", code, exc)
            return None

    # Tools registry — built once per turn. Permissive grants ; per-agent
    # filtering is enforced by the PreToolUse hook via AgentSpec.tool_grants.
    tools_registry = build_registry(
        conv_folder=conv_folder,
        has_workspace_write=True,
        conv_id=conv_id,
        request_id_provider=lambda: "main",
        sandbox_grants=None,
        sandbox_image=None,
        agent_role="router",
    )

    def emitter(event: Any) -> None:
        render_event(console, event, mode=mode)

    # If we're resuming, replace messages[0] with a freshly rendered
    # system prompt to pick up user_memory updates.
    seeded_messages: list[dict] | None = None
    if initial_messages:
        seeded_messages = list(initial_messages)
        if seeded_messages and seeded_messages[0].get("role") == "system":
            seeded_messages[0] = {
                "role": "system",
                "content": main_agent.system_prompt,
            }

    answer = run_main_loop(
        conv_folder=conv_folder,
        agent=main_agent,
        tools_registry=tools_registry,
        llm_client=main_llm,
        user_text=user_text,
        initial_messages=seeded_messages,
        ask_human_callback=ask_human_cb,
        agent_resolver=agent_resolver,
        event_emitter=emitter,
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
        choices=["analyse", "chat", "vocal"],
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

    @kb.add("escape", "enter")  # Meta+Enter / Alt+Enter
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

    # Pre-warm for chat/vocal modes (analyse defers to first turn).
    if args.mode in {"chat", "vocal"}:
        _prewarm(console, {"dispatch": dispatch_llm, "main": main_llm})

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
        conv_id, conv_folder = _create_new_conversation(args.mode)

    render_splash(console, args.main_model, args.dispatch_model, args.mode)

    session = _build_prompt_session()
    ask_human_cb = make_ask_human(console, session)

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
        return 0

    # ----- Interactive loop -----
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
        except Exception as exc:  # noqa: BLE001
            console.print(f"[{C_WARN}]✖ orchestration failed: {exc}[/]")
            return 1

        # Reload the persisted messages for the next turn.
        initial_messages = persistence.load_messages(conv_folder)
        console.print()


if __name__ == "__main__":
    sys.exit(main())
