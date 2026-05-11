"""
Populate the analytics DB with realistic demo sessions so the dashboard
can be exercised without a live Unity connection.

Run from the repo root:
    python -m live_analytics.populate_demo_data

Three sessions are inserted:
  1. A completed 10-min ride with normal HR/speed
  2. A short 3-min sprint with high HR and braking events
  3. An in-progress session (no end_unix_ms) simulating an active ride
"""

from __future__ import annotations

import json
import math
import random
import time
from pathlib import Path

# Ensure the DB is initialised with the correct schema before we write.
from live_analytics.app.storage.sqlite_store import (
    end_session,
    increment_record_count,
    init_db,
    upsert_session,
)
from live_analytics.app.config import DB_PATH, SESSIONS_DIR

SEED = 42
random.seed(SEED)

DB = DB_PATH


def _now_ms() -> int:
    return int(time.time() * 1000)


def _insert_events(db_path: Path, session_id: str, events: list[dict]) -> None:
    """Bulk-insert event rows directly (no public helper exists yet)."""
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executemany(
        "INSERT INTO events (session_id, unix_ms, event_type, payload) VALUES (?,?,?,?)",
        [
            (session_id, e["unix_ms"], e["event_type"], json.dumps(e.get("payload", {})))
            for e in events
        ],
    )
    conn.commit()
    conn.close()


def _write_jsonl(sessions_dir: Path, session_id: str, records: list[dict]) -> None:
    """Write telemetry records as JSONL for the dashboard charts."""
    out_dir = sessions_dir / session_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "telemetry.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r["payload"]) + "\n")


def _make_records(
    session_id: str,
    start_ms: int,
    duration_sec: int,
    hz: int = 20,
    hr_base: float = 75.0,
    speed_base: float = 5.0,
    scenario_id: str = "demo",
) -> list[dict]:
    """Generate synthetic telemetry events for one session."""
    records = []
    n = duration_sec * hz
    for i in range(n):
        t_ms = start_ms + int(i * 1000 / hz)
        t_sec = i / hz
        speed = max(0.0, speed_base + 2.0 * math.sin(t_sec / 30) + random.gauss(0, 0.3))
        hr = hr_base + 20 * (1 - math.exp(-t_sec / 60)) + random.gauss(0, 1.5)
        steering = 5.0 * math.sin(t_sec / 8) + random.gauss(0, 0.5)
        brake_front = random.choices([0, random.randint(50, 200)], weights=[0.97, 0.03])[0]
        records.append(
            {
                "unix_ms": t_ms,
                "event_type": "telemetry",
                "payload": {
                    "unity_time": t_sec,
                    "scenario_id": scenario_id,
                    "speed": round(speed, 3),
                    "steering_angle": round(steering, 3),
                    "brake_front": int(brake_front),
                    "brake_rear": 0,
                    "heart_rate": round(hr, 1),
                    "head_pos_x": round(random.gauss(0, 0.02), 4),
                    "head_pos_y": round(1.7 + random.gauss(0, 0.01), 4),
                    "head_pos_z": round(random.gauss(0, 0.02), 4),
                    "record_type": "gameplay",
                },
            }
        )
    return records


def populate() -> None:
    init_db(DB)

    base = _now_ms()

    sessions = [
        {
            "session_id": "demo_session_001",
            "start_offset_ms": -35 * 60 * 1000,   # started 35 min ago
            "duration_sec": 600,                   # 10-min ride
            "scenario_id": "city_loop",
            "hr_base": 78.0,
            "speed_base": 5.5,
            "ended": True,
        },
        {
            "session_id": "demo_session_002",
            "start_offset_ms": -12 * 60 * 1000,   # started 12 min ago
            "duration_sec": 180,                   # 3-min sprint
            "scenario_id": "sprint_track",
            "hr_base": 120.0,
            "speed_base": 9.0,
            "ended": True,
        },
        {
            "session_id": "demo_session_003",
            "start_offset_ms": -2 * 60 * 1000,    # started 2 min ago
            "duration_sec": 120,                   # in-progress (2 min so far)
            "scenario_id": "open_road",
            "hr_base": 90.0,
            "speed_base": 6.0,
            "ended": False,
        },
    ]

    for s in sessions:
        start_ms = base + s["start_offset_ms"]
        upsert_session(DB, s["session_id"], start_ms, s["scenario_id"])

        records = _make_records(
            session_id=s["session_id"],
            start_ms=start_ms,
            duration_sec=s["duration_sec"],
            hr_base=s["hr_base"],
            speed_base=s["speed_base"],
            scenario_id=s["scenario_id"],
        )

        _insert_events(DB, s["session_id"], records)
        increment_record_count(DB, s["session_id"], len(records))
        _write_jsonl(SESSIONS_DIR, s["session_id"], records)

        if s["ended"]:
            end_ms = start_ms + s["duration_sec"] * 1000
            end_session(DB, s["session_id"], end_ms)

        status = "ended" if s["ended"] else "in-progress"
        print(f"  {s['session_id']}  {len(records):,} records  [{status}]")

    print(f"\nDB: {DB}")
    print("Done – refresh the dashboard to see the sessions.")


if __name__ == "__main__":
    populate()
