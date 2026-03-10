#!/usr/bin/env python3
"""Populate the collector SQLite DB with mock records for manual testing.

Usage: . .venv/bin/activate && python UnityIntegration/python/populate_test_data.py
"""
import time
import struct
import sqlite3
import os
import sys

# ensure repo root is on sys.path so we can import UnityIntegration.python.collector_tail
_SCRIPT_DIR = os.path.dirname(__file__)
_REPO_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from UnityIntegration.python import collector_tail as ct


def make_headpose_rec(seq, unity_t=0.0, px=0.1, py=0.2, pz=0.3, qx=0.0, qy=0.0, qz=0.0, qw=1.0):
    return struct.pack('<I8f', seq, unity_t, px, py, pz, qx, qy, qz, qw)


def make_bike_rec(seq, unity_t=0.0, speed=5.5, steering=0.1, bf=0, br=0):
    return struct.pack('<IfffBB', seq, unity_t, speed, steering, bf, br)


def make_hr_rec(seq, unity_t=0.0, hr_bpm=72.0):
    return struct.pack('<Iff', seq, unity_t, hr_bpm)


def main():
    out_db = os.path.join('collector_out', 'vrs.sqlite')
    os.makedirs(os.path.dirname(out_db), exist_ok=True)
    conn = ct.init_db(out_db)

    sid = int(time.time()) % 1000000
    recv_ns = int(time.time() * 1e9)

    head_recs = [make_headpose_rec(i, unity_t=0.01 * i, px=0.1 * i) for i in range(1, 11)]
    bike_recs = [make_bike_rec(i, unity_t=0.02 * i, speed=3.0 + i) for i in range(1, 6)]
    hr_recs = [make_hr_rec(i, unity_t=0.05 * i, hr_bpm=60.0 + i) for i in range(1, 4)]
    evs = [(i, 0.1 * i, f'{{"evt": "test", "i": {i}}}') for i in range(1, 5)]

    n_head = ct.insert_records_batch(conn, 1, sid, recv_ns, head_recs)
    n_bike = ct.insert_records_batch(conn, 2, sid, recv_ns, bike_recs)
    n_hr = ct.insert_records_batch(conn, 3, sid, recv_ns, hr_recs)
    n_ev = ct.insert_events_batch(conn, sid, recv_ns, evs)

    conn.commit()

    print(f'Inserted headpose={n_head} bike={n_bike} hr={n_hr} events={n_ev} into {out_db} (session_id={sid})')

    # quick verification
    cur = conn.cursor()
    for tbl in ('headpose', 'bike', 'hr', 'events'):
        cur.execute(f'SELECT COUNT(*) FROM {tbl} WHERE session_id=?', (sid,))
        print(tbl, cur.fetchone()[0])


if __name__ == '__main__':
    main()
