#!/usr/bin/env python3
"""Delete conversations older than N days.

Removes both the on-disk folder and the DB record (cascade deletes
requests and artifacts).

Usage:
    python debug/clean_convs.py [--days N] [--yes]

Options:
    --days N    Delete conversations older than N days (default: 7)
    --yes       Skip confirmation prompt
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))

from jeanmichel import db  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete conversations older than N days."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Delete conversations older than this many days (default: 7)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt",
    )
    args = parser.parse_args()

    cutoff = datetime.now(UTC) - timedelta(days=args.days)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT id, title, folder_path, created_at
            FROM conversations
            WHERE created_at < ?
            ORDER BY created_at ASC
            """,
            (cutoff_str,),
        ).fetchall()

    if not rows:
        print(f"No conversations older than {args.days} day(s). Nothing to do.")
        return

    print(f"Conversations older than {args.days} day(s) (cutoff: {cutoff_str}):\n")
    for row in rows:
        title = row[1] or "(no title)"
        print(f"  [{row[3]}]  {row[0][:8]}…  {title}")
        print(f"             {row[2]}")

    print(f"\n{len(rows)} conversation(s) will be deleted (DB records + folders on disk).")

    if not args.yes:
        try:
            answer = input("Confirm? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(0)
        if answer not in ("y", "yes"):
            print("Aborted.")
            sys.exit(0)

    deleted = 0
    errors = 0
    with db.connect() as conn:
        for row in rows:
            conv_id, _, folder_path, _ = row
            # Delete folder on disk
            folder = Path(folder_path)
            if folder.exists():
                try:
                    shutil.rmtree(folder)
                except OSError as e:
                    print(f"  Warning: could not delete {folder}: {e}", file=sys.stderr)
                    errors += 1
            # Delete DB record (CASCADE removes requests + artifacts)
            conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
            deleted += 1

    # Collect remaining active conversation IDs for orphan container cleanup.
    with db.connect() as conn:
        active_rows = conn.execute("SELECT id FROM conversations").fetchall()
    active_ids = {r[0] for r in active_rows}
    _cleanup_orphan_containers(active_ids)

    print(f"\nDeleted {deleted} conversation(s)." + (f" {errors} error(s)." if errors else ""))


def _cleanup_orphan_containers(active_conv_ids: set[str]) -> None:
    """Remove sandbox containers whose conversation no longer exists in DB."""
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=jm-sandbox-", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return  # Docker not available or timed out — skip silently.

    removed = 0
    for line in result.stdout.splitlines():
        name = line.strip()
        if not name.startswith("jm-sandbox-"):
            continue
        conv_id = name[len("jm-sandbox-"):]
        if conv_id not in active_conv_ids:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            removed += 1

    if removed:
        print(f"Removed {removed} orphan sandbox container(s).")


if __name__ == "__main__":
    main()
