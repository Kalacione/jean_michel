#!/usr/bin/env python3
"""Admin CLI for jean-michel — manage agents, tool grants, and paradigms.

Usage (one-shot):
    python debug/admin.py agents
    python debug/admin.py agent <code>
    python debug/admin.py tools
    python debug/admin.py paradigms [<agent-code>]
    python debug/admin.py grant <agent> <tool>
    python debug/admin.py revoke <agent> <tool>
    python debug/admin.py bind <agent> <paradigm>
    python debug/admin.py unbind <agent> <paradigm>
    python debug/admin.py toggle-paradigm <code>

Usage (interactive REPL with autocomplete):
    python debug/admin.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))

from prompt_toolkit import PromptSession  # noqa: E402
from prompt_toolkit.completion import Completer, Completion, WordCompleter  # noqa: E402
from prompt_toolkit.history import InMemoryHistory  # noqa: E402
from rich import box  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.markup import escape  # noqa: E402
from rich.table import Table  # noqa: E402

from jeanmichel import config, db  # noqa: E402

console = Console()

# ---------------------------------------------------------------------------
# Autocomplete
# ---------------------------------------------------------------------------

_COMMANDS = [
    "agents", "agent", "tools", "paradigms", "paradigm", "promotions",
    "convs", "purge-orphans",
    "grant", "revoke", "bind", "unbind",
    "add-paradigm", "toggle-paradigm", "set-model",
    "help", "exit", "quit",
]


class _AdminCompleter(Completer):
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._agent_codes: list[str] = []
        self._all_tool_codes: list[str] = []
        self._all_paradigm_codes: list[str] = []
        self._grants_by_agent: dict[str, list[str]] = {}
        self._bound_by_agent: dict[str, list[str]] = {}
        self.refresh()

    def refresh(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            self._agent_codes = [
                r["code"]
                for r in conn.execute(
                    "SELECT code FROM agents WHERE active=1 ORDER BY code"
                ).fetchall()
            ]
            self._all_tool_codes = [
                r["tool_code"]
                for r in conn.execute(
                    "SELECT DISTINCT tool_code FROM agent_tools ORDER BY tool_code"
                ).fetchall()
            ]
            self._all_paradigm_codes = [
                r["code"]
                for r in conn.execute(
                    "SELECT code FROM paradigms ORDER BY code"
                ).fetchall()
            ]
            for code in self._agent_codes:
                agent_id = conn.execute(
                    "SELECT id FROM agents WHERE code=?", (code,)
                ).fetchone()["id"]
                self._grants_by_agent[code] = [
                    r["tool_code"]
                    for r in conn.execute(
                        "SELECT tool_code FROM agent_tools WHERE agent_id=? ORDER BY tool_code",
                        (agent_id,),
                    ).fetchall()
                ]
                self._bound_by_agent[code] = [
                    r["code"]
                    for r in conn.execute(
                        "SELECT p.code FROM paradigms p "
                        "JOIN agent_paradigms ap ON ap.paradigm_id = p.id "
                        "WHERE ap.agent_id = ? ORDER BY p.code",
                        (agent_id,),
                    ).fetchall()
                ]

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor.lstrip()
        parts = text.split()
        ends_space = text.endswith(" ")

        if not parts:
            pos, partial = 0, ""
        elif ends_space:
            pos, partial = len(parts), ""
        else:
            pos, partial = len(parts) - 1, parts[-1]

        candidates: list[str] = []
        if pos == 0:
            candidates = _COMMANDS
        elif pos == 1:
            cmd = parts[0]
            if cmd in ("agent", "paradigms", "grant", "revoke", "bind", "unbind"):
                candidates = self._agent_codes
            elif cmd == "toggle-paradigm":
                candidates = self._all_paradigm_codes
        elif pos == 2:
            cmd = parts[0]
            agent = parts[1] if len(parts) > 1 else ""
            if cmd == "grant":
                granted = set(self._grants_by_agent.get(agent, []))
                candidates = [t for t in self._all_tool_codes if t not in granted]
            elif cmd == "revoke":
                candidates = self._grants_by_agent.get(agent, [])
            elif cmd == "bind":
                bound = set(self._bound_by_agent.get(agent, []))
                candidates = [p for p in self._all_paradigm_codes if p not in bound]
            elif cmd == "unbind":
                candidates = self._bound_by_agent.get(agent, [])

        for candidate in candidates:
            if candidate.startswith(partial):
                yield Completion(candidate, start_position=-len(partial))


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _show_agents(db_path: Path) -> None:
    with db.connect(db_path) as conn:
        agents = db.list_active_agents(conn)
        rows = []
        for a in agents:
            grants = db.load_tool_grants(conn, a.id)
            paradigms = db.load_paradigms_for_agent(conn, a.id, "analyse")
            rows.append((a, len(grants), len(paradigms)))

    from jeanmichel import config as _cfg
    t = Table(box=box.ROUNDED, header_style="bold cyan")
    t.add_column("Code", style="bold")
    t.add_column("Name")
    t.add_column("Role", style="dim")
    t.add_column("Model")
    t.add_column("Think", justify="center")
    t.add_column("Temp", justify="right")
    t.add_column("Tools", justify="right")
    t.add_column("Paradigms", justify="right")
    for a, nt, np in rows:
        eff = _cfg.agent_model(a.code, a.role, a.model_override)
        model_cell = eff if a.model_override else f"[dim]{eff} (default)[/dim]"
        t.add_row(
            a.code, a.name, a.role, model_cell,
            "✓" if a.thinking_mode else "·",
            str(a.temperature), str(nt), str(np),
        )
    console.print(t)


def _show_agent(db_path: Path, code: str) -> None:
    with db.connect(db_path) as conn:
        try:
            agent = db.get_agent_by_code(conn, code)
        except KeyError as e:
            console.print(f"[red]{e}[/red]")
            return
        grants = db.load_tool_grants(conn, agent.id)
        paradigms = db.load_paradigms_for_agent(conn, agent.id, "analyse")
        bound_rows = conn.execute(
            "SELECT p.code FROM paradigms p "
            "JOIN agent_paradigms ap ON ap.paradigm_id = p.id "
            "WHERE ap.agent_id = ? ORDER BY p.code",
            (agent.id,),
        ).fetchall()
        bound_codes = {r[0] for r in bound_rows}

    from jeanmichel import config as _cfg
    eff = _cfg.agent_model(agent.code, agent.role, agent.model_override)
    model_note = "" if agent.model_override else " [dim](role default)[/dim]"
    console.rule(f"[bold cyan]{agent.name}[/bold cyan] [dim]({agent.code})[/dim]")
    console.print(
        f"[dim]Role:[/dim] {agent.role}  "
        f"[dim]Model:[/dim] {eff}{model_note}  "
        f"[dim]Think:[/dim] {'on' if agent.thinking_mode else 'off'}  "
        f"[dim]Temp:[/dim] {agent.temperature}"
    )
    console.print()
    console.print("[bold]Mission[/bold]")
    console.print(f"  {agent.mission}")
    console.print()

    console.print("[bold]Tools[/bold]")
    if grants:
        for g in grants:
            console.print(f"  [green]● {g}[/green]")
    else:
        console.print("  [dim](none)[/dim]")
    console.print()

    console.print("[bold]Paradigms[/bold]  [dim](G=global, B=explicitly bound)[/dim]")
    if not paradigms:
        console.print("  [dim](none)[/dim]")
    else:
        cur_section, cur_cat = None, None
        for p in paradigms:
            if p.section_code != cur_section:
                cur_section = p.section_code
                console.print(f"  [bold yellow]{p.section_code.upper()}[/bold yellow]")
            if p.category_code != cur_cat:
                cur_cat = p.category_code
                console.print(f"    [dim]{p.category_title}[/dim]")
            tag = "[dim]G[/dim]" if p.code not in bound_codes else "[cyan]B[/cyan]"
            console.print(f"      {tag} [bold]{p.code}[/bold]  {p.title}")


def _show_convs(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, folder_path, status, mode, created_at FROM conversations "
            "ORDER BY created_at DESC LIMIT 20"
        ).fetchall()

    if not rows:
        console.print("[dim]No conversations in database.[/dim]")
        return

    t = Table(box=box.ROUNDED, header_style="bold cyan", title="Conversations (last 20)")
    t.add_column("ID prefix", style="bold", no_wrap=True)
    t.add_column("Mode", style="dim")
    t.add_column("Status")
    t.add_column("Created", style="dim")
    t.add_column("Folder", justify="center")
    for r in rows:
        folder_exists = Path(r["folder_path"]).is_dir()
        folder_mark = "[green]✓[/green]" if folder_exists else "[red]✗ orphan[/red]"
        status_color = {
            "active": "green", "closed": "dim", "awaiting_human": "yellow"
        }.get(r["status"], "white")
        t.add_row(
            r["id"][:12],
            r["mode"],
            f"[{status_color}]{r['status']}[/{status_color}]",
            r["created_at"][:16],
            folder_mark,
        )
    console.print(t)


def _exec_purge_orphans(
    db_path: Path,
    session: PromptSession | None = None,
) -> None:
    """Remove DB records for conversations whose on-disk folder no longer exists."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, folder_path, status, created_at FROM conversations ORDER BY created_at"
        ).fetchall()

    orphans = [
        (r["id"], r["folder_path"], r["status"])
        for r in rows
        if not Path(r["folder_path"]).is_dir()
    ]

    if not orphans:
        console.print("[green]No orphaned conversations found.[/green]")
        return

    console.print(f"\n[yellow]{len(orphans)} orphaned conversation(s):[/yellow]")
    for conv_id, folder, status in orphans:
        console.print(f"  [dim]{conv_id[:12]}[/dim]  {Path(folder).name}  [dim]({status})[/dim]")
    console.print()

    try:
        if session is not None:
            answer = session.prompt("Delete these records (cascade)? [y/N]: ").strip().lower()
        else:
            answer = input("Delete these records (cascade)? [y/N]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Aborted.[/dim]")
        return

    if answer not in ("y", "yes"):
        console.print("[dim]Aborted.[/dim]")
        return

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        for conv_id, _, _ in orphans:
            conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
        conn.commit()
    console.print(f"[green]Deleted {len(orphans)} orphaned record(s) (requests + artifacts cascaded).[/green]")


