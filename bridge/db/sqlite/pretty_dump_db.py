#!/usr/bin/env python3
"""Pretty-print contents of the collector sqlite DB with readable timestamps.

Usage: run inside the repo venv:
  . .venv/bin/activate
  python bridge/db/sqlite/pretty_dump_db.py [--db PATH]

This script does not modify the DB. It converts columns named
`recv_ts_ns` to milliseconds and ISO8601 strings for easier inspection,
and converts `started_unix_ms` to ISO as well.
"""
from __future__ import annotations
import argparse
import sqlite3
import datetime
from pathlib import Path
from typing import Any

# Default: db/sqlite/ -> db/ -> bridge/ -> repo_root/collector_out/vrs.sqlite
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DB = str(_REPO_ROOT / "collector_out" / "vrs.sqlite")


def ns_to_iso(ns: int) -> str:
    """Convert a nanosecond Unix timestamp to an ISO 8601 UTC string.

    Steps: ns ÷ 1_000_000 → milliseconds
           ms ÷ 1000      → seconds (float)  → datetime.utcfromtimestamp
           append "Z"     → explicit UTC marker
    """
    try:
        ms = ns // 1_000_000          # nanoseconds → milliseconds (integer division)
        ts = datetime.datetime.utcfromtimestamp(ms / 1000.0)  # ms → seconds
        return ts.isoformat() + "Z"
    except Exception:
        return "-"


def ms_to_iso(ms: int) -> str:
    """Convert a millisecond Unix timestamp to an ISO 8601 UTC string.

    Steps: ms ÷ 1000 → seconds (float) → datetime.utcfromtimestamp
           append "Z" → explicit UTC marker
    """
    try:
        ts = datetime.datetime.utcfromtimestamp(ms / 1000.0)
        return ts.isoformat() + "Z"
    except Exception:
        return "-"


def pretty_value(col: str, val: Any) -> str:
    if val is None:
        return "NULL"
    if col == "recv_ts_ns":
        try:
            ival = int(val)
            return f"{ival} (ms={ival//1_000_000}, iso={ns_to_iso(ival)})"
        except Exception:
            return str(val)
    if col == "started_unix_ms":
        try:
            ival = int(val)
            return f"{ival} (iso={ms_to_iso(ival)})"
        except Exception:
            return str(val)
    # default formatting for floats/ints/strings
    return str(val)


def dump_table(cur: sqlite3.Cursor, table: str, limit: int = 50) -> None:
    print(f"--- Schema for table: {table} ---")
    cols = [r[1] for r in cur.execute(f"PRAGMA table_info('{table}')")]
    print("Columns:", ", ".join(cols))
    print()
    print(f"--- First rows from {table} (up to {limit}) ---")
    q = f"SELECT * FROM \"{table}\" LIMIT {limit};"
    rows = list(cur.execute(q))
    if not rows:
        print("(no rows)")
        print()
        return
    # print header
    print(" | ".join(cols))
    print("-" * 80)
    for r in rows:
        pretty = [pretty_value(c, v) for c, v in zip(cols, r)]
        print(" | ".join(pretty))
    print()


def main() -> None:
    p = argparse.ArgumentParser(description="Pretty-print collector SQLite DB with readable timestamps.")
    p.add_argument("--db", default=_DEFAULT_DB, help="Path to the collector SQLite database")
    p.add_argument("--limit", type=int, default=50, help="Max rows per table (default: 50)")
    args = p.parse_args()
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")]
    if not tables:
        print("No tables found in:", args.db)
        return
    print("Tables:", ", ".join(tables))
    print()
    for t in tables:
        dump_table(cur, t, limit=args.limit)


if __name__ == "__main__":
    main()
