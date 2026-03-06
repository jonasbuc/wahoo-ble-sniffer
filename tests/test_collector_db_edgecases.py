import struct
import time
import sqlite3
import pytest
from UnityIntegration.python import collector_tail as ct


def make_headpose_rec(seq, unity_t=1.0, px=0.1, py=0.2, pz=0.3, qx=0.0, qy=0.0, qz=0.0, qw=1.0):
    return struct.pack('<I8f', seq, unity_t, px, py, pz, qx, qy, qz, qw)


def test_empty_batch_returns_zero(tmp_path):
    db = tmp_path / 'empty.sqlite'
    conn = ct.init_db(str(db))
    n = ct.insert_records_batch(conn, 1, 1, int(time.time()*1e9), [])
    assert n == 0
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM headpose')
    assert cur.fetchone()[0] == 0


def test_corrupted_short_payload_does_not_insert(tmp_path):
    db = tmp_path / 'corrupt.sqlite'
    conn = ct.init_db(str(db))
    sid = 77
    ts = int(time.time()*1e9)
    good = make_headpose_rec(1)
    bad = b'notlong'
    with pytest.raises(struct.error):
        ct.insert_records_batch(conn, 1, sid, ts, [good, bad])
    # ensure no rows were inserted
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM headpose WHERE session_id=?', (sid,))
    assert cur.fetchone()[0] == 0


def test_large_batch_inserts_all(tmp_path):
    db = tmp_path / 'large.sqlite'
    conn = ct.init_db(str(db))
    sid = 99
    ts = int(time.time()*1e9)
    N = 2000
    recs = [make_headpose_rec(i) for i in range(N)]
    n = ct.insert_records_batch(conn, 1, sid, ts, recs)
    assert n == N
    conn.commit()
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM headpose WHERE session_id=?', (sid,))
    assert cur.fetchone()[0] == N


def test_simulated_executemany_failure_and_rollback(tmp_path):
    # Simulate a failure in executemany that inserts some rows before raising
    db = tmp_path / 'tx.sqlite'
    conn = ct.init_db(str(db))
    sid = 1234
    ts = int(time.time()*1e9)

    recs = [make_headpose_rec(i) for i in range(10)]

    class BadCursor:
        def __init__(self, real):
            self._real = real

        def executemany(self, sql, seq_of_params):
            # insert half the rows then raise to simulate mid-batch failure
            half = max(1, len(seq_of_params) // 2)
            if half:
                self._real.executemany(sql, seq_of_params[:half])
            raise sqlite3.IntegrityError('simulated failure')

        def __getattr__(self, name):
            return getattr(self._real, name)

    class ProxyConn:
        def __init__(self, real):
            self._real = real

        def cursor(self):
            return BadCursor(self._real.cursor())

        def commit(self):
            return self._real.commit()

        def rollback(self):
            return self._real.rollback()

        def __getattr__(self, name):
            return getattr(self._real, name)

    proxy = ProxyConn(conn)
    with pytest.raises(sqlite3.IntegrityError):
        ct.insert_records_batch(proxy, 1, sid, ts, recs)
    # now rollback and ensure zero rows
    conn.rollback()
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM headpose WHERE session_id=?', (sid,))
    assert cur.fetchone()[0] == 0
