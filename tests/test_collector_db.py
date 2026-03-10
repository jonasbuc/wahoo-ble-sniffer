import struct
import time
from UnityIntegration.python import collector_tail as ct


def make_headpose_rec(seq, unity_t=1.0, px=0.1, py=0.2, pz=0.3, qx=0.0, qy=0.0, qz=0.0, qw=1.0):
    # seq u32 + 8 floats = 36 bytes
    return struct.pack('<I8f', seq, unity_t, px, py, pz, qx, qy, qz, qw)


def make_bike_rec(seq, unity_t=1.0, speed=5.5, steering=0.12, bf=0, br=0):
    # seq u32, unity_t f32, speed f32, steering f32, bf byte, br byte
    return struct.pack('<IfffBB', seq, unity_t, speed, steering, bf, br)


def make_hr_rec(seq, unity_t=1.0, hr_bpm=72.0):
    return struct.pack('<Iff', seq, unity_t, hr_bpm)


def test_insert_records_head_bike_hr(tmp_path):
    db = tmp_path / 'test_vrs.sqlite'
    conn = ct.init_db(str(db))

    sid = 12345
    recv_ts_ns = int(time.time() * 1e9)

    head_recs = [make_headpose_rec(1), make_headpose_rec(2, px=1.0)]
    bike_recs = [make_bike_rec(10), make_bike_rec(11, speed=7.7, bf=1, br=0)]
    hr_recs = [make_hr_rec(100, hr_bpm=60.0), make_hr_rec(101, hr_bpm=61.0)]

    n_head = ct.insert_records_batch(conn, 1, sid, recv_ts_ns, head_recs)
    n_bike = ct.insert_records_batch(conn, 2, sid, recv_ts_ns, bike_recs)
    n_hr = ct.insert_records_batch(conn, 3, sid, recv_ts_ns, hr_recs)

    assert n_head == 2
    assert n_bike == 2
    assert n_hr == 2

    conn.commit()

    cur = conn.cursor()
    cur.execute('SELECT seq, px FROM headpose WHERE session_id=? ORDER BY seq', (sid,))
    rows = cur.fetchall()
    assert len(rows) == 2
    assert rows[0][0] == 1
    # second record px was 1.0
    assert abs(rows[1][1] - 1.0) < 1e-6

    cur.execute('SELECT seq, speed, brake_front FROM bike WHERE session_id=? ORDER BY seq', (sid,))
    rows = cur.fetchall()
    assert len(rows) == 2
    assert rows[0][0] == 10
    assert abs(rows[1][1] - 7.7) < 1e-6
    assert rows[1][2] == 1

    cur.execute('SELECT seq, hr_bpm FROM hr WHERE session_id=? ORDER BY seq', (sid,))
    rows = cur.fetchall()
    assert len(rows) == 2
    assert abs(rows[0][1] - 60.0) < 1e-6


def test_insert_events_batch(tmp_path):
    db = tmp_path / 'test_vrs_events.sqlite'
    conn = ct.init_db(str(db))

    sid = 555
    recv_ts_ns = int(time.time() * 1e9)
    evs = [(1, 0.1, '{"a":1}'), (2, 0.2, '{"b":2}'), (3, 0.3, '{"c":3}')]

    n = ct.insert_events_batch(conn, sid, recv_ts_ns, evs)
    assert n == 3
    conn.commit()
    cur = conn.cursor()
    cur.execute('SELECT seq, json FROM events WHERE session_id=? ORDER BY seq', (sid,))
    rows = cur.fetchall()
    assert len(rows) == 3
    assert rows[0][1] == '{"a":1}'
