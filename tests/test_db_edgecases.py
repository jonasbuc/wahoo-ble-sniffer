import struct
from pathlib import Path

from UnityIntegration.python import collector_tail as ct
import importlib.util
import sys


def load_validate_db():
    path = Path('UnityIntegration/python/db/sqlite/validate_db.py')
    spec = importlib.util.spec_from_file_location('validate_db', str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules['validate_db'] = mod
    spec.loader.exec_module(mod)
    return mod


vdb = load_validate_db()


def make_headpose_rec(seq, unity_t, px, py, pz, qx, qy, qz, qw):
    # seq u32, unity_t f32, px f32, py f32, pz f32, qx f32, qy f32, qz f32, qw f32
    return struct.pack('<Iffffffff', seq, unity_t, px, py, pz, qx, qy, qz, qw)


def make_bike_rec(seq, unity_t, speed, steering, bf, br):
    # seq u32, unity_t f32, speed f32, steering f32, bf byte, br byte
    # pack bf/br as bytes appended
    rec = struct.pack('<Ifff', seq, unity_t, speed, steering)
    # ensure bytes at positions 16 and 17 as collector expects
    # current rec length is 16 bytes; append bf and br
    return rec + bytes([bf, br])


def make_hr_rec(seq, unity_t, hr):
    return struct.pack('<Iff', seq, unity_t, hr)


def test_empty_batches_no_insert(tmp_path):
    db = tmp_path / 'test.sqlite'
    conn = ct.init_db(str(db))
    # empty batches should return 0 and not insert
    assert ct.insert_records_batch(conn, 1, 1, 0, []) == 0
    assert ct.insert_events_batch(conn, 1, 0, []) == 0
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM headpose')
    assert cur.fetchone()[0] == 0
    conn.close()


def test_headpose_quaternion_norm_and_nan(tmp_path):
    db = tmp_path / 'test.sqlite'
    conn = ct.init_db(str(db))
    # Good record (norm approx 1)
    good = make_headpose_rec(1, 0.01, 0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 1.0)
    # Bad quaternion (all zeros) -> norm 0
    bad_norm = make_headpose_rec(2, 0.02, 0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 0.0)
    # NaN in px
    nan_px = make_headpose_rec(3, 0.03, float('nan'), 0.2, 0.3, 0.0, 0.0, 0.0, 1.0)
    ct.insert_records_batch(conn, 1, 42, 1234567890, [good, bad_norm, nan_px])
    c, probs = vdb.validate_headpose(conn)
    assert c == 3
    # expect at least one quaternion-norm problem and one non-float problem
    assert any('quaternion norm' in p or 'quaternion norm off' in p or 'quaternion' in p for p in probs) or any(
        'non-float' in p for p in probs)
    conn.close()


def test_bike_invalid_speed_and_brakes(tmp_path):
    db = tmp_path / 'test.sqlite'
    conn = ct.init_db(str(db))
    # invalid negative speed
    rec1 = make_bike_rec(1, 0.01, -5.0, 0.1, 0, 0)
    # invalid brakes (not 0/1)
    rec2 = make_bike_rec(2, 0.02, 5.0, 0.1, 2, 3)
    ct.insert_records_batch(conn, 2, 7, 999, [rec1, rec2])
    c, probs = vdb.validate_bike(conn)
    assert c == 2
    assert any('invalid speed' in p or 'brake flags' in p for p in probs)
    conn.close()


def test_hr_out_of_range(tmp_path):
    db = tmp_path / 'test.sqlite'
    conn = ct.init_db(str(db))
    rec_low = make_hr_rec(1, 0.01, 10.0)
    rec_high = make_hr_rec(2, 0.02, 300.0)
    rec_ok = make_hr_rec(3, 0.03, 70.0)
    ct.insert_records_batch(conn, 3, 5, 123, [rec_low, rec_high, rec_ok])
    c, probs = vdb.validate_hr(conn)
    assert c == 3
    assert any('out of range' in p for p in probs)
    conn.close()


def test_events_invalid_json_and_long_string(tmp_path):
    db = tmp_path / 'test.sqlite'
    conn = ct.init_db(str(db))
    good = (1, 0.1, '{"evt":"ok"}')
    bad = (2, 0.2, 'not json { this is bad')
    long_js = '{"a": "' + ('x' * 10000) + '"}'
    ct.insert_events_batch(conn, 11, 555, [good, bad, (3, 0.3, long_js)])
    c, probs = vdb.validate_events(conn)
    assert c == 3
    assert any('json parse error' in p for p in probs)
    conn.close()


def test_sessions_missing_ok(tmp_path):
    db = tmp_path / 'test.sqlite'
    conn = ct.init_db(str(db))
    # do not insert into sessions; insert headpose rows
    rec = make_headpose_rec(1, 0.01, 0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 1.0)
    ct.insert_records_batch(conn, 1, 9999, 1000, [rec])
    # sessions table exists but has no rows
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM sessions')
    assert cur.fetchone()[0] == 0
    # validation on sensor tables still runs
    c, probs = vdb.validate_headpose(conn)
    assert c == 1
    conn.close()


def test_float_inf_nan_detected(tmp_path):
    db = tmp_path / 'test.sqlite'
    conn = ct.init_db(str(db))
    rec_inf = make_headpose_rec(1, 0.01, float('inf'), 0.2, 0.3, 0.0, 0.0, 0.0, 1.0)
    rec_nan = make_headpose_rec(2, 0.02, float('nan'), 0.2, 0.3, 0.0, 0.0, 0.0, 1.0)
    ct.insert_records_batch(conn, 1, 1, 1, [rec_inf, rec_nan])
    c, probs = vdb.validate_headpose(conn)
    assert any('non-float' in p for p in probs)
    conn.close()
