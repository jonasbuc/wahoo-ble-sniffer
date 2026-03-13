#!/usr/bin/env python3
"""
collector_tail.py
=================
Continuously tail VRSF binary log files written by Unity, validate each
chunk with CRC32 checksums, and import the records into SQLite.
Optionally also writes Parquet part-files for large-scale offline analysis.

VRSF Chunk Binary Layout
------------------------
Every stream file is a sequence of self-describing 40-byte headers followed
by a variable-length payload.  The collector reads them one chunk at a time,
always resuming where it left off (stored in FileTail.offset).

  Offset  Size  Type     Field
  ------  ----  -------  ----------------------------------------
     0     4    char[4]  Magic = "VRSF"
     4     1    uint8    Version (currently 1)
     5     1    uint8    StreamId  (1=headpose, 2=bike, 3=hr, 4=events)
     6     2    uint16   Flags (reserved, write 0)
     8     8    uint64   SessionId
    16     4    uint32   ChunkSeq  (monotonically increasing per stream)
    20     4    uint32   RecordCount
    24     4    uint32   PayloadBytes
    28     4    uint32   HeaderCRC32  (computed with this field = 0)
    32     4    uint32   PayloadCRC32
    36     4    uint32   Reserved
  ----
  Total = 40 bytes header + PayloadBytes payload

Record sizes per stream (fixed-size streams):
  Stream 1 – headpose : 36 bytes  (seq u32, unity_t f32, px py pz f32×3, qx qy qz qw f32×4)
  Stream 2 – bike     : 20 bytes  (seq u32, unity_t f32, speed f32, steering f32, bf u8, br u8, pad 2)
  Stream 3 – hr       :  12 bytes  (seq u32, unity_t f32, hr_bpm f32)
  Stream 4 – events   : variable   (seq u32, unity_t f32, json_len u32, json UTF-8)

Outputs
-------
- SQLite database (WAL mode, NORMAL sync) at --out path
  Tables: sessions, headpose, bike, hr, events
- Optional Parquet part-files written alongside the SQLite file every second

Usage
-----
  python collector_tail.py --logs Logs --out collector_out/vrs.sqlite
"""
import argparse
import glob
import json
import logging
import os
import sqlite3
import struct
import threading
import time
import zlib
from collections import defaultdict
from pathlib import Path
from typing import DefaultDict, Dict, List, Optional, Tuple

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    HAVE_PYARROW = True
except Exception:
    HAVE_PYARROW = False

LOG = logging.getLogger("collector")

HEADER_FMT = "<4s B B H Q I I I I I"  # layout reference only; fields parsed manually below
HEADER_SIZE = 40  # total bytes in the VRSF chunk header


# ── Low-level binary helpers ──────────────────────────────────────────────────

def read_u32_le(b, off):
    """Unpack a 4-byte little-endian unsigned integer from buffer *b* at *off*."""
    return struct.unpack_from('<I', b, off)[0]


def read_u64_le(b, off):
    """Unpack an 8-byte little-endian unsigned integer from buffer *b* at *off*."""
    return struct.unpack_from('<Q', b, off)[0]


def crc32(data):
    """Return the CRC32 checksum of *data* (standard zlib/IEEE 802.3 polynomial).

    The result is masked to 32 bits to ensure a non-negative value on all
    platforms (Python's zlib.crc32 can return a signed int on Python 2;
    masking makes the behaviour explicit).
    """
    return zlib.crc32(data) & 0xffffffff


# ── FileTail: byte-offset cursor over a single .vrsf stream file ─────────────