def _show_tools(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        all_codes = [
            r["tool_code"]
            for r in conn.execute(
                "SELECT DISTINCT tool_code FROM agent_tools ORDER BY tool_code"
            ).fetchall()
        ]
        agents = conn.execute(
            "SELECT id, code FROM agents WHERE active=1 ORDER BY code"
        ).fetchall()
        grants_by_tool: dict[str, list[str]] = {c: [] for c in all_codes}
        for a in agents:
            for g in conn.execute(
                "SELECT tool_code FROM agent_tools WHERE agent_id=?", (a["id"],)
            ).fetchall():
                if g["tool_code"] in grants_by_tool:
                    grants_by_tool[g["tool_code"]].append(a["code"])

    if not all_codes:
        console.print("[dim]No tool grants in database.[/dim]")
        return
    t = Table(box=box.ROUNDED, header_style="bold cyan")
    t.add_column("Tool code", style="bold")
    t.add_column("Granted to")
    for code in all_codes:
        t.add_row(code, ", ".join(grants_by_tool[code]) or "[dim](none)[/dim]")
    console.print(t)


def _show_paradigms(db_path: Path, agent_code: str | None = None) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT s.code AS sec, c.code AS cat, c.title AS cat_title,
                   p.code, p.title, p.is_global, p.active,
                   substr(p.content, 1, 60) AS preview
            FROM paradigms p
            JOIN categories c ON c.id = p.category_id
            JOIN sections   s ON s.id = c.section_id
            ORDER BY s.order_priority, c.order_priority, p.order_priority, p.id
        """).fetchall()

        bound_codes: set[str] = set()
        if agent_code:
            a = conn.execute(
                "SELECT id FROM agents WHERE code=?", (agent_code,)
            ).fetchone()
            if a is None:
                console.print(f"[red]Unknown agent: {agent_code}[/red]")
                return
            bound_codes = {
                r["code"]
                for r in conn.execute(
                    "SELECT p.code FROM paradigms p "
                    "JOIN agent_paradigms ap ON ap.paradigm_id = p.id "
                    "WHERE ap.agent_id = ?",
                    (a["id"],),
                ).fetchall()
            }

    t = Table(box=box.ROUNDED, header_style="bold cyan")
    t.add_column("Code", style="bold", no_wrap=True)
    t.add_column("Title")
    t.add_column("Section.Cat", style="dim", no_wrap=True)
    t.add_column("G", justify="center")
    t.add_column("On", justify="center")
    if agent_code:
        t.add_column("Applied", justify="center")
    t.add_column("Preview", style="dim", max_width=54)
    for r in rows:
        active_mark = "[green]✓[/green]" if r["active"] else "[red]✗[/red]"
        global_mark = "G" if r["is_global"] else "·"
        row_data = [
            r["code"], r["title"],
            f"{r['sec']}.{r['cat']}",
            global_mark, active_mark,
        ]
        if agent_code:
            if r["is_global"]:
                row_data.append("[dim]G[/dim]")
            elif r["code"] in bound_codes:
                row_data.append("[cyan]B[/cyan]")
            else:
                row_data.append("[dim]·[/dim]")
        row_data.append(escape((r["preview"] or "").replace("\n", " ")))
        t.add_row(*row_data)
    console.print(t)
    console.print("[dim]Full content: [/dim][cyan]paradigm <code>[/cyan]")
    if agent_code:
        console.print("[dim]G=global (always applied), B=explicitly bound, ·=not applied[/dim]")


def _show_paradigm_detail(db_path: Path, code: str) -> None:
    """Full view of one paradigm: the injected content + the dev rationale + bindings."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT p.code, p.title, p.content, p.rationale, p.is_global, p.active, "
            "p.order_priority, s.code AS sec, c.code AS cat "
            "FROM paradigms p JOIN categories c ON c.id=p.category_id "
            "JOIN sections s ON s.id=c.section_id WHERE p.code=?",
            (code,),
        ).fetchone()
        if row is None:
            console.print(f"[red]Unknown paradigm: {code}[/red]")
            return
        agents = [
            r["code"] for r in conn.execute(
                "SELECT a.code FROM agent_paradigms ap JOIN agents a ON a.id=ap.agent_id "
                "JOIN paradigms p ON p.id=ap.paradigm_id WHERE p.code=? ORDER BY a.code", (code,)
            ).fetchall()
        ]
        modes = [
            r["mode"] for r in conn.execute(
                "SELECT mode FROM paradigm_modes pm JOIN paradigms p ON p.id=pm.paradigm_id "
                "WHERE p.code=?", (code,)
            ).fetchall()
        ]
    state = "[green]active[/green]" if row["active"] else "[red]INACTIVE[/red]"
    console.print(f"[bold cyan]{escape(row['code'])}[/bold cyan] — {escape(row['title'])}")
    console.print(
        f"[dim]{row['sec']}.{row['cat']}  ·  {'global' if row['is_global'] else 'bound'}  ·  "
        f"order {row['order_priority']}  ·  [/dim]{state}"
    )
    if modes:
        console.print(f"[dim]modes: {', '.join(modes)}[/dim]")
    console.print(f"[dim]agents: {', '.join(agents) if agents else '(none — global or unbound)'}[/dim]")
    console.print("\n[bold]content (injected verbatim into the prompt):[/bold]")
    console.print(row["content"], markup=False)
    if row["rationale"]:
        console.print("\n[bold dim]rationale (dev note — NOT injected):[/bold dim]")
        console.print(row["rationale"], markup=False, style="dim")


