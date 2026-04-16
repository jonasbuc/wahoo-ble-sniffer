import os
import struct
import time
import threading
import json
import pytest
from bridge import collector_tail as ct


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


def make_event_record(seq, t, j):
    jb = json.dumps(j).encode('utf8')
    return struct.pack('<I f I', seq, t, len(jb)) + jb


@pytest.mark.parametrize('num_head,num_events,parquet_rows', [
    (2, 1, 1),
    (4, 2, 2),
])
def test_parquet_buffering_param(tmp_path, num_head, num_events, parquet_rows):
    # Skip if pyarrow not available
    if not ct.HAVE_PYARROW:
        pytest.skip('pyarrow not installed; skipping parquet buffer test')

    tmp = str(tmp_path)
    logs = os.path.join(tmp, 'Logs')
    os.makedirs(logs, exist_ok=True)
    sid = 333
    sd = os.path.join(logs, f'session_{sid}')
    os.makedirs(sd, exist_ok=True)
    manifest = {'session_id': sid, 'started_unix_ms': int(time.time()*1000), 'files': ['headpose.vrsf', 'events.vrsf']}
    open(os.path.join(sd, 'manifest.json'), 'w').write(json.dumps(manifest))

    head_recs = [make_headpose_record(i, 0.01*i) for i in range(num_head)]
    write_vrsf_file(os.path.join(sd, 'headpose.vrsf'), 1, sid, head_recs)
    ev_recs = [make_event_record(i, 0.1*i, {'evt': 'p', 'i': i}) for i in range(num_events)]
    write_vrsf_file(os.path.join(sd, 'events.vrsf'), 4, sid, ev_recs)

    out_db = os.path.join(tmp, 'collector_out', 'vrs.sqlite')
    out_parquet = os.path.join(tmp, 'collector_out')
    os.makedirs(os.path.dirname(out_db), exist_ok=True)
    stop_event = threading.Event()
    t = threading.Thread(target=ct.watch_sessions, args=(
        logs, out_db, out_parquet, stop_event, 0, parquet_rows), daemon=True)
    t.start()
    time.sleep(1.0)
    stop_event.set()
    t.join(timeout=2.0)

    # Look for parquet parts
    part_dir = os.path.join(out_parquet, f'session_{sid}_parquet')
    files = []
    if os.path.exists(part_dir):
        files = [p for p in os.listdir(part_dir) if p.endswith('.parquet')]
    assert files, 'No parquet part files written'
    # ensure at least one part file exists and is non-empty
    found_nonzero = False
    for f in files:
        p = os.path.join(part_dir, f)
        if os.path.getsize(p) > 0:
            found_nonzero = True
            break
    assert found_nonzero
