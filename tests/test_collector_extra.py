"""
test_collector_extra.py
=======================
Additional tests for collector_tail.py covering paths not yet exercised:

  • Low-level binary helpers: crc32, read_u32_le, read_u64_le
  • insert_records_batch — streams 1 (headpose), 2 (bike), 3 (hr), unknown
  • flush_parquet_parts — no-op when HAVE_PYARROW=False, split logic
  • FileTail.__init__ attribute defaults
  • VRSF header field parsing (version, stream_id, session_id, etc.)
  • Empty payload chunk (payload_bytes=0, record_count=0)
  • Multiple sessions both processed by watch_sessions
  • Session already in 'seen' not re-processed
  • Parquet output path in watch_sessions
  • Batch-commit mode (sqlite_batch_size > 0)
"""

from __future__ import annotations

import json
import os
import sqlite3
import struct
import threading
import time
import zlib
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Resolve the collector module — either as an importable package or by
# appending to sys.path (conftest.py already handles this in the repo).
# ---------------------------------------------------------------------------
import sys as _sys

_repo = Path(__file__).resolve().parent.parent
if str(_repo) not in _sys.path:
    _sys.path.insert(0, str(_repo))

import bridge.collector_tail as ct

# ── VRSF chunk builder ────────────────────────────────────────────────────────

def _make_vrsf_chunk(stream_id: int, session_id: int, chunk_seq: int,
                     payload: bytes, version: int = 1, flags: int = 0) -> bytes:
    """Build a valid 40-byte VRSF header + *payload* with correct CRCs.

    Header layout (40 bytes):
      0   4  magic      "VRSF"
      4   1  version    uint8
      5   1  stream_id  uint8
      6   2  flags      uint16
      8   8  session_id uint64
     16   4  chunk_seq  uint32
     20   4  record_count uint32
     24   4  payload_bytes uint32
     28   4  header_crc  uint32  (computed with this field zeroed)
     32   4  payload_crc uint32
     36   4  reserved    uint32
    """
    payload_bytes = len(payload)
    payload_crc = zlib.crc32(payload) & 0xFFFFFFFF
    record_count = 0

    # fmt: "<4sBBHQIIIIII" = 4+1+1+2+8+4+4+4+4+4+4 = 40 bytes
    # Pack with header_crc=0 first to compute the real CRC
    hdr = struct.pack(
        "<4sBBHQIIIIII",
        b"VRSF",
        version,
        stream_id,
        flags,
        session_id,
        chunk_seq,
        record_count,
        payload_bytes,
        0,            # HeaderCRC32 placeholder
        payload_crc,
        0,            # Reserved
    )
    assert len(hdr) == 40, f"Expected 40-byte header, got {len(hdr)}"

    # Zero out bytes 28–35 (HeaderCRC32 + PayloadCRC32) before computing CRC
    hdr_copy = bytearray(hdr)
    for i in range(28, 36):
        hdr_copy[i] = 0
    header_crc = zlib.crc32(bytes(hdr_copy)) & 0xFFFFFFFF

    # Re-pack with the real header CRC
    hdr = struct.pack(
        "<4sBBHQIIIIII",
        b"VRSF",
        version,
        stream_id,
        flags,
        session_id,
        chunk_seq,
        record_count,
        payload_bytes,
        header_crc,
        payload_crc,
        0,            # Reserved
    )
    assert len(hdr) == 40
    return hdr + payload


def _make_headpose_rec(seq=0, unity_t=0.0,
                       px=0.0, py=0.0, pz=0.0,
                       qx=0.0, qy=0.0, qz=0.0, qw=1.0) -> bytes:
    return struct.pack("<Iffffffff", seq, unity_t, px, py, pz, qx, qy, qz, qw)


def _make_bike_rec(seq=0, unity_t=0.0, speed=0.0, steering=0.0,
                   brake_front=0, brake_rear=0) -> bytes:
    base = struct.pack("<Ifff", seq, unity_t, speed, steering)
    return base + bytes([brake_front, brake_rear, 0, 0])


