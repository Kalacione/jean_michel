#!/usr/bin/env python3
"""Dump the Jean-Michel SQLite database to a restorable SQL file.

Usage:
    python debug/export_db.py [--db PATH] [--out PATH]

Default output is stdout. Use --out to write to a file.
Restore with: sqlite3 jeanmichel.db < backup.sql
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def dump_db(db_path: Path) -> str:
    conn = sqlite3.connect(db_path)
    lines = list(conn.iterdump())
    conn.close()
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Dump Jean-Michel DB to SQL.")
    parser.add_argument(
        "--db",
        default="jeanmichel.db",
        help="Path to the SQLite database file (default: jeanmichel.db)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output file path. Defaults to stdout.",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Error: database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    output = dump_db(db_path)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Exported to {args.out}")
    else:
        sys.stdout.write(output)


if __name__ == "__main__":
    main()
