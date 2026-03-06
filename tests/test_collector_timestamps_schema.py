import time
import sqlite3
from UnityIntegration.python import collector_tail as ct


def test_recv_ts_ns_precision(tmp_path):
    db = tmp_path / 'ts.sqlite'
    conn = ct.init_db(str(db))
    sid = 10
    # choose a specific recv timestamp in nanoseconds
    recv_ts_ns = 123456789012345678
    recs = [
        # seq 1 record
        b'\x01\x00\x00\x00' + (123.0).to_bytes(4, 'little', signed=False) if False else None
    ]
    # craft a proper headpose record using struct to avoid endianness mistakes
    import struct
    rec = struct.pack('<I8f', 1, 0.5, 0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 1.0)
    n = ct.insert_records_batch(conn, 1, sid, recv_ts_ns, [rec])
    assert n == 1
    conn.commit()
    cur = conn.cursor()
    cur.execute('SELECT recv_ts_ns FROM headpose WHERE session_id=?', (sid,))
    row = cur.fetchone()
    assert row is not None
    assert row[0] == recv_ts_ns


def test_schema_contains_expected_columns(tmp_path):
    db = tmp_path / 'schema.sqlite'
    conn = ct.init_db(str(db))
    cur = conn.cursor()
    cur.execute("PRAGMA table_info('headpose')")
    cols = [r[1] for r in cur.fetchall()]
    expected = ['session_id', 'recv_ts_ns', 'seq', 'unity_t', 'px', 'py', 'pz', 'qx', 'qy', 'qz', 'qw']
    for col in expected:
        assert col in cols