def _make_hr_rec(seq=0, unity_t=0.0, hr_bpm=0.0) -> bytes:
    return struct.pack("<Iff", seq, unity_t, hr_bpm)


def _make_session(parent: Path, session_id: int, started_ms: int = 0) -> Path:
    d = parent / f"session_{session_id}"
    d.mkdir()
    with open(d / "manifest.json", "w") as f:
        json.dump({"session_id": session_id, "started_unix_ms": started_ms}, f)
    return d


# ─────────────────────────────────────────────────────────────────────────────
# Low-level binary helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestBinaryHelpers:

    def test_crc32_empty_bytes_is_zero(self):
        """CRC32 of empty input must be 0."""
        assert ct.crc32(b"") == 0

    def test_crc32_known_vector(self):
        """CRC32 of b'123456789' is the well-known test vector 0xCBF43926."""
        assert ct.crc32(b"123456789") == 0xCBF43926

    def test_crc32_result_fits_in_uint32(self):
        """Result is always in [0, 2**32 - 1]."""
        for data in (b"hello", b"\xff" * 100, b"\x00" * 8):
            result = ct.crc32(data)
            assert 0 <= result <= 0xFFFFFFFF

    def test_crc32_is_deterministic(self):
        data = b"wahoo_ble_sniffer"
        assert ct.crc32(data) == ct.crc32(data)

    def test_read_u32_le_little_endian(self):
        b = struct.pack("<I", 0xDEADBEEF)
        assert ct.read_u32_le(b, 0) == 0xDEADBEEF

    def test_read_u32_le_at_offset(self):
        b = b"\x00\x00" + struct.pack("<I", 42) + b"\x00"
        assert ct.read_u32_le(b, 2) == 42

    def test_read_u32_le_min_max(self):
        assert ct.read_u32_le(struct.pack("<I", 0), 0) == 0
        assert ct.read_u32_le(struct.pack("<I", 0xFFFFFFFF), 0) == 0xFFFFFFFF

    def test_read_u64_le_little_endian(self):
        val = 0x0102030405060708
        b = struct.pack("<Q", val)
        assert ct.read_u64_le(b, 0) == val

    def test_read_u64_le_at_offset(self):
        val = 12345678901234567
        b = b"\xAB\xCD" + struct.pack("<Q", val)
        assert ct.read_u64_le(b, 2) == val

    def test_read_u64_le_zero(self):
        b = struct.pack("<Q", 0)
        assert ct.read_u64_le(b, 0) == 0


# ─────────────────────────────────────────────────────────────────────────────
# FileTail.__init__ attribute defaults
# ─────────────────────────────────────────────────────────────────────────────

class TestFileTailInit:

    def test_offset_starts_at_zero(self):
        ft = ct.FileTail("/tmp/fake.vrsf", 1, 99, rec_size=36)
        assert ft.offset == 0

    def test_missing_starts_at_zero(self):
        ft = ct.FileTail("/tmp/fake.vrsf", 1, 99, rec_size=36)
        assert ft.missing == 0

    def test_last_seq_starts_none(self):
        ft = ct.FileTail("/tmp/fake.vrsf", 1, 99, rec_size=36)
        assert ft.last_seq is None

    def test_attributes_stored_correctly(self):
        ft = ct.FileTail("/path/to/file.vrsf", 3, 7, rec_size=12, variable=False)
        assert ft.path == "/path/to/file.vrsf"
        assert ft.stream_id == 3
        assert ft.session_id == 7
        assert ft.rec_size == 12
        assert ft.variable is False

    def test_variable_false_by_default(self):
        ft = ct.FileTail("/tmp/x.vrsf", 2, 1)
        assert ft.variable is False

    def test_rec_size_none_by_default(self):
        ft = ct.FileTail("/tmp/x.vrsf", 4, 1)
        assert ft.rec_size is None

    def test_variable_true_for_events_stream(self):
        ft = ct.FileTail("/tmp/events.vrsf", 4, 1, variable=True)
        assert ft.variable is True


