"""Stop lingering jm-sandbox-* Docker containers (CLI wrapper for reap_sandboxes).

Sandbox containers are started detached (`docker run -d --rm`) and persist across
turns; nothing stops them, so they accumulate (one per conversation). Stopping is
SAFE — an active conversation respawns its container on the next bash_sandbox call.

    ./jm.sh --reap-sandboxes                 # stop all jm-sandbox-* containers
    ./jm.sh --reap-sandboxes --idle-minutes 30   # only those running > 30 min
"""

from __future__ import annotations

import argparse

from jeanmichel.tools.bash_sandbox import reap_sandboxes


def main() -> None:
    parser = argparse.ArgumentParser(description="Stop lingering jm-sandbox-* containers.")
    parser.add_argument(
        "--idle-minutes",
        type=int,
        default=None,
        help="Only stop containers running longer than N minutes (default: all).",
    )
    args = parser.parse_args()
    stopped = reap_sandboxes(max_idle_minutes=args.idle_minutes)
    if stopped:
        print(f"stopped {len(stopped)} sandbox container(s):")
        for name in stopped:
            print(f"  - {name}")
    else:
        print("no sandbox containers to reap.")


if __name__ == "__main__":
    main()