def _review_promotions(db_path: Path, session: PromptSession | None) -> None:
    """Review queued paradigm-promotion candidates (pending_consolidation kind='rule')
    across conversations : create (DARK) / bind an existing one / drop."""
    if session is None:
        console.print("[red]promotions is interactive — run it in the REPL (jm.sh --admin).[/red]")
        return
    import json

    from jeanmichel.service import consolidation

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT conversation_id, payload FROM pending_consolidation "
            "WHERE kind='rule' AND status='pending' ORDER BY id"
        ).fetchall()
    cands = [(r["conversation_id"], json.loads(r["payload"])) for r in rows]
    if not cands:
        console.print("[dim]Aucune promotion de paradigme en attente.[/dim]")
        return

    for i, (conv_id, c) in enumerate(cands):
        sim = ", ".join(m["code"] for m in c.get("existing_matches", []))
        console.print(f"\n[bold magenta]Promotion {i + 1}/{len(cands)}[/bold magenta]  ·  "
                      f"{c['section_code']}.{c['category_code']}")
        console.print(f"[bold]{escape(c['title'])}[/bold]")
        console.print(c["content"], markup=False)
        if c.get("grounding_quote"):
            console.print(f"[dim]source : {escape(c['grounding_quote'])}[/dim]")
        if sim:
            console.print(f"[yellow]paradigmes proches : {sim} (binder l'un d'eux évite un doublon)[/yellow]")
        choice = (session.prompt(
            "[c]réer (inactif) / [b]ind existant / [d]rop / [s]kip / [q]uit (défaut s): "
        ).strip().lower() or "s")
        if choice == "q":
            break
        if choice == "s":
            continue
        try:
            if choice == "d":
                consolidation.remove_pending(conv_id, c)
                console.print("[dim]drop.[/dim]")
            elif choice == "b":
                para = session.prompt("  paradigme existant à binder (code): ").strip()
                agent = session.prompt("  agent (code): ").strip()
                with sqlite3.connect(db_path) as conn:
                    consolidation.apply_rule_candidate(
                        conn, c, action="bind", bind_agent=agent, bind_to_code=para
                    )
                consolidation.mark_applied(conv_id, c)
                console.print(f"[green]✓ bind {para} → {agent}[/green]")
            else:  # create
                with sqlite3.connect(db_path) as conn:
                    res = consolidation.apply_rule_candidate(conn, c, action="create")
                consolidation.mark_applied(conv_id, c)
                console.print(f"[green]✓ créé (inactif) : {res['code']} "
                              "— active via toggle-paradigm + bind <agent> <code>[/green]")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]✖ {exc}[/red]")


