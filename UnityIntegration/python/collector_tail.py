#!/usr/bin/env python3
"""
collector_tail.py

Tail VRSF files produced by Unity, validate chunks, import into SQLite (WAL) and optionally write Parquet parts.

Usage: python collector_tail.py --logs Logs --out collector_out/vrs.sqlite
"""
import argparse
import os
import struct
import time
import sqlite3
import threading
from typing import DefaultDict, Tuple, List, Dict, Optional
import json
import glob
from collections import defaultdict

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    HAVE_PYARROW = True
except Exception:
    HAVE_PYARROW = False

HEADER_FMT = "<4s B B H Q I I I I I"  # 4 +1+1+2+8+4+4+4+4+4 = 36? but we read 40 bytes; we will parse manually
HEADER_SIZE = 40


def read_u32_le(b, off):
    return struct.unpack_from('<I', b, off)[0]


def read_u64_le(b, off):
    return struct.unpack_from('<Q', b, off)[0]


def crc32(data):
    import zlib
    return zlib.crc32(data) & 0xffffffff


class FileTail:
    def __init__(self, path, stream_id, session_id, rec_size=None, variable=False):
        self.path = path
        self.stream_id = stream_id
        self.session_id = session_id
        self.rec_size = rec_size
        self.variable = variable
        self.offset = 0
        self.missing = 0
        self.last_seq = None

    def tail_once(self):
        """Read the next complete chunk if present.

        Return (recv_ts_ns, list_of_parsed_records) or (None, None) if nothing to process.
        """
        if not os.path.exists(self.path):
            return None, None
        size = os.path.getsize(self.path)
        if size - self.offset < HEADER_SIZE:
            return None, None
        with open(self.path, 'rb') as f:
            f.seek(self.offset)
            hdr = f.read(HEADER_SIZE)
            if len(hdr) < HEADER_SIZE:
                return None, None
            # parse header
            magic = hdr[0:4]
            if magic != b'VRSF':
                print('Bad magic in', self.path)
                self.offset += 1
                return None, None
            payload_bytes = read_u32_le(hdr, 24)
            header_crc = read_u32_le(hdr, 28)
            payload_crc = read_u32_le(hdr, 32)

            # wait until payload available
            if size - self.offset < HEADER_SIZE + payload_bytes:
                return None, None

            # verify header crc
            hdr_copy = bytearray(hdr)
            for i in range(28, 36):
                hdr_copy[i] = 0
            if crc32(hdr_copy) != header_crc:
                print('Header CRC mismatch', self.path)
                self.offset += 1
                return None, None

            payload = f.read(payload_bytes)
            if crc32(payload) != payload_crc:
                print('Payload CRC mismatch', self.path)
                # skip faulty chunk
                self.offset += HEADER_SIZE + payload_bytes
                return None, None

            # parse payload into records
            recv_ts_ns = time.monotonic_ns()
            parsed = []
            off = 0
            if not self.variable:
                rec_size = self.rec_size
                while off + rec_size <= len(payload):
                    rec = payload[off:off+rec_size]
                    parsed.append(rec)
                    off += rec_size
            else:
                # variable records: seq u32, unity_t f32, json_len u32, json bytes
                while off + 12 <= len(payload):
                    seq = struct.unpack_from('<I', payload, off)[0]
                    unity_t = struct.unpack_from('<f', payload, off+4)[0]
                    jlen = struct.unpack_from('<I', payload, off+8)[0]
                    if off + 12 + jlen > len(payload):
                        break
                    js = payload[off+12:off+12+jlen].decode('utf8', errors='replace')
                    parsed.append((seq, unity_t, js))
                    off += 12 + jlen

            self.offset += HEADER_SIZE + payload_bytes
            return recv_ts_ns, parsed