class FileTail:
    """Stateful reader that incrementally consumes one VRSF stream file.

    The file is opened fresh on every call to ``tail_once()``, which avoids
    holding a file handle open across long idle periods (Unity may be writing
    to the same file concurrently).  The ``offset`` attribute remembers how
    many bytes have already been processed so each call resumes where the
    last one left off.

    Attributes
    ----------
    path       : absolute path to the .vrsf file
    stream_id  : VRSF stream identifier (1/2/3/4)
    session_id : numeric session ID from manifest.json
    rec_size   : fixed record size in bytes (None for variable-length streams)
    variable   : True for stream 4 (events), False for streams 1–3
    offset     : byte position of the next unread byte in the file
    missing    : count of out-of-order or missing sequence numbers detected
    last_seq   : last sequence number seen (for gap detection, not yet used)
    """

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
        """Read the next complete chunk from the file if enough bytes are available.

        Algorithm
        ---------
        1. Check that the file exists and has grown past the current offset
           by at least HEADER_SIZE bytes; otherwise return (None, None).
        2. Read and validate the 40-byte header:
           - Verify the 4-byte magic "VRSF".
           - Zero out the two CRC fields in a local copy and compute CRC32;
             compare against the stored HeaderCRC32.  This detects partial
             writes or file corruption.
        3. Check that the full payload (PayloadBytes) has been written.
        4. Read the payload and verify PayloadCRC32.
        5. Parse the payload into records:
           - Fixed-size streams (1–3): slice into rec_size-byte chunks.
           - Variable-size stream (4): parse framed records
             (seq u32 + unity_t f32 + json_len u32 + JSON UTF-8 bytes).
        6. Advance self.offset and return (recv_ts_ns, list_of_records).

        Returns
        -------
        (recv_ts_ns, records)
            recv_ts_ns : monotonic wall-clock nanoseconds at the moment of
                         reading (used as the database receipt timestamp).
            records    : for streams 1-3 a list of raw bytes objects, each
                         rec_size bytes long; for stream 4 a list of
                         (seq, unity_t, json_str) tuples.
        (None, None)
            When there is nothing new to process (file absent, incomplete
            header, incomplete payload) or a non-recoverable corruption is
            detected (bad magic/CRC — the offset is advanced by 1 byte so
            the next call tries to re-sync).
        """
        if not os.path.exists(self.path):
            return None, None
        size = os.path.getsize(self.path)
        if size - self.offset < HEADER_SIZE:
            # Not enough bytes for a header yet — Unity is still writing.
            return None, None
        with open(self.path, 'rb') as f:
            f.seek(self.offset)
            hdr = f.read(HEADER_SIZE)
            if len(hdr) < HEADER_SIZE:
                return None, None

            # ── Header field extraction ───────────────────────────────────
            magic = hdr[0:4]
            if magic != b'VRSF':
                # Unexpected byte at current offset; advance 1 byte and retry.
                LOG.warning("Bad magic at offset %d in %s", self.offset, self.path)
                self.offset += 1
                return None, None

            # Read payload size from header offset 24 (see layout table above).
            payload_bytes = read_u32_le(hdr, 24)
            header_crc = read_u32_le(hdr, 28)   # stored CRC of the header
            payload_crc = read_u32_le(hdr, 32)  # stored CRC of the payload

            # ── Wait for full payload ─────────────────────────────────────
            if size - self.offset < HEADER_SIZE + payload_bytes:
                # Payload not fully written yet; come back on the next tick.
                return None, None

            # ── Header CRC verification ───────────────────────────────────
            # To verify: zero out bytes 28-35 (HeaderCRC32 + PayloadCRC32)
            # in a copy of the header, then CRC the 40-byte copy.
            # The writer uses the same convention: those fields are zeroed
            # before computing the header CRC.
            hdr_copy = bytearray(hdr)
            for i in range(28, 36):
                hdr_copy[i] = 0
            if crc32(hdr_copy) != header_crc:
                LOG.warning("Header CRC mismatch at offset %d in %s", self.offset, self.path)
                self.offset += 1  # attempt byte-level re-sync
                return None, None

            # ── Payload CRC verification ──────────────────────────────────
            payload = f.read(payload_bytes)
            if crc32(payload) != payload_crc:
                LOG.warning("Payload CRC mismatch at offset %d in %s", self.offset, self.path)
                # Skip the entire faulty chunk so we can continue with the next.
                self.offset += HEADER_SIZE + payload_bytes
                return None, None

            # ── Parse payload into individual records ─────────────────────
            recv_ts_ns = time.monotonic_ns()  # timestamp of receipt (not Unity time)
            parsed = []
            off = 0
            if not self.variable:
                # Fixed-size records: slice into rec_size-byte chunks.
                rec_size = self.rec_size
                while off + rec_size <= len(payload):
                    rec = payload[off:off+rec_size]
                    parsed.append(rec)
                    off += rec_size
            else:
                # Variable-length event records (stream 4):
                # Each record is: [seq: u32][unity_t: f32][json_len: u32][json: utf-8 × json_len]
                while off + 12 <= len(payload):
                    seq = struct.unpack_from('<I', payload, off)[0]
                    unity_t = struct.unpack_from('<f', payload, off+4)[0]
                    jlen = struct.unpack_from('<I', payload, off+8)[0]
                    if off + 12 + jlen > len(payload):
                        break  # truncated JSON — stop here; retry on next call
                    js = payload[off+12:off+12+jlen].decode('utf8', errors='replace')
                    parsed.append((seq, unity_t, js))
                    off += 12 + jlen

            # Advance cursor past this chunk so the next call starts after it.
            self.offset += HEADER_SIZE + payload_bytes
            return recv_ts_ns, parsed


