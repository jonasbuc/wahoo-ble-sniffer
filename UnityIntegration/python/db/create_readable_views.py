#!/usr/bin/env python3
"""Create readable SQLite VIEWs for the collector DB.

This adds views like `headpose_readable` and `bike_readable` that expose
human-friendly time columns (recv_ts_ms and recv_ts_iso) without modifying
the underlying raw tables.

Run in repo venv:
  . .venv/bin/activate
  python UnityIntegration/python/db/create_readable_views.py [--db PATH]
"""
from __future__ import annotations
import argparse
import sqlite3
from pathlib import Path

# Default: db/ → python/ → UnityIntegration/ → repo_root/collector_out/vrs.sqlite
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_DB = _REPO_ROOT / "collector_out" / "vrs.sqlite"


VIEWS = [
    (
        "headpose_readable",
        """
        CREATE VIEW IF NOT EXISTS headpose_readable AS
        SELECT
          session_id,
          recv_ts_ns,
          -- nanoseconds → milliseconds (÷ 1 000 000) for spreadsheet-friendly timestamps
          (recv_ts_ns/1000000) AS recv_ts_ms,
          -- nanoseconds → seconds → SQLite datetime → append "Z" for explicit UTC
          datetime(recv_ts_ns/1000000000.0, 'unixepoch') || 'Z' AS recv_ts_iso,
          seq, unity_t, px, py, pz, qx, qy, qz, qw
        FROM headpose;
        """,
    ),
    (
        "bike_readable",
        """
        CREATE VIEW IF NOT EXISTS bike_readable AS
        SELECT
          session_id,
          recv_ts_ns,
          (recv_ts_ns/1000000) AS recv_ts_ms,
          datetime(recv_ts_ns/1000000000.0, 'unixepoch') || 'Z' AS recv_ts_iso,
          seq, unity_t, speed, steering, brake_front, brake_rear
        FROM bike;
        """,
    ),
    (
        "hr_readable",
        """
        CREATE VIEW IF NOT EXISTS hr_readable AS
        SELECT
          session_id,
          recv_ts_ns,
          (recv_ts_ns/1000000) AS recv_ts_ms,
          datetime(recv_ts_ns/1000000000.0, 'unixepoch') || 'Z' AS recv_ts_iso,
          seq, unity_t, hr_bpm
        FROM hr;
        """,
    ),
    (
        "events_readable",
        """
        CREATE VIEW IF NOT EXISTS events_readable AS
        SELECT
          session_id,
          recv_ts_ns,
          (recv_ts_ns/1000000) AS recv_ts_ms,
          datetime(recv_ts_ns/1000000000.0, 'unixepoch') || 'Z' AS recv_ts_iso,
          seq, unity_t, json,
          -- Convenience columns extracted from the JSON payload so you can
          -- filter/group by event name without parsing JSON in every query.
          json_extract(json, '$.evt') AS evt_name,
          json_extract(json, '$.i')   AS evt_i
        FROM events;
        """,
    ),
    (
        "sessions_readable",
        """
        CREATE VIEW IF NOT EXISTS sessions_readable AS
        SELECT
          session_id,
          started_unix_ms,
          -- milliseconds → seconds → SQLite datetime (÷ 1000)
          datetime(started_unix_ms/1000.0, 'unixepoch') || 'Z' AS started_unix_iso,
          session_dir
        FROM sessions;
        """,
    ),
]


def create_views(db_path: str | Path) -> None:
    """Create all readable VIEWs in *db_path* (callable without argparse)."""
    db = Path(db_path)
    if not db.exists():
        print(f"DB not found: {db}")
        return
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    for name, sql in VIEWS:
        print(f"Creating view: {name}")
        cur.executescript(sql)
    conn.commit()
    conn.close()
    print("All views created (if not already present).")


def main() -> None:
    p = argparse.ArgumentParser(description="Create readable SQLite VIEWs for the collector DB.")
    p.add_argument("--db", default=str(_DEFAULT_DB), help="Path to the collector SQLite database")
    args = p.parse_args()
    create_views(args.db)


if __name__ == "__main__":
    main()
