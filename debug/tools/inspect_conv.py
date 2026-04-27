#!/usr/bin/env python3
"""Inspect the artifacts of a conversation in chronological order.

Usage:
    python tools/inspect_conv.py <conv_id_prefix>
    python tools/inspect_conv.py <conv_id_prefix> --agent jean-michel
    python tools/inspect_conv.py <conv_id_prefix> --kind prompt thought
    python tools/inspect_conv.py <conv_id_prefix> --list
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))

from jeanmichel import config  # noqa: E402

ANSI = {
    "reset":   "\033[0m",
    "bold":    "\033[1m",
    "dim":     "\033[2m",
    "cyan":    "\033[36m",
    "yellow":  "\033[33m",
    "green":   "\033[32m",
    "magenta": "\033[35m",
    "red":     "\033[31m",
}

KIND_COLOR = {
    "prompt":        "cyan",
    "thought":       "dim",
    "tool_call":     "yellow",
    "tool_response": "yellow",
    "briefing":      "magenta",
    "ask_human":     "red",
    "human_answer":  "green",
    "response":      "green",
    "summary":       "green",
}


def c(color: str, text: str) -> str:
    return f"{ANSI.get(color, '')}{text}{ANSI['reset']}"


def find_conv_folder(conv_id_prefix: str) -> Path:
    convs_dir = config.CONVERSATIONS_DIR
    matches = [d for d in convs_dir.iterdir()
               if d.is_dir() and conv_id_prefix in d.name]
    if not matches:
        print(f"No conversation found matching '{conv_id_prefix}' in {convs_dir}", file=sys.stderr)
        sys.exit(1)
    if len(matches) > 1:
        print(f"Multiple matches — be more specific:", file=sys.stderr)
        for m in sorted(matches):
            print(f"  {m.name}", file=sys.stderr)
        sys.exit(1)
    return matches[0]


def parse_kind_agent(filename: str) -> tuple[str, str]:
    """Extract (agent, kind) from a filename like 152343847_jean-michel_prompt.md."""
    stem = Path(filename).stem  # e.g. 152343847_jean-michel_prompt
    parts = stem.split("_")
    if len(parts) < 3:
        return "unknown", "unknown"
    # timestamp is first token; agent is everything up to the last token; kind is last token
    kind = parts[-1]
    agent = "_".join(parts[1:-1])
    return agent, kind


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect conversation artifacts.")
    parser.add_argument("conv_id", help="Conversation ID or unique prefix.")
    parser.add_argument("--agent", nargs="+", help="Filter by agent code(s).")
    parser.add_argument("--kind", nargs="+",
                        help="Filter by kind(s): prompt thought tool_call tool_response "
                             "briefing ask_human human_answer response summary")
    parser.add_argument("--list", action="store_true",
                        help="List artifact filenames only, no content.")
    args = parser.parse_args()

    folder = find_conv_folder(args.conv_id)
    print(c("bold", f"\n=== Conversation: {folder.name} ===\n"))

    artifacts = sorted(f for f in folder.iterdir()
                       if f.is_file() and f.suffix == ".md" and f.name != "conversation.md")

    for path in artifacts:
        agent, kind = parse_kind_agent(path.name)

        if args.agent and agent not in args.agent:
            continue
        if args.kind and kind not in args.kind:
            continue

        color = KIND_COLOR.get(kind, "reset")
        header = c(color, f"── {path.name}  [{agent} / {kind}]")
        print(header)

        if args.list:
            continue

        text = path.read_text(encoding="utf-8")
        # Skip YAML frontmatter for readability
        if text.startswith("---"):
            end = text.find("\n---\n", 4)
            if end != -1:
                text = text[end + 5:].lstrip("\n")

        # Dim the content slightly vs the header
        print(c("dim", text))
        print()

    if args.list:
        return

    # Show conversation journal last
    journal = folder / "conversation.md"
    if journal.exists():
        print(c("bold", "── conversation.md"))
        print(c("dim", journal.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