# ── Database initialisation ───────────────────────────────────────────────────

def init_db(path):
    """Create (or open) the SQLite database and ensure all tables exist.

    Schema
    ------
    sessions   – one row per Unity recording session (populated by manifest)
    headpose   – VR headset position + quaternion at ~90 Hz
    bike       – speed, steering, and brake states at ~50 Hz
    hr         – heart-rate BPM at ~1 Hz
    events     – arbitrary JSON event strings (triggers, laps, etc.)

    All tables are indexed on (session_id, recv_ts_ns) to support efficient
    time-range queries grouped by session.

    SQLite pragmas used
    -------------------
    WAL             : Write-Ahead Logging — readers never block writers and
                      vice versa, which is important because Unity writes while
                      this collector reads.
    synchronous=NORMAL : fsync only at WAL checkpoints, not on every commit.
    temp_store=MEMORY  : keep temp tables in RAM for faster query execution.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    cur = conn.cursor()
    cur.execute('PRAGMA journal_mode=WAL;')
    cur.execute('PRAGMA synchronous=NORMAL;')
    cur.execute('PRAGMA temp_store=MEMORY;')
    cur.execute('PRAGMA cache_size=-8000;')   # ~8 MB page cache (negative = KiB)
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


# ── Record insertion helpers ──────────────────────────────────────────────────

def insert_records_batch(conn, stream_id, session_id, recv_ts_ns, recs):
    """Unpack binary records for a fixed-size stream and bulk-insert them.

    Uses ``executemany`` with a list of tuples for efficiency — a single
    round-trip to SQLite instead of one INSERT per record.

    Stream 1 – headpose (36 bytes per record)
    -----------------------------------------
      Offset  Size  Column
        0      4    seq       (uint32 LE)  — record sequence number
        4      4    unity_t   (float32 LE) — Unity Time.time in seconds
        8      4    px        (float32 LE) — head position X (metres)
       12      4    py        (float32 LE) — head position Y
       16      4    pz        (float32 LE) — head position Z
       20      4    qx        (float32 LE) — rotation quaternion X
       24      4    qy        (float32 LE) — rotation quaternion Y
       28      4    qz        (float32 LE) — rotation quaternion Z
       32      4    qw        (float32 LE) — rotation quaternion W (scalar)

    Stream 2 – bike (20 bytes per record)
    --------------------------------------
      Offset  Size  Column
        0      4    seq       (uint32 LE)
        4      4    unity_t   (float32 LE)
        8      4    speed     (float32 LE) — km/h
       12      4    steering  (float32 LE) — normalised −1…+1
       16      1    brake_front (uint8)    — 0 or 1
       17      1    brake_rear  (uint8)    — 0 or 1
       18      2    (padding)

    Stream 3 – hr (12 bytes per record)
    ------------------------------------
      Offset  Size  Column
        0      4    seq       (uint32 LE)
        4      4    unity_t   (float32 LE)
        8      4    hr_bpm    (float32 LE) — beats per minute

    Parameters
    ----------
    conn        : open sqlite3.Connection (caller manages commit)
    stream_id   : 1, 2, or 3
    session_id  : integer session identifier
    recv_ts_ns  : monotonic receipt timestamp (nanoseconds)
    recs        : list of bytes objects, each rec_size bytes long

    Returns
    -------
    int : number of rows inserted (0 if stream_id not handled here)
    """
    cur = conn.cursor()
    if not recs:
        return 0
    if stream_id == 1:
        # headpose: u32(4) + 8×f32(32) = 36 bytes → format '<Iffffffff'
        rows = [
            (session_id, recv_ts_ns, seq, ut, px, py, pz, qx, qy, qz, qw)
            for seq, ut, px, py, pz, qx, qy, qz, qw
            in (struct.unpack_from('<Iffffffff', rec) for rec in recs)
        ]
        cur.executemany(
            'INSERT INTO headpose'
            '(session_id, recv_ts_ns, seq, unity_t, px,py,pz,qx,qy,qz,qw)'
            ' VALUES(?,?,?,?,?,?,?,?,?,?,?)', rows)
        return len(rows)
    elif stream_id == 2:
        # bike: '<Ifff' = u32 + 3×f32 = 16 bytes; brake bytes at [16] and [17]
        rows = [
            (session_id, recv_ts_ns, seq, ut, speed, steering, rec[16], rec[17])
            for (seq, ut, speed, steering), rec
            in ((struct.unpack_from('<Ifff', rec), rec) for rec in recs)
        ]
        cur.executemany(
            'INSERT INTO bike'
            '(session_id, recv_ts_ns, seq, unity_t, speed, steering, brake_front, brake_rear)'
            ' VALUES(?,?,?,?,?,?,?,?)', rows)
        return len(rows)
    elif stream_id == 3:
        # hr: '<Iff' = u32 + 2×f32 = 12 bytes
        rows = [
            (session_id, recv_ts_ns, seq, ut, hr_bpm)
            for seq, ut, hr_bpm
            in (struct.unpack_from('<Iff', rec) for rec in recs)
        ]
        cur.executemany('INSERT INTO hr(session_id, recv_ts_ns, seq, unity_t, hr_bpm) VALUES(?,?,?,?,?)', rows)
        return len(rows)
    # stream_id 4 is handled by insert_events_batch
    return 0


def insert_events_batch(conn, session_id, recv_ts_ns, rec_tuples):
    """Bulk-insert pre-parsed event records (stream 4) into the events table.

    Parameters
    ----------
    conn        : open sqlite3.Connection
    session_id  : integer session identifier
    recv_ts_ns  : monotonic receipt timestamp (nanoseconds)
    rec_tuples  : list of (seq: int, unity_t: float, json_str: str) tuples
                  as produced by FileTail.tail_once() for variable streams

    Returns
    -------
    int : number of rows inserted
    """
    if not rec_tuples:
        return 0
    rows = [(session_id, recv_ts_ns, seq, unity_t, js) for (seq, unity_t, js) in rec_tuples]
    cur = conn.cursor()
    cur.executemany('INSERT INTO events(session_id, recv_ts_ns, seq, unity_t, json) VALUES(?,?,?,?,?)', rows)
    return len(rows)


# ── Parquet buffering (optional, requires pyarrow) ────────────────────────────

# Module-level buffers shared across tails.  Keyed by (session_id, stream_id).
PARQUET_BUFFERS: DefaultDict[Tuple[int, int], List[Dict[str, object]]] = defaultdict(list)
# Monotonically increasing part index per (session_id, stream_id) — ensures
# no two parts overwrite each other even across multiple process restarts.
PARQUET_PART_COUNTER: DefaultDict[Tuple[int, int], int] = defaultdict(int)
# Lock to protect both dicts when flushed from a background thread.
PARQUET_LOCK = threading.Lock()


def flush_parquet_parts(out_dir, part_rows=10000):
    """Drain the in-memory Parquet buffers and write part files to *out_dir*.

    Called from the main watch loop roughly every second.  Each
    (session_id, stream_id) pair gets its own sub-directory and a series of
    numbered part files — ``stream{id}_part_{N}.parquet``.

    Parameters
    ----------
    out_dir   : directory where per-session sub-directories are created
    part_rows : maximum number of rows to write per Parquet file; rows that
                don't fill a complete part remain in the buffer until the
                next flush.
    """
    if not HAVE_PYARROW:
        return
    with PARQUET_LOCK:
        items = list(PARQUET_BUFFERS.items())
        for (sid, stream_id), rows in items:
            if not rows:
                continue
            part_dir = os.path.join(out_dir, f'session_{sid}_parquet')
            os.makedirs(part_dir, exist_ok=True)
            # Write in chunks of part_rows; any remainder stays in the buffer.
            while rows:
                chunk = rows[:part_rows]
                part_idx = PARQUET_PART_COUNTER[(sid, stream_id)]
                part_file = os.path.join(part_dir, f'stream{stream_id}_part_{part_idx}.parquet')
                try:
                    # pa.Table.from_pylist converts a list of dicts to a columnar table.
                    table = pa.Table.from_pylist(chunk)
                    pq.write_table(table, part_file)
                    PARQUET_PART_COUNTER[(sid, stream_id)] += 1
                    LOG.info("Wrote parquet part: %s (%d rows)", part_file, len(chunk))
                except Exception as e:
                    LOG.warning("Parquet write error: %s", e)
                    break  # leave remaining rows in the buffer for next attempt
                rows = rows[part_rows:]
            PARQUET_BUFFERS[(sid, stream_id)] = []


# ── Main watch loop ───────────────────────────────────────────────────────────

def watch_sessions(
    logs_root: str,
    out_db: str,
    out_parquet_dir: Optional[str] = None,
    stop_event: Optional[threading.Event] = None,
    sqlite_batch_size: int = 0,
    parquet_rows: int = 10000,
):
    """Watch *logs_root* for new sessions and tail their VRSF files forever.

    Main loop (runs every 100 ms)
    ------------------------------
    1. ``scan_once()`` – look for new ``session_*/manifest.json`` directories
       that have not been seen yet and create four ``FileTail`` objects for
       them (headpose, bike, hr, events).
    2. For every existing tail: call ``tail_once()`` which reads the next
       complete VRSF chunk (if available) and returns parsed records.
    3. Insert parsed records into SQLite via ``insert_records_batch`` /
       ``insert_events_batch``.
    4. Optionally append the same records to the in-memory Parquet buffer.
    5. Commit strategy:
       - ``sqlite_batch_size == 0`` (default): commit immediately after every
         chunk — lowest latency, each chunk is atomic.
       - ``sqlite_batch_size > 0``: accumulate ``pending_inserts`` across
         chunks and commit only when the threshold is reached — higher
         throughput for very high-Hz streams.
    6. Print per-stream insertion counts once per second as a heartbeat.
    7. Flush Parquet buffers to disk once per second.

    Parameters
    ----------
    logs_root        : root directory containing ``session_*`` sub-directories
    out_db           : path to the SQLite output file
    out_parquet_dir  : if given, also write Parquet part files here
    stop_event       : optional ``threading.Event``; set it to stop the loop
                       cleanly (used by tests / GUI shutdown)
    sqlite_batch_size: see commit strategy above
    parquet_rows     : maximum rows per Parquet part file
    """
    conn = init_db(out_db)
    seen = set()   # set of session directories already registered
    tails = []     # flat list of all active FileTail objects

    def scan_once():
        """Discover any new session directories and create FileTail objects."""
        for d in sorted(glob.glob(os.path.join(logs_root, 'session_*'))):
            if d in seen:
                continue
            manifest = os.path.join(d, 'manifest.json')
            if not os.path.exists(manifest):
                continue  # session may still be initialising
            with open(manifest, 'r') as f:
                m = json.load(f)
            sid = m.get('session_id')
            # One FileTail per stream file in this session directory.
            tails.append(FileTail(os.path.join(d, 'headpose.vrsf'), 1, sid, rec_size=36, variable=False))
            tails.append(FileTail(os.path.join(d, 'bike.vrsf'),    2, sid, rec_size=20, variable=False))
            tails.append(FileTail(os.path.join(d, 'hr.vrsf'),      3, sid, rec_size=12, variable=False))
            tails.append(FileTail(os.path.join(d, 'events.vrsf'),  4, sid, variable=True))
            seen.add(d)

    LOG.info("Collector: watching %s", logs_root)
    last_print        = time.time()
    last_parquet_flush = time.time()
    counts: DefaultDict[int, int] = defaultdict(int)  # per-stream insert counter (reset every second)
    pending_inserts = 0  # rows inserted since last commit (used with sqlite_batch_size > 0)

    while True:
        if stop_event and stop_event.is_set():
            break

        scan_once()

        for t in list(tails):
            recv_ts_ns, parsed = t.tail_once()
            if parsed is None:
                continue
            sid = t.session_id
            inserted = 0

            # ── SQLite insert ─────────────────────────────────────────────
            try:
                if t.stream_id == 4:
                    inserted = insert_events_batch(conn, sid, recv_ts_ns, parsed)
                else:
                    inserted = insert_records_batch(conn, t.stream_id, sid, recv_ts_ns, parsed)
            except Exception as e:
                LOG.error("DB insert error: %s", e)
                inserted = 0

            if inserted:
                counts[t.stream_id] += inserted
                pending_inserts    += inserted

                # ── Parquet buffering ─────────────────────────────────────
                if out_parquet_dir and HAVE_PYARROW:
                    key = (sid, t.stream_id)
                    if t.stream_id == 1:
                        for rec in parsed:
                            seq     = struct.unpack_from('<I', rec, 0)[0]
                            unity_t = struct.unpack_from('<f', rec, 4)[0]
                            px      = struct.unpack_from('<f', rec, 8)[0]
                            py      = struct.unpack_from('<f', rec, 12)[0]
                            pz      = struct.unpack_from('<f', rec, 16)[0]
                            qx      = struct.unpack_from('<f', rec, 20)[0]
                            qy      = struct.unpack_from('<f', rec, 24)[0]
                            qz      = struct.unpack_from('<f', rec, 28)[0]
                            qw      = struct.unpack_from('<f', rec, 32)[0]
                            PARQUET_BUFFERS[key].append({
                                'session_id': sid, 'recv_ts_ns': recv_ts_ns, 'seq': seq,
                                'unity_t': unity_t, 'px': px, 'py': py, 'pz': pz,
                                'qx': qx, 'qy': qy, 'qz': qz, 'qw': qw,
                            })
                    elif t.stream_id == 2:
                        for rec in parsed:
                            seq      = struct.unpack_from('<I', rec, 0)[0]
                            unity_t  = struct.unpack_from('<f', rec, 4)[0]
                            speed    = struct.unpack_from('<f', rec, 8)[0]
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
                            seq     = struct.unpack_from('<I', rec, 0)[0]
                            unity_t = struct.unpack_from('<f', rec, 4)[0]
                            hr_bpm  = struct.unpack_from('<f', rec, 8)[0]
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

            # ── Commit strategy ───────────────────────────────────────────
            if sqlite_batch_size <= 0:
                # Default: commit every chunk for lowest latency.
                try:
                    conn.commit()
                    pending_inserts = 0
                except Exception:
                    pass
            else:
                # Batched: commit only when accumulated rows exceed threshold.
                if pending_inserts >= sqlite_batch_size:
                    try:
                        conn.commit()
                        pending_inserts = 0
                    except Exception:
                        pass

        # ── Heartbeat print (once per second) ─────────────────────────────
        if time.time() - last_print >= 1.0:
            LOG.debug("rates head=%d bike=%d hr=%d events=%d",
                      counts[1], counts[2], counts[3], counts[4])
            counts = defaultdict(int)  # reset counters for the next second
            last_print = time.time()

        # ── Parquet flush (once per second) ───────────────────────────────
        if out_parquet_dir and HAVE_PYARROW and (time.time() - last_parquet_flush) >= 1.0:
            flush_parquet_parts(out_parquet_dir, part_rows=parquet_rows)
            last_parquet_flush = time.time()

        time.sleep(0.1)  # poll interval — 100 ms keeps CPU usage negligible

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    # Commit any rows that were buffered but not yet written (only possible
    # when sqlite_batch_size > 0), then flush remaining Parquet data.
    try:
        if pending_inserts > 0:
            conn.commit()
    except Exception:
        pass
    if out_parquet_dir and HAVE_PYARROW:
        flush_parquet_parts(out_parquet_dir, part_rows=parquet_rows)


def main():
    """Entry point: parse CLI arguments and start the collector watch loop."""
    # Resolve sensible defaults relative to the repo root (3 levels up from this file:
    # UnityIntegration/python/collector_tail.py → repo root).
    _here = Path(__file__).resolve().parent
    _repo_root = _here.parent.parent   # UnityIntegration/python → UnityIntegration → repo root
    _default_logs = str(_repo_root / "Logs")
    _default_out  = str(_repo_root / "collector_out" / "vrs.sqlite")

    p = argparse.ArgumentParser(description='Tail VRSF session logs into SQLite/Parquet.')
    p.add_argument('--logs', default=_default_logs,
                   help='Root directory containing session_* subdirectories')
    p.add_argument('--out', default=_default_out,
                   help='Path for the output SQLite database file')
    p.add_argument('--sqlite-batch-size', type=int, default=0,
                   help='If >0, commit DB every N records; 0 means commit per chunk (default)')
    p.add_argument('--parquet-rows', type=int, default=10000,
                   help='Maximum rows per Parquet part file (default: 10000)')
    p.add_argument('--verbose', action='store_true',
                   help='Enable DEBUG logging (default: INFO)')
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    # Pass the same directory as the Parquet output so parts sit next to the DB.
    watch_sessions(args.logs, args.out, out_parquet_dir=out_dir,
                   sqlite_batch_size=args.sqlite_batch_size, parquet_rows=args.parquet_rows)


if __name__ == '__main__':
    main()