# ---------------------------------------------------------------------------
# Write command handlers
# ---------------------------------------------------------------------------

def _exec_grant(db_path: Path, agent: str, tool: str, completer: _AdminCompleter | None) -> None:
    try:
        with db.connect(db_path) as conn:
            db.grant_tool(conn, agent, tool)
        console.print(f"[green]Granted[/green] [bold]{tool}[/bold] → [bold]{agent}[/bold]")
        if completer:
            completer.refresh()
    except KeyError as e:
        console.print(f"[red]{e}[/red]")


def _exec_revoke(db_path: Path, agent: str, tool: str, completer: _AdminCompleter | None) -> None:
    try:
        with db.connect(db_path) as conn:
            db.revoke_tool(conn, agent, tool)
        console.print(f"[yellow]Revoked[/yellow] [bold]{tool}[/bold] from [bold]{agent}[/bold]")
        if completer:
            completer.refresh()
    except KeyError as e:
        console.print(f"[red]{e}[/red]")


def _exec_bind(db_path: Path, agent: str, paradigm: str, completer: _AdminCompleter | None) -> None:
    try:
        with db.connect(db_path) as conn:
            db.bind_paradigm(conn, agent, paradigm)
        console.print(f"[green]Bound[/green] [bold]{paradigm}[/bold] → [bold]{agent}[/bold]")
        if completer:
            completer.refresh()
    except KeyError as e:
        console.print(f"[red]{e}[/red]")


