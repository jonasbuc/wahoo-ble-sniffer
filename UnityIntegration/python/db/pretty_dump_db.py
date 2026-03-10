#!/usr/bin/env python3
"""Pretty-print contents of the collector sqlite DB with readable timestamps.

Usage: run inside the repo venv:
  . .venv/bin/activate
  python UnityIntegration/python/db/pretty_dump_db.py

This script does not modify the DB. It converts columns named
`recv_ts_ns` to milliseconds and ISO8601 strings for easier inspection,
and converts `started_unix_ms` to ISO as well.
"""
from __future__ import annotations
import sqlite3
import datetime
from typing import Any

DB = "collector_out/vrs.sqlite"


def ns_to_iso(ns: int) -> str:
    try:
        ms = ns // 1_000_000
        ts = datetime.datetime.utcfromtimestamp(ms / 1000.0)
        return ts.isoformat() + "Z"
    except Exception:
        return "-"


def ms_to_iso(ms: int) -> str:
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
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")]
    if not tables:
        print("No tables found in:", DB)
        return
    print("Tables:", ", ".join(tables))
    print()
    for t in tables:
        dump_table(cur, t)


if __name__ == "__main__":
    main()