# ─────────────────────────────────────────────────────────────────────────────
# insert_records_batch — per-stream correctness
# ─────────────────────────────────────────────────────────────────────────────

def _fresh_db(tmp_path) -> sqlite3.Connection:
    db = str(tmp_path / "test.sqlite")
    return ct.init_db(db)


class TestInsertRecordsBatch:

    def test_empty_recs_returns_zero(self, tmp_path):
        conn = _fresh_db(tmp_path)
        n = ct.insert_records_batch(conn, 1, 1, 1000, [])
        assert n == 0

    def test_unknown_stream_id_returns_zero(self, tmp_path):
        """Stream IDs not handled (0, 5, 99) must return 0 without error."""
        conn = _fresh_db(tmp_path)
        rec = b"\x00" * 36
        for sid in (0, 5, 99, 255):
            n = ct.insert_records_batch(conn, sid, 1, 1000, [rec])
            assert n == 0, f"Expected 0 for stream_id={sid}, got {n}"

    # ── Stream 1 – headpose ──────────────────────────────────────────────────

    def test_stream1_single_record_inserted(self, tmp_path):
        conn = _fresh_db(tmp_path)
        rec = _make_headpose_rec(seq=7, unity_t=1.5,
                                  px=1.0, py=2.0, pz=3.0,
                                  qx=0.0, qy=0.0, qz=0.0, qw=1.0)
        n = ct.insert_records_batch(conn, 1, 42, 9_000_000_000, [rec])
        conn.commit()
        assert n == 1
        row = conn.execute("SELECT * FROM headpose").fetchone()
        assert row is not None
        sid, ts_ns, seq, unity_t, px, py, pz, qx, qy, qz, qw = row
        assert sid    == 42
        assert ts_ns  == 9_000_000_000
        assert seq    == 7
        assert abs(unity_t - 1.5) < 0.001
        assert abs(px - 1.0) < 0.001
        assert abs(qw - 1.0) < 0.001

    def test_stream1_multiple_records(self, tmp_path):
        conn = _fresh_db(tmp_path)
        recs = [_make_headpose_rec(seq=i) for i in range(5)]
        n = ct.insert_records_batch(conn, 1, 1, 0, recs)
        conn.commit()
        assert n == 5
        assert conn.execute("SELECT COUNT(*) FROM headpose").fetchone()[0] == 5

    def test_stream1_quaternion_values_preserved(self, tmp_path):
        """Quaternion components must round-trip through struct pack/unpack."""
        conn = _fresh_db(tmp_path)
        qx, qy, qz, qw = 0.5, -0.5, 0.5, 0.5
        rec = _make_headpose_rec(qx=qx, qy=qy, qz=qz, qw=qw)
        ct.insert_records_batch(conn, 1, 1, 0, [rec])
        conn.commit()
        row = conn.execute("SELECT qx,qy,qz,qw FROM headpose").fetchone()
        assert abs(row[0] - qx) < 0.001
        assert abs(row[1] - qy) < 0.001
        assert abs(row[2] - qz) < 0.001
        assert abs(row[3] - qw) < 0.001

    # ── Stream 2 – bike ──────────────────────────────────────────────────────

    def test_stream2_single_record_inserted(self, tmp_path):
        conn = _fresh_db(tmp_path)
        rec = _make_bike_rec(seq=3, unity_t=0.1, speed=25.5, steering=-0.3,
                             brake_front=1, brake_rear=0)
        n = ct.insert_records_batch(conn, 2, 10, 5_000, [rec])
        conn.commit()
        assert n == 1
        row = conn.execute("SELECT seq,speed,steering,brake_front,brake_rear FROM bike").fetchone()
        seq, speed, steering, bf, br = row
        assert seq == 3
        assert abs(speed - 25.5) < 0.01
        assert abs(steering - (-0.3)) < 0.01
        assert bf == 1
        assert br == 0

    def test_stream2_brake_flags_both_set(self, tmp_path):
        conn = _fresh_db(tmp_path)
        rec = _make_bike_rec(brake_front=1, brake_rear=1)
        ct.insert_records_batch(conn, 2, 1, 0, [rec])
        conn.commit()
        row = conn.execute("SELECT brake_front, brake_rear FROM bike").fetchone()
        assert row == (1, 1)

    def test_stream2_brake_flags_both_clear(self, tmp_path):
        conn = _fresh_db(tmp_path)
        rec = _make_bike_rec(brake_front=0, brake_rear=0)
        ct.insert_records_batch(conn, 2, 1, 0, [rec])
        conn.commit()
        row = conn.execute("SELECT brake_front, brake_rear FROM bike").fetchone()
        assert row == (0, 0)

    def test_stream2_multiple_records(self, tmp_path):
        conn = _fresh_db(tmp_path)
        recs = [_make_bike_rec(seq=i, speed=float(i)) for i in range(10)]
        n = ct.insert_records_batch(conn, 2, 1, 0, recs)
        conn.commit()
        assert n == 10

    # ── Stream 3 – HR ────────────────────────────────────────────────────────

    def test_stream3_hr_bpm_round_trip(self, tmp_path):
        conn = _fresh_db(tmp_path)
        rec = _make_hr_rec(seq=1, unity_t=0.5, hr_bpm=155.0)
        n = ct.insert_records_batch(conn, 3, 5, 0, [rec])
        conn.commit()
        assert n == 1
        row = conn.execute("SELECT seq, hr_bpm FROM hr").fetchone()
        assert row[0] == 1
        assert abs(row[1] - 155.0) < 0.5   # f32 rounding tolerance

    def test_stream3_low_hr_value(self, tmp_path):
        conn = _fresh_db(tmp_path)
        rec = _make_hr_rec(hr_bpm=35.0)
        ct.insert_records_batch(conn, 3, 1, 0, [rec])
        conn.commit()
        hr_bpm = conn.execute("SELECT hr_bpm FROM hr").fetchone()[0]
        assert abs(hr_bpm - 35.0) < 0.5

    def test_stream3_multiple_records(self, tmp_path):
        conn = _fresh_db(tmp_path)
        recs = [_make_hr_rec(seq=i, hr_bpm=float(60 + i)) for i in range(6)]
        n = ct.insert_records_batch(conn, 3, 1, 0, recs)
        conn.commit()
        assert n == 6
        rows = conn.execute("SELECT hr_bpm FROM hr ORDER BY seq").fetchall()
        for i, (bpm,) in enumerate(rows):
            assert abs(bpm - (60.0 + i)) < 0.5