def _exec_unbind(db_path: Path, agent: str, paradigm: str, completer: _AdminCompleter | None) -> None:
    try:
        with db.connect(db_path) as conn:
            db.unbind_paradigm(conn, agent, paradigm)
        console.print(f"[yellow]Unbound[/yellow] [bold]{paradigm}[/bold] from [bold]{agent}[/bold]")
        if completer:
            completer.refresh()
    except KeyError as e:
        console.print(f"[red]{e}[/red]")


def _exec_toggle(db_path: Path, code: str, completer: _AdminCompleter | None) -> None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT active FROM paradigms WHERE code=?", (code,)
        ).fetchone()
    if row is None:
        console.print(f"[red]Unknown paradigm: {code}[/red]")
        return
    new_active = not bool(row[0])
    try:
        with db.connect(db_path) as conn:
            db.set_paradigm_active(conn, code, new_active)
        status = "[green]enabled[/green]" if new_active else "[yellow]disabled[/yellow]"
        console.print(f"Paradigm [bold]{code}[/bold] → {status}")
        if completer:
            completer.refresh()
    except KeyError as e:
        console.print(f"[red]{e}[/red]")


def _exec_set_model(db_path: Path, agent: str, model: str, completer: _AdminCompleter | None) -> None:
    """Set an agent's per-agent model override, or clear it (→ role default)."""
    cleared = model.strip().lower() in ("--clear", "default", "none", "")
    try:
        with db.connect(db_path) as conn:
            db.set_agent_model(conn, agent, None if cleared else model)
        if cleared:
            console.print(f"[yellow]Cleared[/yellow] model override of [bold]{agent}[/bold] → role default")
        else:
            console.print(f"[green]Set[/green] [bold]{agent}[/bold] model → [bold]{model}[/bold]")
        if completer:
            completer.refresh()
    except KeyError as e:
        console.print(f"[red]{e}[/red]")


