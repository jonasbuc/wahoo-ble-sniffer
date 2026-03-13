import struct
import zlib
from pathlib import Path

from UnityIntegration.python.collector_tail import FileTail, init_db


def make_header(payload: bytes, version: int = 1, stream_id: int = 1) -> bytes:
    hdr = bytearray(40)
    hdr[0:4] = b'VRSF'
    hdr[4] = version
    hdr[5] = stream_id
    payload_len = len(payload)
    hdr[24:28] = struct.pack('<I', payload_len)
    hdr_copy = bytearray(hdr)
    for i in range(28, 36):
        hdr_copy[i] = 0
    hcrc = zlib.crc32(hdr_copy) & 0xFFFFFFFF
    pcrc = zlib.crc32(payload) & 0xFFFFFFFF
    hdr[28:32] = struct.pack('<I', hcrc)
    hdr[32:36] = struct.pack('<I', pcrc)
    return bytes(hdr)


def write_vrsf(path: Path, payload: bytes, stream_id: int = 1):
    hdr = make_header(payload, stream_id=stream_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as f:
        f.write(hdr)
        f.write(payload)


def pack_headpose(seq, unity_t, px, py, pz, qx, qy, qz, qw):
    return struct.pack('<Iffffffff', seq, unity_t, px, py, pz, qx, qy, qz, qw)


def pack_bike(seq, unity_t, speed, steering, bf=0, br=0):
    rec = struct.pack('<Ifff', seq, unity_t, speed, steering)
    return rec + bytes([bf, br])


def pack_hr(seq, unity_t, hr):
    return struct.pack('<Iff', seq, unity_t, hr)


def pack_event(seq, unity_t, js: str):
    jb = js.encode('utf8')
    return struct.pack('<IfI', seq, unity_t, len(jb)) + jb


def test_collector_end_to_end(tmp_path):
    # Prepare temp DB and files
    dbp = tmp_path / 'collector.sqlite'
    conn = init_db(str(dbp))

    logs = tmp_path / 'logs'
    logs.mkdir()

    # session id used for inserts
    session_id = 777

    # headpose: two records
    h1 = pack_headpose(1, 0.01, 0.1, 0.2, 0.3, 0, 0, 0, 1)
    h2 = pack_headpose(2, 0.02, 0.2, 0.2, 0.3, 0, 0, 0, 1)
    write_vrsf(logs / 'headpose.vrsf', h1 + h2, stream_id=1)

    # bike: two records
    b1 = pack_bike(1, 0.01, 3.0, 0.1)
    b2 = pack_bike(2, 0.02, 4.0, 0.1)
    write_vrsf(logs / 'bike.vrsf', b1 + b2, stream_id=2)

    # hr: two records
    r1 = pack_hr(1, 0.05, 60.0)
    r2 = pack_hr(2, 0.10, 61.0)
    write_vrsf(logs / 'hr.vrsf', r1 + r2, stream_id=3)

    # events: two variable records
    e1 = pack_event(1, 0.1, '{"evt":"x"}')
    e2 = pack_event(2, 0.2, '{"evt":"y"}')
    write_vrsf(logs / 'events.vrsf', e1 + e2, stream_id=4)

    # Tail each file and insert into DB
    # small sleep to ensure monotonic_ns changes (not strictly necessary)
    tails = [
        FileTail(str(logs / 'headpose.vrsf'), 1, session_id, rec_size=36, variable=False),
        FileTail(str(logs / 'bike.vrsf'), 2, session_id, rec_size=18, variable=False),
        FileTail(str(logs / 'hr.vrsf'), 3, session_id, rec_size=12, variable=False),
        FileTail(str(logs / 'events.vrsf'), 4, session_id, variable=True),
    ]

    for ft in tails:
        recv_ns, parsed = ft.tail_once()
        assert recv_ns is not None
        if ft.variable:
            # insert_events_batch expects list of tuples (seq, unity_t, js)
            cur = conn.cursor()
            inserted = 0
            from UnityIntegration.python.collector_tail import insert_events_batch, insert_records_batch
            inserted = insert_events_batch(conn, session_id, recv_ns, parsed)
            conn.commit()
        else:
            from UnityIntegration.python.collector_tail import insert_records_batch
            inserted = insert_records_batch(conn, ft.stream_id, session_id, recv_ns, parsed)
            conn.commit()
        assert inserted > 0

    # verify counts
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM headpose')
    assert cur.fetchone()[0] == 2
    cur.execute('SELECT COUNT(*) FROM bike')
    assert cur.fetchone()[0] == 2
    cur.execute('SELECT COUNT(*) FROM hr')
    assert cur.fetchone()[0] == 2
    cur.execute('SELECT COUNT(*) FROM events')
    assert cur.fetchone()[0] == 2

    # create readable views and assert they exist
    # import module by path to avoid package issues
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location('create_readable_views', str(
        Path('UnityIntegration/python/db/create_readable_views.py')))
    mod = importlib.util.module_from_spec(spec)
    sys.modules['create_readable_views'] = mod
    spec.loader.exec_module(mod)
    # Call the library function directly so argparse doesn't read pytest's sys.argv
    mod.create_views(dbp)

    # query a readable view
    cur.execute('SELECT recv_ts_ms FROM headpose_readable LIMIT 1')
    r = cur.fetchone()
    assert r is not None
    conn.close()
