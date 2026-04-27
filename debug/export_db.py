#!/usr/bin/env python3
"""Export the Jean-Michel SQLite database to human-readable JSON.

Usage:
    python debug/export_db.py [--db PATH] [--out PATH]

Default output is stdout. Use --out to write to a file.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def _rows_to_dicts(conn: sqlite3.Connection, query: str, params: tuple = ()) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def export_db(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    data: dict = {}

    # Static config tables
    data["agents"] = _rows_to_dicts(conn, "SELECT * FROM agents ORDER BY id")
    data["sections"] = _rows_to_dicts(conn, "SELECT * FROM sections ORDER BY order_priority, id")
    data["categories"] = _rows_to_dicts(conn, "SELECT * FROM categories ORDER BY section_id, order_priority, id")
    data["paradigms"] = _rows_to_dicts(conn, "SELECT * FROM paradigms ORDER BY category_id, order_priority, id")
    data["agent_paradigms"] = _rows_to_dicts(conn, "SELECT * FROM agent_paradigms ORDER BY agent_id, paradigm_id")
    data["agent_tools"] = _rows_to_dicts(conn, "SELECT * FROM agent_tools ORDER BY agent_id, tool_code")

    # Runtime tables
    data["conversations"] = _rows_to_dicts(conn, "SELECT * FROM conversations ORDER BY created_at DESC")
    data["requests"] = _rows_to_dicts(conn, "SELECT * FROM requests ORDER BY created_at DESC")
    data["messages"] = _rows_to_dicts(conn, "SELECT * FROM messages ORDER BY created_at DESC")

    conn.close()
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Jean-Michel DB to JSON.")
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

    data = export_db(db_path)
    output = json.dumps(data, indent=2, ensure_ascii=False, default=str)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Exported to {args.out}")
    else:
        print(output)


if __name__ == "__main__":
    main()
