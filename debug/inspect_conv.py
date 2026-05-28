#!/usr/bin/env python3
"""Inspect a v2 conversation by reading its filesystem artefacts.

v2 layout (cf. §6 doc 06 + §6 bis doc 06) :

    conversations/<id>/
    ├── messages.json                  — main agent's full messages[]
    ├── state.json                     — scalar counters snapshot
    ├── events.jsonl                   — append-only typed event log
    └── subagent_<request_id>.json     — per subagent execution

Usage :
    ./jm.sh --inspect-conv <id_prefix>
    ./jm.sh --inspect-conv <id_prefix> --events            (timeline only)
    ./jm.sh --inspect-conv <id_prefix> --messages          (main messages only)
    ./jm.sh --inspect-conv <id_prefix> --subagents         (list subagent files)
    ./jm.sh --inspect-conv <id_prefix> --subagent <req_id> (one subagent)

Falls back to a "no v2 artefacts found" message for pre-v2 conversations.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))

from jeanmichel import config  # noqa: E402
from jeanmichel import db  # noqa: E402


# ---- ANSI helpers --------------------------------------------------------

ANSI = {
    "reset":   "\033[0m",
    "bold":    "\033[1m",
    "dim":     "\033[2m",
    "cyan":    "\033[36m",
    "yellow":  "\033[33m",
    "green":   "\033[32m",
    "magenta": "\033[35m",
    "red":     "\033[31m",
    "blue":    "\033[34m",
}


def c(color: str, text: str) -> str:
    return f"{ANSI.get(color, '')}{text}{ANSI['reset']}"


def _truncate(s: str, n: int) -> str:
    s = (s or "").replace("\n", " ⏎ ")
    return s if len(s) <= n else s[: n - 1] + "…"


# ---- Conversation lookup -------------------------------------------------


def _resolve_conv_folder(prefix: str) -> tuple[str | None, Path | None]:
    """Find a conversation by id prefix. Returns (id, folder) or (None, None)."""
    try:
        with db.connect() as conn:
            row = db.get_conversation(conn, prefix)
            if row is not None:
                return row["id"], Path(row["folder_path"])
    except Exception:
        pass

    # Fallback : scan the conversations directory by prefix.
    convs_dir = config.CONVERSATIONS_DIR
    if not convs_dir.exists():
        return None, None
    matches = []
    for d in convs_dir.iterdir():
        if not d.is_dir():
            continue
        # Folder name pattern : YYYY-MM-DD_HH-MM_<conv_id>
        parts = d.name.split("_", 2)
        if len(parts) >= 3 and parts[2].startswith(prefix):
            matches.append((parts[2], d))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(c("red", f"Ambiguous prefix '{prefix}' — {len(matches)} matches:"))
        for cid, d in matches:
            print(f"  {cid}  →  {d}")
        return None, None
    return None, None


# ---- Render messages.json ------------------------------------------------


def _render_role(role: str, content: str, extra: str = "") -> None:
    color_by_role = {
        "system":    "blue",
        "user":      "cyan",
        "assistant": "green",
        "tool":      "yellow",
    }
    color = color_by_role.get(role, "dim")
    label = role.upper()
    if extra:
        label = f"{label} ({extra})"
    print(c(color, f"  ── {label} ──"))
    # Indent each line.
    for line in (content or "").rstrip().splitlines():
        print(f"    {line}")
    print()


def _print_messages(messages: list[dict], title: str) -> None:
    print(c("bold", f"\n=== {title} ({len(messages)} messages) ===\n"))
    if not messages:
        print(c("dim", "  (empty)\n"))
        return
    for idx, msg in enumerate(messages):
        role = msg.get("role", "?")
        if role == "tool":
            tool_name = msg.get("tool_name", "?")
            content = msg.get("content", "")
            _render_role(role, content, extra=f"#{idx} {tool_name}")
        elif role == "assistant":
            content = msg.get("content") or ""
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                names = ", ".join((tc.get("function") or {}).get("name", "?") for tc in tool_calls)
                _render_role(role, content, extra=f"#{idx} calls: {names}")
            else:
                _render_role(role, content, extra=f"#{idx}")
            thinking = msg.get("thinking", "")
            if thinking:
                print(c("dim", "    (thinking)"))
                for line in thinking.rstrip().splitlines():
                    print(c("dim", f"      {line}"))
                print()
        else:
            _render_role(role, msg.get("content", ""), extra=f"#{idx}")


# ---- Render events.jsonl -------------------------------------------------


def _print_events(events: list[dict], filter_types: set[str] | None = None) -> None:
    print(c("bold", f"\n=== events.jsonl ({len(events)} events) ===\n"))
    if not events:
        print(c("dim", "  (no events)\n"))
        return
    for ev in events:
        ev_type = ev.get("type", "?")
        if filter_types and ev_type not in filter_types:
            continue
        utc = ev.get("utc", "")[:23]  # trim microseconds tail
        # Specific renderers per event type.
        if ev_type in ("RequestStarted", "RequestCompleted"):
            color = "blue"
        elif ev_type == "DelegationStarted":
            color = "magenta"
        elif ev_type == "DelegationCompleted":
            color = "magenta"
        elif ev_type in ("ToolCallStarted", "ToolCallCompleted"):
            color = "yellow"
        elif ev_type in ("LLMCallStarted", "LLMCallCompleted"):
            color = "dim"
        elif ev_type == "HookFired":
            color = "red"
        elif ev_type == "WorkingBudgetUpdate":
            color = "red"
        elif ev_type == "MemoryNearCapacity":
            color = "red"
        else:
            color = "dim"

        # Compact one-line summary per event.
        payload = {k: v for k, v in ev.items() if k not in ("type", "utc")}
        summary_parts = [f"{k}={_truncate(str(v), 60)}" for k, v in payload.items()]
        summary = " · ".join(summary_parts)
        print(c(color, f"  [{utc}] {ev_type}"))
        if summary:
            print(c("dim", f"    {summary}"))


# ---- Render state.json ---------------------------------------------------


def _print_state(state: dict) -> None:
    print(c("bold", "\n=== state.json ===\n"))
    if not state:
        print(c("dim", "  (no state)\n"))
        return
    for k, v in state.items():
        print(f"  {c('cyan', k):24} {v}")


# ---- Subagent listing ----------------------------------------------------


def _list_subagents(conv_folder: Path) -> list[Path]:
    return sorted(conv_folder.glob("subagent_*.json"))


# ---- Main ----------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a v2 conversation.")
    parser.add_argument("conv_id", help="Conversation id or unique prefix.")
    parser.add_argument(
        "--events",
        action="store_true",
        help="Print events.jsonl timeline only.",
    )
    parser.add_argument(
        "--messages",
        action="store_true",
        help="Print messages.json only.",
    )
    parser.add_argument(
        "--state",
        action="store_true",
        help="Print state.json only.",
    )
    parser.add_argument(
        "--subagents",
        action="store_true",
        help="List subagent_*.json files.",
    )
    parser.add_argument(
        "--subagent",
        metavar="REQUEST_ID",
        help="Print the messages of a specific subagent.",
    )
    parser.add_argument(
        "--filter-event",
        action="append",
        default=[],
        help="Filter events.jsonl to one type (repeatable).",
    )
    args = parser.parse_args()

    conv_id, conv_folder = _resolve_conv_folder(args.conv_id)
    if conv_folder is None or not conv_folder.exists():
        print(c("red", f"Conversation '{args.conv_id}' not found."))
        return 1

    print(c("bold", f"Conversation: {conv_id}"))
    print(c("dim", f"Folder      : {conv_folder}\n"))

    # Discover available v2 artefacts.
    messages_path = conv_folder / "messages.json"
    events_path = conv_folder / "events.jsonl"
    state_path = conv_folder / "state.json"
    subagent_files = _list_subagents(conv_folder)

    has_v2_artefacts = (
        messages_path.exists() or events_path.exists() or state_path.exists()
        or bool(subagent_files)
    )
    if not has_v2_artefacts:
        print(c("yellow",
            "No v2 artefacts found in this conversation folder.\n"
            "  Expected: messages.json, events.jsonl, state.json, subagent_*.json\n"
            "  This may be a legacy (pre-v2) conversation. Use the legacy "
            "artefact files (HHMMSS_*.md) directly."))
        return 0

    # --subagent <request_id> — focused mode.
    if args.subagent:
        target = conv_folder / f"subagent_{args.subagent}.json"
        if not target.exists():
            print(c("red", f"subagent file not found: {target.name}"))
            return 1
        sub_msgs = json.loads(target.read_text(encoding="utf-8"))
        _print_messages(sub_msgs, f"subagent {args.subagent}")
        return 0

    # --subagents — listing mode.
    if args.subagents:
        print(c("bold", f"=== Subagents ({len(subagent_files)}) ===\n"))
        for f in subagent_files:
            try:
                msgs = json.loads(f.read_text(encoding="utf-8"))
                role_system = msgs[0].get("content", "") if msgs else ""
                # First line of system prompt usually says "You are <agent>".
                first_line = role_system.splitlines()[0] if role_system else ""
                print(f"  {c('cyan', f.name)}  ({len(msgs)} msgs)  {c('dim', _truncate(first_line, 80))}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {c('red', f.name)}  unreadable: {exc}")
        return 0

    # Default : print whatever the user asked for. If nothing specific, print all.
    show_messages = args.messages or not (args.events or args.state)
    show_events = args.events or not (args.messages or args.state)
    show_state = args.state or not (args.events or args.messages)

    if show_messages and messages_path.exists():
        msgs = json.loads(messages_path.read_text(encoding="utf-8"))
        _print_messages(msgs, "messages.json (main agent)")

    if show_events and events_path.exists():
        events = []
        for line in events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        filter_set = set(args.filter_event) if args.filter_event else None
        _print_events(events, filter_set)

    if show_state and state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        _print_state(state)

    # Always show subagent count.
    if subagent_files:
        print(c("dim", f"\n({len(subagent_files)} subagent file(s) — use --subagents to list)"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