# ─────────────────────────────────────────────────────────────────────────────
# FileTail — VRSF chunk reading
# ─────────────────────────────────────────────────────────────────────────────

class TestFileTailRead:

    def _write_chunk(self, path: Path, stream_id: int, session_id: int,
                     chunk_seq: int, payload: bytes):
        chunk = _make_vrsf_chunk(stream_id, session_id, chunk_seq, payload)
        with open(path, "ab") as f:
            f.write(chunk)

    def test_tail_once_returns_none_when_file_missing(self, tmp_path):
        ft = ct.FileTail(str(tmp_path / "does_not_exist.vrsf"), 1, 1, rec_size=36)
        ts, recs = ft.tail_once()
        assert ts is None
        assert recs is None

    def test_tail_once_returns_none_when_file_empty(self, tmp_path):
        p = tmp_path / "empty.vrsf"
        p.write_bytes(b"")
        ft = ct.FileTail(str(p), 1, 1, rec_size=36)
        ts, recs = ft.tail_once()
        assert ts is None
        assert recs is None

    def test_tail_once_empty_payload_chunk_returns_empty_list(self, tmp_path):
        """A chunk with 0-byte payload must return (ts, []) — not None, not error."""
        p = tmp_path / "stream.vrsf"
        self._write_chunk(p, 1, 99, 0, b"")
        ft = ct.FileTail(str(p), 1, 99, rec_size=36)
        ts, recs = ft.tail_once()
        assert ts is not None
        assert recs == []

    def test_tail_once_advances_offset(self, tmp_path):
        p = tmp_path / "hp.vrsf"
        payload = _make_headpose_rec(seq=0)
        self._write_chunk(p, 1, 1, 0, payload)
        ft = ct.FileTail(str(p), 1, 1, rec_size=36)
        assert ft.offset == 0
        ts, recs = ft.tail_once()
        assert ft.offset == 40 + len(payload)

    def test_tail_once_reads_correct_number_of_records(self, tmp_path):
        p = tmp_path / "hp.vrsf"
        payload = b"".join(_make_headpose_rec(seq=i) for i in range(3))
        self._write_chunk(p, 1, 1, 0, payload)
        ft = ct.FileTail(str(p), 1, 1, rec_size=36)
        ts, recs = ft.tail_once()
        assert len(recs) == 3

    def test_tail_once_two_sequential_chunks(self, tmp_path):
        """The tail must read chunk 1, then advance and read chunk 2."""
        p = tmp_path / "hp.vrsf"
        payload1 = _make_headpose_rec(seq=0)
        payload2 = _make_headpose_rec(seq=1)
        self._write_chunk(p, 1, 1, 0, payload1)
        self._write_chunk(p, 1, 1, 1, payload2)

        ft = ct.FileTail(str(p), 1, 1, rec_size=36)
        ts1, recs1 = ft.tail_once()
        ts2, recs2 = ft.tail_once()

        assert len(recs1) == 1
        assert len(recs2) == 1
        # After reading both chunks offset should equal file size
        assert ft.offset == os.path.getsize(str(p))

    def test_bad_magic_skips_one_byte(self, tmp_path):
        """A byte not matching 'VRSF' magic must advance offset by 1."""
        p = tmp_path / "bad.vrsf"
        p.write_bytes(b"\xFF" * 50)  # garbage bytes
        ft = ct.FileTail(str(p), 1, 1, rec_size=36)
        ft.tail_once()
        assert ft.offset == 1

    def test_variable_stream_events_parsed(self, tmp_path):
        """Stream 4 (events) uses variable-length records."""
        p = tmp_path / "events.vrsf"
        js = json.dumps({"evt": "lap", "i": 1})
        js_bytes = js.encode("utf-8")
        payload = struct.pack("<IIf", 0, len(js_bytes), 1.0) + js_bytes
        # Correct event frame: seq(u32), unity_t(f32), json_len(u32), json
        # Re-pack correctly: seq, unity_t, jlen
        payload = struct.pack("<IfI", 0, 1.0, len(js_bytes)) + js_bytes
        self._write_chunk(p, 4, 1, 0, payload)

        ft = ct.FileTail(str(p), 4, 1, variable=True)
        ts, recs = ft.tail_once()
        assert len(recs) == 1
        seq, unity_t, js_out = recs[0]
        assert seq == 0
        assert abs(unity_t - 1.0) < 0.001
        parsed = json.loads(js_out)
        assert parsed["evt"] == "lap"
        assert parsed["i"] == 1

    def test_header_crc_mismatch_skips_one_byte(self, tmp_path):
        """A corrupted header CRC must advance offset by 1 (re-sync)."""
        p = tmp_path / "corrupt.vrsf"
        chunk = bytearray(_make_vrsf_chunk(1, 1, 0, _make_headpose_rec()))
        # Corrupt the header CRC field (bytes 28-31)
        chunk[28] ^= 0xFF
        p.write_bytes(bytes(chunk))

        ft = ct.FileTail(str(p), 1, 1, rec_size=36)
        ts, recs = ft.tail_once()
        assert ts is None
        assert ft.offset == 1

    def test_payload_crc_mismatch_skips_whole_chunk(self, tmp_path):
        """A corrupted payload CRC must skip the full chunk."""
        p = tmp_path / "bad_payload.vrsf"
        chunk = bytearray(_make_vrsf_chunk(1, 1, 0, _make_headpose_rec()))
        # Corrupt the payload CRC field (bytes 32-35)
        chunk[32] ^= 0xFF
        p.write_bytes(bytes(chunk))

        ft = ct.FileTail(str(p), 1, 1, rec_size=36)
        ts, recs = ft.tail_once()
        assert ts is None
        assert ft.offset == 40 + 36  # skipped header + payload