def _exec_add_paradigm(
    db_path: Path,
    session: PromptSession,
    completer: _AdminCompleter | None,
) -> None:
    console.print("\n[bold]Add a new paradigm[/bold]  [dim]Ctrl+C to cancel[/dim]\n")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cats = conn.execute(
            "SELECT s.code AS sec, c.code AS cat, c.title "
            "FROM categories c JOIN sections s ON s.id = c.section_id "
            "WHERE c.active=1 AND s.active=1 "
            "ORDER BY s.order_priority, c.order_priority"
        ).fetchall()
    cat_keys = [f"{r['sec']}.{r['cat']}" for r in cats]

    console.print("[dim]Available categories:[/dim]")
    for k in cat_keys:
        console.print(f"  {k}")
    console.print()

    try:
        category = session.prompt(
            "Category (section.category): ",
            completer=WordCompleter(cat_keys, sentence=True),
        ).strip()
        if category not in cat_keys:
            console.print("[red]Invalid category.[/red]")
            return
        sec_code, cat_code = category.split(".", 1)

        code = session.prompt("Code (snake_case): ").strip()
        if not code:
            console.print("[red]Code cannot be empty.[/red]")
            return

        title = session.prompt("Title: ").strip()
        if not title:
            console.print("[red]Title cannot be empty.[/red]")
            return

        console.print("[dim]Content (Markdown bullets — Alt+Enter to submit):[/dim]")
        content = session.prompt("", multiline=True).strip()
        if not content:
            console.print("[red]Content cannot be empty.[/red]")
            return

        rationale = session.prompt("Rationale [optional]: ").strip() or None

        is_global_str = session.prompt("Global? (y/N): ").strip().lower()
        is_global = is_global_str in ("y", "yes")

        order_str = session.prompt("Order priority [100]: ").strip()
        order_priority = int(order_str) if order_str.isdigit() else 100

    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled.[/dim]")
        return

    try:
        with db.connect(db_path) as conn:
            pid = db.create_paradigm(
                conn,
                section_code=sec_code,
                category_code=cat_code,
                code=code,
                title=title,
                content=content,
                rationale=rationale,
                is_global=is_global,
                order_priority=order_priority,
            )
        console.print(f"\n[green]Created[/green] paradigm [bold]{code}[/bold] (id={pid})")
        if completer:
            completer.refresh()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