def init_db(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    cur = conn.cursor()
    cur.execute('PRAGMA journal_mode=WAL;')
    cur.execute('PRAGMA synchronous=NORMAL;')
    cur.execute('PRAGMA temp_store=MEMORY;')
    cur.execute(
        'CREATE TABLE IF NOT EXISTS sessions'
        '(session_id INTEGER PRIMARY KEY, started_unix_ms INTEGER, session_dir TEXT)'
    )
    cur.execute(
        'CREATE TABLE IF NOT EXISTS headpose'
        '(session_id INTEGER, recv_ts_ns INTEGER, seq INTEGER, unity_t REAL,'
        ' px REAL, py REAL, pz REAL, qx REAL, qy REAL, qz REAL, qw REAL)'
    )
    cur.execute(
        'CREATE INDEX IF NOT EXISTS idx_headpose_sid ON headpose(session_id, recv_ts_ns)'
    )
    cur.execute(
        'CREATE TABLE IF NOT EXISTS bike'
        '(session_id INTEGER, recv_ts_ns INTEGER, seq INTEGER, unity_t REAL,'
        ' speed REAL, steering REAL, brake_front INTEGER, brake_rear INTEGER)'
    )
    cur.execute(
        'CREATE INDEX IF NOT EXISTS idx_bike_sid ON bike(session_id, recv_ts_ns)'
    )
    cur.execute(
        'CREATE TABLE IF NOT EXISTS hr'
        '(session_id INTEGER, recv_ts_ns INTEGER, seq INTEGER, unity_t REAL, hr_bpm REAL)'
    )
    cur.execute(
        'CREATE INDEX IF NOT EXISTS idx_hr_sid ON hr(session_id, recv_ts_ns)'
    )
    cur.execute(
        'CREATE TABLE IF NOT EXISTS events'
        '(session_id INTEGER, recv_ts_ns INTEGER, seq INTEGER, unity_t REAL, json TEXT)'
    )
    cur.execute(
        'CREATE INDEX IF NOT EXISTS idx_events_sid ON events(session_id, recv_ts_ns)'
    )
    conn.commit()
    return conn


def insert_records_batch(conn, stream_id, session_id, recv_ts_ns, recs):
    cur = conn.cursor()
    if not recs:
        return 0
    if stream_id == 1:
        rows = []
        for rec in recs:
            seq = struct.unpack_from('<I', rec, 0)[0]
            unity_t = struct.unpack_from('<f', rec, 4)[0]
            px = struct.unpack_from('<f', rec, 8)[0]
            py = struct.unpack_from('<f', rec, 12)[0]
            pz = struct.unpack_from('<f', rec, 16)[0]
            qx = struct.unpack_from('<f', rec, 20)[0]
            qy = struct.unpack_from('<f', rec, 24)[0]
            qz = struct.unpack_from('<f', rec, 28)[0]
            qw = struct.unpack_from('<f', rec, 32)[0]
            rows.append((session_id, recv_ts_ns, seq, unity_t, px, py, pz, qx, qy, qz, qw))
        cur.executemany(
            'INSERT INTO headpose'
            '(session_id, recv_ts_ns, seq, unity_t, px,py,pz,qx,qy,qz,qw)'
            ' VALUES(?,?,?,?,?,?,?,?,?,?,?)', rows)
        return len(rows)
    elif stream_id == 2:
        rows = []
        for rec in recs:
            seq = struct.unpack_from('<I', rec, 0)[0]
            unity_t = struct.unpack_from('<f', rec, 4)[0]
            speed = struct.unpack_from('<f', rec, 8)[0]
            steering = struct.unpack_from('<f', rec, 12)[0]
            bf = rec[16]
            br = rec[17]
            rows.append((session_id, recv_ts_ns, seq, unity_t, speed, steering, bf, br))
        cur.executemany(
            'INSERT INTO bike'
            '(session_id, recv_ts_ns, seq, unity_t, speed, steering, brake_front, brake_rear)'
            ' VALUES(?,?,?,?,?,?,?,?)', rows)
        return len(rows)
    elif stream_id == 3:
        rows = []
        for rec in recs:
            seq = struct.unpack_from('<I', rec, 0)[0]
            unity_t = struct.unpack_from('<f', rec, 4)[0]
            hr_bpm = struct.unpack_from('<f', rec, 8)[0]
            rows.append((session_id, recv_ts_ns, seq, unity_t, hr_bpm))
        cur.executemany('INSERT INTO hr(session_id, recv_ts_ns, seq, unity_t, hr_bpm) VALUES(?,?,?,?,?)', rows)
        return len(rows)
    return 0


def insert_events_batch(conn, session_id, recv_ts_ns, rec_tuples):
    if not rec_tuples:
        return 0
    rows = []
    for (seq, unity_t, js) in rec_tuples:
        rows.append((session_id, recv_ts_ns, seq, unity_t, js))
    cur = conn.cursor()
    cur.executemany('INSERT INTO events(session_id, recv_ts_ns, seq, unity_t, json) VALUES(?,?,?,?,?)', rows)
    return len(rows)


PARQUET_BUFFERS: DefaultDict[Tuple[int, int], List[Dict[str, object]]] = defaultdict(list)
PARQUET_PART_COUNTER: DefaultDict[Tuple[int, int], int] = defaultdict(int)
PARQUET_LOCK = threading.Lock()


def flush_parquet_parts(out_dir, part_rows=10000):
    if not HAVE_PYARROW:
        return
    with PARQUET_LOCK:
        items = list(PARQUET_BUFFERS.items())
        for (sid, stream_id), rows in items:
            if not rows:
                continue
            part_dir = os.path.join(out_dir, f'session_{sid}_parquet')
            os.makedirs(part_dir, exist_ok=True)
            # write in chunks of part_rows
            while rows:
                chunk = rows[:part_rows]
                part_idx = PARQUET_PART_COUNTER[(sid, stream_id)]
                part_file = os.path.join(part_dir, f'stream{stream_id}_part_{part_idx}.parquet')
                try:
                    table = pa.Table.from_pylist(chunk)
                    pq.write_table(table, part_file)
                    PARQUET_PART_COUNTER[(sid, stream_id)] += 1
                    print(f'Wrote parquet part: {part_file} ({len(chunk)} rows)')
                except Exception as e:
                    print('Parquet write error:', e)
                    break
                rows = rows[part_rows:]
            PARQUET_BUFFERS[(sid, stream_id)] = []


def watch_sessions(
    logs_root: str,
    out_db: str,
    out_parquet_dir: Optional[str] = None,
    stop_event: Optional[threading.Event] = None,
    sqlite_batch_size: int = 0,
    parquet_rows: int = 10000,
):
    conn = init_db(out_db)
    seen = set()
    tails = []

    def scan_once():
        for d in sorted(glob.glob(os.path.join(logs_root, 'session_*'))):
            if d in seen:
                continue
            manifest = os.path.join(d, 'manifest.json')
            if not os.path.exists(manifest):
                continue
            with open(manifest, 'r') as f:
                m = json.load(f)
            sid = m.get('session_id')
            # create tails
            tails.append(FileTail(os.path.join(d, 'headpose.vrsf'), 1, sid, rec_size=36, variable=False))
            tails.append(FileTail(os.path.join(d, 'bike.vrsf'), 2, sid, rec_size=20, variable=False))
            tails.append(FileTail(os.path.join(d, 'hr.vrsf'), 3, sid, rec_size=12, variable=False))
            tails.append(FileTail(os.path.join(d, 'events.vrsf'), 4, sid, variable=True))
            seen.add(d)

    print('Collector: watching', logs_root)
    last_print = time.time()
    last_parquet_flush = time.time()
    counts: DefaultDict[int, int] = defaultdict(int)
    pending_inserts = 0
    while True:
        if stop_event and stop_event.is_set():
            break
        scan_once()
        total_inserted = 0
        for t in list(tails):
            recv_ts_ns, parsed = t.tail_once()
            if parsed is None:
                continue
            sid = t.session_id
            inserted = 0
            # perform batch DB insert per chunk (single transaction scope)
            try:
                # execute inserts (these will be part of current transaction until conn.commit())
                if t.stream_id == 4:
                    inserted = insert_events_batch(conn, sid, recv_ts_ns, parsed)
                else:
                    inserted = insert_records_batch(conn, t.stream_id, sid, recv_ts_ns, parsed)
            except Exception as e:
                print('DB insert error:', e)
                inserted = 0

            if inserted:
                counts[t.stream_id] += inserted
                total_inserted += inserted
                pending_inserts += inserted

                # buffer for parquet
                if out_parquet_dir and HAVE_PYARROW:
                    key = (sid, t.stream_id)
                    if t.stream_id == 1:
                        for rec in parsed:
                            seq = struct.unpack_from('<I', rec, 0)[0]
                            unity_t = struct.unpack_from('<f', rec, 4)[0]
                            px = struct.unpack_from('<f', rec, 8)[0]
                            py = struct.unpack_from('<f', rec, 12)[0]
                            pz = struct.unpack_from('<f', rec, 16)[0]
                            qx = struct.unpack_from('<f', rec, 20)[0]
                            qy = struct.unpack_from('<f', rec, 24)[0]
                            qz = struct.unpack_from('<f', rec, 28)[0]
                            qw = struct.unpack_from('<f', rec, 32)[0]
                            PARQUET_BUFFERS[key].append({
                                'session_id': sid, 'recv_ts_ns': recv_ts_ns, 'seq': seq,
                                'unity_t': unity_t, 'px': px, 'py': py, 'pz': pz,
                                'qx': qx, 'qy': qy, 'qz': qz, 'qw': qw,
                            })
                    elif t.stream_id == 2:
                        for rec in parsed:
                            seq = struct.unpack_from('<I', rec, 0)[0]
                            unity_t = struct.unpack_from('<f', rec, 4)[0]
                            speed = struct.unpack_from('<f', rec, 8)[0]
                            steering = struct.unpack_from('<f', rec, 12)[0]
                            bf = rec[16]
                            br = rec[17]
                            PARQUET_BUFFERS[key].append({
                                'session_id': sid, 'recv_ts_ns': recv_ts_ns, 'seq': seq,
                                'unity_t': unity_t, 'speed': speed, 'steering': steering,
                                'brake_front': bf, 'brake_rear': br,
                            })
                    elif t.stream_id == 3:
                        for rec in parsed:
                            seq = struct.unpack_from('<I', rec, 0)[0]
                            unity_t = struct.unpack_from('<f', rec, 4)[0]
                            hr_bpm = struct.unpack_from('<f', rec, 8)[0]
                            PARQUET_BUFFERS[key].append({
                                'session_id': sid, 'recv_ts_ns': recv_ts_ns,
                                'seq': seq, 'unity_t': unity_t, 'hr_bpm': hr_bpm,
                            })
                    elif t.stream_id == 4:
                        for (seq, unity_t, js) in parsed:
                            PARQUET_BUFFERS[key].append({
                                'session_id': sid, 'recv_ts_ns': recv_ts_ns,
                                'seq': seq, 'unity_t': unity_t, 'json': js,
                            })

            # commit behavior: commit per chunk (sqlite_batch_size==0), otherwise commit when pending >= batch size
            if sqlite_batch_size <= 0:
                try:
                    conn.commit()
                    pending_inserts = 0
                except Exception:
                    pass
            else:
                if pending_inserts >= sqlite_batch_size:
                    try:
                        conn.commit()
                        pending_inserts = 0
                    except Exception:
                        pass

        if time.time() - last_print >= 1.0:
            print(f"rates head={counts[1]} bike={counts[2]} hr={counts[3]} events={counts[4]}")
            counts = defaultdict(int)
            last_print = time.time()

        # parquet flush every second
        if out_parquet_dir and HAVE_PYARROW and (time.time() - last_parquet_flush) >= 1.0:
            flush_parquet_parts(out_parquet_dir, part_rows=parquet_rows)
            last_parquet_flush = time.time()

        time.sleep(0.1)

    # finalize: commit any pending inserts and flush parquet
    try:
        if pending_inserts > 0:
            conn.commit()
    except Exception:
        pass
    if out_parquet_dir and HAVE_PYARROW:
        flush_parquet_parts(out_parquet_dir, part_rows=parquet_rows)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--logs', default='Logs')
    p.add_argument('--out', default='collector_out/vrs.sqlite')
    p.add_argument('--sqlite-batch-size', type=int, default=0,
                   help='If >0, commit DB every N records; 0 means commit per chunk')
    p.add_argument('--parquet-rows', type=int, default=10000, help='Max rows per parquet part file')
    args = p.parse_args()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    watch_sessions(args.logs, args.out, out_parquet_dir=os.path.dirname(args.out),
                   sqlite_batch_size=args.sqlite_batch_size, parquet_rows=args.parquet_rows)


if __name__ == '__main__':
    main()