# ─────────────────────────────────────────────────────────────────────────────
# flush_parquet_parts
# ─────────────────────────────────────────────────────────────────────────────

class TestFlushParquetParts:

    def setup_method(self):
        """Clear module-level Parquet buffers before each test."""
        ct.PARQUET_BUFFERS.clear()
        ct.PARQUET_PART_COUNTER.clear()

    def test_no_op_when_pyarrow_absent(self, tmp_path, monkeypatch):
        """flush_parquet_parts must silently return when HAVE_PYARROW is False."""
        monkeypatch.setattr(ct, "HAVE_PYARROW", False)
        ct.PARQUET_BUFFERS[(1, 1)].extend([{"x": i} for i in range(5)])
        ct.flush_parquet_parts(str(tmp_path))
        # Buffer must be untouched and no files written
        assert len(ct.PARQUET_BUFFERS[(1, 1)]) == 5
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.skipif(
        not __import__("importlib").util.find_spec("pyarrow"),
        reason="pyarrow not installed",
    )
    def test_single_part_written(self, tmp_path):
        rows = [{"session_id": 1, "recv_ts_ns": i, "hr_bpm": float(60 + i)} for i in range(5)]
        ct.PARQUET_BUFFERS[(1, 3)].extend(rows)
        ct.flush_parquet_parts(str(tmp_path), part_rows=10000)
        part_dir = tmp_path / "session_1_parquet"
        parts = list(part_dir.glob("stream3_part_*.parquet"))
        assert len(parts) == 1

    @pytest.mark.skipif(
        not __import__("importlib").util.find_spec("pyarrow"),
        reason="pyarrow not installed",
    )
    def test_buffer_cleared_after_flush(self, tmp_path):
        rows = [{"session_id": 1, "recv_ts_ns": i, "x": i} for i in range(3)]
        ct.PARQUET_BUFFERS[(1, 1)].extend(rows)
        ct.flush_parquet_parts(str(tmp_path), part_rows=1000)
        assert ct.PARQUET_BUFFERS[(1, 1)] == []

    @pytest.mark.skipif(
        not __import__("importlib").util.find_spec("pyarrow"),
        reason="pyarrow not installed",
    )
    def test_part_split_at_part_rows_boundary(self, tmp_path):
        """With part_rows=3 and 7 rows:
        flush writes chunks of 3 until exhausted → 3 parts (3+3+1 rows).
        The buffer is fully drained."""
        rows = [{"session_id": 2, "recv_ts_ns": i, "v": i} for i in range(7)]
        ct.PARQUET_BUFFERS[(2, 2)].extend(rows)
        ct.flush_parquet_parts(str(tmp_path), part_rows=3)
        part_dir = tmp_path / "session_2_parquet"
        parts = sorted(part_dir.glob("stream2_part_*.parquet"))
        # 3 parts: rows 0-2, 3-5, 6
        assert len(parts) == 3
        # Buffer fully drained
        assert ct.PARQUET_BUFFERS[(2, 2)] == []

    @pytest.mark.skipif(
        not __import__("importlib").util.find_spec("pyarrow"),
        reason="pyarrow not installed",
    )
    def test_partial_buffer_rows_still_flushed(self, tmp_path):
        """Even fewer rows than part_rows are flushed immediately —
        the implementation drains the buffer completely on each call."""
        rows = [{"session_id": 1, "recv_ts_ns": i, "v": i} for i in range(4)]
        ct.PARQUET_BUFFERS[(1, 1)].extend(rows)
        ct.flush_parquet_parts(str(tmp_path), part_rows=100)
        # One part file written with all 4 rows
        part_dir = tmp_path / "session_1_parquet"
        parts = list(part_dir.glob("stream1_part_*.parquet"))
        assert len(parts) == 1
        # Buffer now empty
        assert ct.PARQUET_BUFFERS[(1, 1)] == []


