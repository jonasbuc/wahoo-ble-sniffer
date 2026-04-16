#!/usr/bin/env python3
"""Validate contents of the collector SQLite DB for correct formatting and sensible values.

Checks performed:
- headpose: px/py/pz are finite floats, quaternion norm ~1
- bike: speed non-negative, steering finite, brake flags are 0/1
- hr: hr_bpm in plausible range (30..220)
- events: json column parses

Usage: . .venv/bin/activate && python UnityIntegration/python/db/validate_db.py [--db PATH]
"""
import argparse
import json
import math
import os
import sqlite3
from pathlib import Path

# Default: db/sqlite/ -> db/ -> bridge/ -> repo_root/collector_out/vrs.sqlite
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DB = str(_REPO_ROOT / "collector_out" / "vrs.sqlite")

def float_ok(x):
    """Return True if *x* can be cast to a finite float (not NaN, not ±Inf)."""
    try:
        v = float(x)
    except Exception:
        return False
    return not (math.isinf(v) or math.isnan(v))


def validate_headpose(conn):
    """Check every headpose row for finite floats and a valid quaternion norm.

    A unit quaternion representing a rotation must satisfy:
      ||q|| = sqrt(qx² + qy² + qz² + qw²) ≈ 1.0

    We allow ±0.1 tolerance to account for floating-point rounding in the
    Unity writer and struct pack/unpack round-trips.  Values outside that
    range indicate corrupted or un-normalised rotation data.
    """
    cur = conn.cursor()
    cur.execute('SELECT session_id, seq, px, py, pz, qx, qy, qz, qw FROM headpose')
    problems = []
    count = 0
    for row in cur.fetchall():
        count += 1
        sid, seq, px, py, pz, qx, qy, qz, qw = row
        if not all(float_ok(v) for v in (px, py, pz, qx, qy, qz, qw)):
            problems.append(f'headpose non-float in session {sid} seq {seq}')
            continue
        # Quaternion norm: sqrt(qx² + qy² + qz² + qw²) should be ≈ 1.0
        norm = math.sqrt(qx*qx + qy*qy + qz*qz + qw*qw)
        if abs(norm - 1.0) > 0.1:
            problems.append(f'headpose quaternion norm off ({norm:.3f}) session {sid} seq {seq}')
    return count, problems


def validate_bike(conn):
    cur = conn.cursor()
    cur.execute('SELECT session_id, seq, speed, steering, brake_front, brake_rear FROM bike')
    problems = []
    count = 0
    for row in cur.fetchall():
        count += 1
        sid, seq, speed, steering, bf, br = row
        if not float_ok(speed) or speed < -0.1:
            problems.append(f'bike invalid speed {speed} session {sid} seq {seq}')
        if not float_ok(steering):
            problems.append(f'bike invalid steering {steering} session {sid} seq {seq}')
        if bf not in (0, 1) or br not in (0, 1):
            problems.append(f'bike brake flags not 0/1 bf={bf} br={br} session {sid} seq {seq}')
    return count, problems


def validate_hr(conn):
    cur = conn.cursor()
    cur.execute('SELECT session_id, seq, hr_bpm FROM hr')
    problems = []
    count = 0
    for row in cur.fetchall():
        count += 1
        sid, seq, hr = row
        if not float_ok(hr):
            problems.append(f'hr non-float {hr} session {sid} seq {seq}')
            continue
        if not (30.0 <= hr <= 220.0):
            problems.append(f'hr out of range {hr} session {sid} seq {seq}')
    return count, problems


def validate_events(conn):
    cur = conn.cursor()
    cur.execute('SELECT session_id, seq, json FROM events')
    problems = []
    count = 0
    for row in cur.fetchall():
        count += 1
        sid, seq, js = row
        try:
            _ = json.loads(js)
        except Exception as e:
            problems.append(f'events json parse error session {sid} seq {seq}: {e}')
    return count, problems


def main():
    p = argparse.ArgumentParser(description="Validate collector SQLite DB for correctness.")
    p.add_argument("--db", default=_DEFAULT_DB, help="Path to the collector SQLite database")
    args = p.parse_args()
    db_path = args.db
    if not os.path.exists(db_path):
        print('DB not found:', db_path)
        return 2
    conn = sqlite3.connect(db_path)
    results = []

    c, p = validate_headpose(conn)
    results.append(('headpose', c, p))
    c, p = validate_bike(conn)
    results.append(('bike', c, p))
    c, p = validate_hr(conn)
    results.append(('hr', c, p))
    c, p = validate_events(conn)
    results.append(('events', c, p))

    all_ok = True
    for tbl, cnt, probs in results:
        print(f'{tbl}: {cnt} rows')
        if probs:
            all_ok = False
            print('  Problems:')
            for x in probs[:10]:
                print('   -', x)
            if len(probs) > 10:
                print(f'   ... and {len(probs)-10} more')
    if all_ok:
        print('\nValidation passed: all checks OK')
        return 0
    else:
        print('\nValidation failed: see problems above')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
