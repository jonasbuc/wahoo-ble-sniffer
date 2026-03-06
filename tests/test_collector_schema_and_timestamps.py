import os
import struct
import time
import threading
import json
import tempfile
import sqlite3
from UnityIntegration.python.collector_tail import watch_sessions, init_db


def crc32(b):
    import zlib
    return zlib.crc32(b) & 0xffffffff


def build_header(stream_id, session_id, chunk_seq, record_count, payload_bytes):
    hdr = bytearray(40)
    hdr[0:4] = b'VRSF'
    hdr[4] = 1
    hdr[5] = stream_id
    hdr[6:8] = (0).to_bytes(2, 'little')
    hdr[8:16] = (session_id).to_bytes(8, 'little')
    hdr[16:20] = (chunk_seq).to_bytes(4, 'little')
    hdr[20:24] = (record_count).to_bytes(4, 'little')
    hdr[24:28] = (payload_bytes).to_bytes(4, 'little')
    hdr_copy = bytearray(hdr)
    for i in range(28,36): hdr_copy[i] = 0
    header_crc = crc32(hdr_copy)
    return hdr, header_crc


def write_vrsf_file(path, stream_id, session_id, records_bytes_list):
    payload = b''.join(records_bytes_list)
    payload_bytes = len(payload)
    hdr, header_crc = build_header(stream_id, session_id, 0, len(records_bytes_list), payload_bytes)
    payload_crc = crc32(payload)
    hdr[28:32] = (header_crc).to_bytes(4, 'little')
    hdr[32:36] = (payload_crc).to_bytes(4, 'little')
    with open(path, 'ab') as f:
        f.write(hdr)
        f.write(payload)


def make_headpose_record(seq, t):
    return struct.pack('<I f f f f f f f f', seq, t, 1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 1.0)


def make_bike_record(seq, t):
    return struct.pack('<I f f f B B H', seq, t, 5.0, 0.1, 1, 0, 0)


def test_tables_have_expected_columns(tmp_path):
    db = tmp_path / 'schema_check.sqlite'
    conn = init_db(str(db))
    # headpose checked elsewhere; check bike/hr/events
    cur = conn.cursor()
    cur.execute("PRAGMA table_info('bike')")
    bike_cols = [r[1] for r in cur.fetchall()]
    assert 'speed' in bike_cols and 'brake_front' in bike_cols

    cur.execute("PRAGMA table_info('hr')")
    hr_cols = [r[1] for r in cur.fetchall()]
    assert 'hr_bpm' in hr_cols

    cur.execute("PRAGMA table_info('events')")
    ev_cols = [r[1] for r in cur.fetchall()]
    assert 'json' in ev_cols


def test_recv_ts_ns_consistent_across_streams(tmp_path):
    tmp = str(tmp_path)
    logs = os.path.join(tmp, 'Logs')
    os.makedirs(logs, exist_ok=True)
    sid = 1111
    sd = os.path.join(logs, f'session_{sid}')
    os.makedirs(sd, exist_ok=True)
    manifest = {'session_id': sid, 'started_unix_ms': int(time.time()*1000), 'files': ['headpose.vrsf','bike.vrsf']}
    open(os.path.join(sd,'manifest.json'),'w').write(json.dumps(manifest))

    # Write one chunk per file so they will be processed and recv_ts_ns captured per chunk
    head_recs = [make_headpose_record(1, 0.01)]
    write_vrsf_file(os.path.join(sd,'headpose.vrsf'), 1, sid, head_recs)
    bike_recs = [make_bike_record(1, 0.02)]
    write_vrsf_file(os.path.join(sd,'bike.vrsf'), 2, sid, bike_recs)

    out_db = os.path.join(tmp, 'collector_out', 'vrs.sqlite')
    os.makedirs(os.path.dirname(out_db), exist_ok=True)
    stop_event = threading.Event()
    t = threading.Thread(target=watch_sessions, args=(logs, out_db, None, stop_event), daemon=True)
    t.start()
    time.sleep(1.0)
    stop_event.set()
    t.join(timeout=2.0)

    conn = sqlite3.connect(out_db)
    cur = conn.cursor()
    cur.execute('SELECT recv_ts_ns FROM headpose WHERE session_id=?', (sid,))
    hts = [r[0] for r in cur.fetchall()]
    cur.execute('SELECT recv_ts_ns FROM bike WHERE session_id=?', (sid,))
    bts = [r[0] for r in cur.fetchall()]

    # There should be one value in each; ensure types are integers and equal if both present
    assert hts and bts
    assert isinstance(hts[0], int) and isinstance(bts[0], int)
    # For these small synthetic files processed close together we expect non-decreasing and often equal timestamps
    assert abs(hts[0] - bts[0]) < 1_000_000  # within 1ms