# ─────────────────────────────────────────────────────────────────────────────
# watch_sessions integration
# ─────────────────────────────────────────────────────────────────────────────

class TestWatchSessions:
    """Integration-level tests against a real in-temp-dir session tree."""

    def _write_session(self, logs_root: Path, session_id: int,
                       stream_recs: dict[int, list[bytes]],
                       event_recs: list[tuple] | None = None) -> Path:
        """Create a session directory and write VRSF stream files into it."""
        session_dir = _make_session(logs_root, session_id)
        rec_sizes = {1: 36, 2: 20, 3: 12}
        stream_files = {1: "headpose.vrsf", 2: "bike.vrsf", 3: "hr.vrsf"}

        for sid, recs in stream_recs.items():
            if not recs:
                continue
            payload = b"".join(recs)
            chunk = _make_vrsf_chunk(sid, session_id, 0, payload)
            with open(session_dir / stream_files[sid], "wb") as f:
                f.write(chunk)

        if event_recs is not None:
            payload = b""
            for seq, unity_t, js in event_recs:
                js_bytes = js.encode("utf-8")
                payload += struct.pack("<IfI", seq, unity_t, len(js_bytes)) + js_bytes
            chunk = _make_vrsf_chunk(4, session_id, 0, payload)
            with open(session_dir / "events.vrsf", "wb") as f:
                f.write(chunk)

        return session_dir

    def _run_once(self, logs_root: Path, db_path: Path,
                  out_parquet_dir: Path | None = None,
                  batch_size: int = 0):
        """Run watch_sessions for a short burst via a stop_event."""
        stop = threading.Event()

        def _stopper():
            time.sleep(0.4)
            stop.set()

        threading.Thread(target=_stopper, daemon=True).start()
        ct.watch_sessions(
            str(logs_root),
            str(db_path),
            out_parquet_dir=str(out_parquet_dir) if out_parquet_dir else None,
            stop_event=stop,
            sqlite_batch_size=batch_size,
        )

    def test_single_headpose_session_inserted(self, tmp_path):
        logs = tmp_path / "Logs"
        logs.mkdir()
        recs = [_make_headpose_rec(seq=i) for i in range(3)]
        self._write_session(logs, 1, {1: recs})

        db = tmp_path / "out.sqlite"
        self._run_once(logs, db)

        conn = sqlite3.connect(str(db))
        n = conn.execute("SELECT COUNT(*) FROM headpose").fetchone()[0]
        assert n == 3

    def test_single_hr_session_inserted(self, tmp_path):
        logs = tmp_path / "Logs"
        logs.mkdir()
        recs = [_make_hr_rec(seq=i, hr_bpm=75.0) for i in range(2)]
        self._write_session(logs, 2, {3: recs})

        db = tmp_path / "out.sqlite"
        self._run_once(logs, db)

        conn = sqlite3.connect(str(db))
        n = conn.execute("SELECT COUNT(*) FROM hr").fetchone()[0]
        assert n == 2

    def test_two_sessions_both_inserted(self, tmp_path):
        """Two independent sessions must both be discovered and imported."""
        logs = tmp_path / "Logs"
        logs.mkdir()
        self._write_session(logs, 10, {3: [_make_hr_rec(hr_bpm=100.0)]})
        self._write_session(logs, 20, {3: [_make_hr_rec(hr_bpm=120.0)]})

        db = tmp_path / "out.sqlite"
        self._run_once(logs, db)

        conn = sqlite3.connect(str(db))
        n = conn.execute("SELECT COUNT(*) FROM hr").fetchone()[0]
        assert n == 2
        sids = {r[0] for r in conn.execute("SELECT session_id FROM hr")}
        assert sids == {10, 20}

    def test_session_not_rediscovered_within_same_run(self, tmp_path):
        """Within a single watch_sessions run, a session directory must only
        be registered once (added to 'seen' after first discovery)."""
        logs = tmp_path / "Logs"
        logs.mkdir()
        self._write_session(logs, 5, {3: [_make_hr_rec(hr_bpm=80.0)]})

        db = tmp_path / "out.sqlite"
        # Run a single pass that polls several times — the session must not
        # accumulate duplicate tail objects on subsequent scans.
        stop = threading.Event()

        def _stopper():
            time.sleep(0.5)
            stop.set()

        threading.Thread(target=_stopper, daemon=True).start()
        ct.watch_sessions(str(logs), str(db), stop_event=stop)

        # Each record should appear exactly once
        conn = sqlite3.connect(str(db))
        n = conn.execute("SELECT COUNT(*) FROM hr").fetchone()[0]
        assert n == 1, f"Expected exactly 1 HR row (no duplicates), got {n}"

    def test_bike_data_inserted(self, tmp_path):
        logs = tmp_path / "Logs"
        logs.mkdir()
        recs = [_make_bike_rec(seq=i, speed=float(i * 10)) for i in range(4)]
        self._write_session(logs, 3, {2: recs})

        db = tmp_path / "out.sqlite"
        self._run_once(logs, db)

        conn = sqlite3.connect(str(db))
        n = conn.execute("SELECT COUNT(*) FROM bike").fetchone()[0]
        assert n == 4

    def test_events_inserted(self, tmp_path):
        logs = tmp_path / "Logs"
        logs.mkdir()
        events = [
            (0, 0.0, json.dumps({"evt": "lap", "i": 1})),
            (1, 0.1, json.dumps({"evt": "start", "i": 0})),
        ]
        self._write_session(logs, 4, {}, event_recs=events)

        db = tmp_path / "out.sqlite"
        self._run_once(logs, db)

        conn = sqlite3.connect(str(db))
        n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert n == 2

    def test_batch_commit_mode(self, tmp_path):
        """sqlite_batch_size > 0 must still result in all rows committed on stop."""
        logs = tmp_path / "Logs"
        logs.mkdir()
        recs = [_make_headpose_rec(seq=i) for i in range(5)]
        self._write_session(logs, 1, {1: recs})

        db = tmp_path / "out.sqlite"
        self._run_once(logs, db, batch_size=100)

        conn = sqlite3.connect(str(db))
        n = conn.execute("SELECT COUNT(*) FROM headpose").fetchone()[0]
        assert n == 5

    @pytest.mark.skipif(
        not __import__("importlib").util.find_spec("pyarrow"),
        reason="pyarrow not installed",
    )
    def test_parquet_output_created(self, tmp_path):
        """When out_parquet_dir is set, .parquet files must be created for HR data."""
        logs = tmp_path / "Logs"
        logs.mkdir()
        recs = [_make_hr_rec(seq=i, hr_bpm=float(60 + i)) for i in range(5)]
        self._write_session(logs, 7, {3: recs})

        db = tmp_path / "out.sqlite"
        parquet_out = tmp_path / "parquet_parts"
        parquet_out.mkdir()

        # Need more time for parquet flush (triggered at 1-second intervals)
        stop = threading.Event()

        def _stopper():
            time.sleep(1.5)
            stop.set()

        threading.Thread(target=_stopper, daemon=True).start()
        ct.watch_sessions(
            str(logs), str(db),
            out_parquet_dir=str(parquet_out),
            stop_event=stop,
        )

        # Check that at least one parquet file was written somewhere in parquet_out
        all_parquets = list(parquet_out.rglob("*.parquet"))
        assert len(all_parquets) >= 1, (
            f"Expected at least 1 .parquet file in {parquet_out}, found: {list(parquet_out.rglob('*'))}"
        )