def _show_help() -> None:
    console.print("""
[bold]Commands:[/bold]
  [cyan]agents[/cyan]                        List all active agents
  [cyan]agent[/cyan] <code>                  Show full agent profile (tools + paradigms)
  [cyan]tools[/cyan]                         List all known tool codes and their holders
  [cyan]paradigms[/cyan] [<agent>]           List all paradigms (+ preview); if agent given, applied status
  [cyan]paradigm[/cyan] <code>               Show one paradigm in full (content + rationale + bindings)
  [cyan]promotions[/cyan]                    Review queued rule→paradigm promotions (create / bind / drop)
  [cyan]convs[/cyan]                         List recent conversations with folder existence check
  [cyan]purge-orphans[/cyan]                 Remove DB records for conversations with missing folders
  [cyan]grant[/cyan] <agent> <tool>          Grant a tool to an agent
  [cyan]revoke[/cyan] <agent> <tool>         Revoke a tool from an agent
  [cyan]bind[/cyan] <agent> <paradigm>       Bind a paradigm to an agent
  [cyan]unbind[/cyan] <agent> <paradigm>     Unbind a paradigm from an agent
  [cyan]add-paradigm[/cyan]                  Interactive wizard to create a new paradigm
  [cyan]toggle-paradigm[/cyan] <code>        Enable/disable a paradigm (toggle)
  [cyan]set-model[/cyan] <agent> <model>     Set an agent's model override (--clear → role default)
  [cyan]help[/cyan]                          Show this help
  [cyan]exit[/cyan] / [cyan]quit[/cyan]                  Exit the REPL

[dim]Autocomplete works on all arguments (Tab). In add-paradigm, Alt+Enter submits multi-line content.[/dim]
""")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def run_command(
    parts: list[str],
    db_path: Path,
    session: PromptSession | None = None,
    completer: _AdminCompleter | None = None,
) -> bool:
    """Execute a parsed command. Returns False to signal exit."""
    if not parts:
        return True
    cmd, args = parts[0], parts[1:]

    if cmd == "agents":
        _show_agents(db_path)
    elif cmd == "agent":
        if not args:
            console.print("[red]Usage: agent <code>[/red]")
        else:
            _show_agent(db_path, args[0])
    elif cmd == "tools":
        _show_tools(db_path)
    elif cmd == "paradigms":
        _show_paradigms(db_path, args[0] if args else None)
    elif cmd == "paradigm":
        if not args:
            console.print("[red]Usage: paradigm <code>[/red]")
        else:
            _show_paradigm_detail(db_path, args[0])
    elif cmd == "promotions":
        _review_promotions(db_path, session)
    elif cmd == "convs":
        _show_convs(db_path)
    elif cmd == "purge-orphans":
        _exec_purge_orphans(db_path, session=session)
    elif cmd == "grant":
        if len(args) < 2:
            console.print("[red]Usage: grant <agent> <tool>[/red]")
        else:
            _exec_grant(db_path, args[0], args[1], completer)
    elif cmd == "revoke":
        if len(args) < 2:
            console.print("[red]Usage: revoke <agent> <tool>[/red]")
        else:
            _exec_revoke(db_path, args[0], args[1], completer)
    elif cmd == "bind":
        if len(args) < 2:
            console.print("[red]Usage: bind <agent> <paradigm>[/red]")
        else:
            _exec_bind(db_path, args[0], args[1], completer)
    elif cmd == "unbind":
        if len(args) < 2:
            console.print("[red]Usage: unbind <agent> <paradigm>[/red]")
        else:
            _exec_unbind(db_path, args[0], args[1], completer)
    elif cmd == "toggle-paradigm":
        if not args:
            console.print("[red]Usage: toggle-paradigm <code>[/red]")
        else:
            _exec_toggle(db_path, args[0], completer)
    elif cmd == "set-model":
        if len(args) < 2:
            console.print("[red]Usage: set-model <agent> <model | --clear>[/red]")
        else:
            _exec_set_model(db_path, args[0], args[1], completer)
    elif cmd == "add-paradigm":
        if session is None:
            console.print("[red]add-paradigm is only available in interactive mode.[/red]")
        else:
            _exec_add_paradigm(db_path, session, completer)
    elif cmd == "help":
        _show_help()
    elif cmd in ("exit", "quit"):
        return False
    else:
        console.print(
            f"[red]Unknown command:[/red] {cmd!r}  — type [cyan]help[/cyan] for the list."
        )
    return True


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def repl(db_path: Path) -> None:
    completer = _AdminCompleter(db_path)
    session: PromptSession[str] = PromptSession(
        history=InMemoryHistory(),
        completer=completer,
    )
    console.print(
        "[bold cyan]jm-admin[/bold cyan]  "
        "[dim]Tab=autocomplete  ↑↓=history  [/dim][cyan]help[/cyan][dim] for commands[/dim]"
    )
    while True:
        try:
            line = session.prompt("jm-admin> ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Bye.[/dim]")
            break
        parts = line.split()
        if not run_command(parts, db_path, session=session, completer=completer):
            console.print("[dim]Bye.[/dim]")
            break


def main() -> None:
    db_path = config.DB_PATH
    if not db_path.exists():
        console.print(f"[red]Database not found: {db_path}[/red]")
        console.print("Run [cyan]./jm.sh --install[/cyan] first.")
        sys.exit(1)

    args = sys.argv[1:]
    if not args:
        repl(db_path)
    else:
        run_command(args, db_path)


if __name__ == "__main__":
    main()
