import os
import struct
import time
import threading
import json
import sqlite3
from UnityIntegration.python.collector_tail import watch_sessions


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
    for i in range(28, 36):
        hdr_copy[i] = 0
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


def make_hr_record(seq, t):
    return struct.pack('<I f f', seq, t, 120.0)


def make_event_record(seq, t, j):
    jb = json.dumps(j).encode('utf8')
    return struct.pack('<I f I', seq, t, len(jb)) + jb


def test_collector_reads_vrsf_and_writes_db(tmp_path):
    tmp = str(tmp_path)
    logs = os.path.join(tmp, 'Logs')
    os.makedirs(logs, exist_ok=True)
    sid = 424242
    sd = os.path.join(logs, f'session_{sid}')
    os.makedirs(sd, exist_ok=True)
    manifest = {'session_id': sid, 'started_unix_ms': int(
        time.time()*1000), 'files': ['headpose.vrsf', 'bike.vrsf', 'hr.vrsf', 'events.vrsf']}
    open(os.path.join(sd, 'manifest.json'), 'w').write(json.dumps(manifest))

    head_recs = [make_headpose_record(i, 0.01*i) for i in range(10)]
    write_vrsf_file(os.path.join(sd, 'headpose.vrsf'), 1, sid, head_recs)
    bike_recs = [make_bike_record(i, 0.02*i) for i in range(5)]
    write_vrsf_file(os.path.join(sd, 'bike.vrsf'), 2, sid, bike_recs)
    hr_recs = [make_hr_record(i, 0.05*i) for i in range(3)]
    write_vrsf_file(os.path.join(sd, 'hr.vrsf'), 3, sid, hr_recs)
    ev_recs = [make_event_record(i, 0.1*i, {'evt': 'test', 'i': i}) for i in range(4)]
    write_vrsf_file(os.path.join(sd, 'events.vrsf'), 4, sid, ev_recs)

    out_db = os.path.join(tmp, 'collector_out', 'vrs.sqlite')
    os.makedirs(os.path.dirname(out_db), exist_ok=True)
    stop_event = threading.Event()
    t = threading.Thread(target=watch_sessions, args=(
        logs, out_db, os.path.join(tmp, 'collector_out'), stop_event), daemon=True)
    t.start()
    time.sleep(1.5)
    stop_event.set()
    t.join(timeout=2.0)

    conn = sqlite3.connect(out_db)
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM headpose')
    head_count = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM bike')
    bike_count = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM hr')
    hr_count = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM events')
    ev_count = cur.fetchone()[0]

    assert head_count == 10
    assert bike_count == 5
    assert hr_count == 3
    assert ev_count == 4
