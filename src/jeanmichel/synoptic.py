"""Synoptic diagram of the agent chains — generated from the live DB.

A 'schéma synoptique' (electronics-style) of how agents hand off to each other,
rendered as a Mermaid flowchart + a roster table. Generated from jeanmichel.db
(the source of truth) so it never goes stale:

    ./jm.sh --synoptic            # writes docs/agents_synoptic.md
    ./jm.sh --synoptic --stdout   # prints to stdout

The delegation edges come from `agent_delegation_targets`; the deliberation
agents (critical-coder / sergent-kiss) are engine-invoked (not via delegate_to)
so they are shown in a dedicated subgraph.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import config, db

# Engine-invoked (by the deliberation engine, not via the router's delegate_to).
_DELIB_AGENTS = ("critical-coder", "sergent-kiss")


def _nid(code: str) -> str:
    """Mermaid-safe node id."""
    return code.replace("-", "_").replace(".", "_")


def _model_label(model_override: str | None) -> str:
    return model_override or "default"


def _head_commit() -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(config.REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() if r.returncode == 0 else "?"
    except (subprocess.SubprocessError, OSError):
        return "?"


def render_synoptic(conn) -> str:
    """Return the synoptic markdown (mermaid flowchart + roster table)."""
    agents = db.list_active_agents(conn)
    by_code = {a.code: a for a in agents}
    # list_active_agents does not select model_override → fetch it directly.
    models = {
        r[0]: r[1]
        for r in conn.execute("SELECT code, model_override FROM agents WHERE active = 1")
    }
    deleg = {a.code: sorted(db.load_delegation_targets(conn, a.id)) for a in agents}
    tool_n = {a.code: len(db.load_tool_grants(conn, a.id)) for a in agents}
    para_n = {
        a.code: conn.execute(
            "SELECT COUNT(*) FROM agent_paradigms WHERE agent_id = ?", (a.id,)
        ).fetchone()[0]
        for a in agents
    }

    normal = [a for a in agents if a.code not in _DELIB_AGENTS]
    router = next((a for a in normal if a.role == "router"), None)

    lines: list[str] = ["```mermaid", "flowchart TD"]
    lines.append('  User([Human]) --> DISP["Dispatcher · Tier-0 (alexa | deep)"]')
    lines.append('  DISP -->|alexa| ALEXA["Direct answer"]')
    if router is not None:
        lines.append(f'  DISP -->|deep| {_nid(router.code)}')

    # Node declarations (label = code + role · model).
    for a in normal:
        label = f"{a.code}<br/>{a.role} · {_model_label(models.get(a.code))}"
        lines.append(f'  {_nid(a.code)}["{label}"]')

    # Delegation edges (only between active, non-deliberation agents).
    for a in normal:
        for t in deleg[a.code]:
            if t in by_code and t not in _DELIB_AGENTS:
                lines.append(f"  {_nid(a.code)} --> {_nid(t)}")

    # Deliberation subgraph (engine-invoked on hard code steps).
    present_delib = [c for c in _DELIB_AGENTS if c in by_code]
    if present_delib and router is not None:
        lines.append('  subgraph DELIB ["Deliberation · engine-invoked · code mode"]')
        if "critical-coder" in by_code:
            lines.append('    critical_coder["critical-coder<br/>thesis / antithesis / synthesis / review"]')
        if "sergent-kiss" in by_code:
            lines.append('    sergent_kiss["sergent-kiss<br/>PASS / REWORK gate"]')
        if {"critical-coder", "sergent-kiss"} <= set(by_code):
            lines.append("    critical_coder --> sergent_kiss")
        lines.append("  end")
        lines.append(f"  {_nid(router.code)} -. hard code step .-> DELIB")

    # Styling.
    lines.append("  classDef router fill:#e6f3ff,stroke:#0366d6,stroke-width:2px;")
    lines.append("  classDef finalizer fill:#eaffea,stroke:#2da44e;")
    for a in normal:
        if a.role == "router":
            lines.append(f"  class {_nid(a.code)} router;")
        elif a.role == "finalizer":
            lines.append(f"  class {_nid(a.code)} finalizer;")
    lines.append("```")
    mermaid = "\n".join(lines)

    # Roster table.
    rows = ["| Agent | Role | Model | Tools | Paradigms | Delegates to |",
            "|---|---|---|--:|--:|---|"]
    for a in sorted(agents, key=lambda x: (x.role != "router", x.code)):
        targets = ", ".join(deleg[a.code]) if deleg[a.code] else "—"
        tag = " · engine" if a.code in _DELIB_AGENTS else ""
        rows.append(
            f"| `{a.code}`{tag} | {a.role} | {_model_label(models.get(a.code))} "
            f"| {tool_n[a.code]} | {para_n[a.code]} | {targets} |"
        )
    table = "\n".join(rows)

    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"# Agent synoptic — chaînes logiques\n\n"
        f"> Généré depuis `jeanmichel.db` le {stamp} (commit `{_head_commit()}`). "
        f"Ne pas éditer à la main — régénérer avec `./jm.sh --synoptic`.\n\n"
        f"Rectangles = maillons LLM · losange = dispatch · sous-graphe = délibération "
        f"(invoquée par le moteur, mode code). Les arêtes pleines = `delegate_to` "
        f"(table `agent_delegation_targets`).\n\n"
        f"## Flux de délégation\n\n{mermaid}\n\n## Roster\n\n{table}\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the agent synoptic diagram from the DB.")
    parser.add_argument("--out", default=str(config.REPO_ROOT / "docs" / "agents_synoptic.md"),
                        help="Output markdown path (default: docs/agents_synoptic.md).")
    parser.add_argument("--stdout", action="store_true", help="Print to stdout instead of writing a file.")
    args = parser.parse_args(argv)

    with db.connect() as conn:
        md = render_synoptic(conn)

    if args.stdout:
        sys.stdout.write(md)
        return 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"synoptic written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
