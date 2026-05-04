"""
Regression tests for the recv_ts_ns timestamp bug.

Root cause (fixed): collector_tail.FileTail.tail_once() used time.monotonic_ns()
instead of time.time_ns().  monotonic_ns() counts from an arbitrary reference
(system boot), so recv_ts_ns would be a tiny value like 7_200_000_000_000 (2 h
of uptime in ns) rather than ~1.7 × 10¹⁸ (current Unix epoch in ns).

Both the SQLite readable views and the MSSQL schema interpret recv_ts_ns as
nanoseconds since the Unix epoch:
  SQLite : datetime(recv_ts_ns/1000000000.0, 'unixepoch') → human date
  MSSQL  : DATEADD(SECOND, recv_ts_ns / 1000000000, '1970-01-01') → DATETIME2

A monotonic timestamp would decode to a date near 1970-01-01, corrupting all
human-readable views in both local SQLite and the external MSSQL database.
"""
from __future__ import annotations

import struct
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from bridge import collector_tail as ct


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_headpose_rec() -> bytes:
    """Pack one valid headpose record (stream 1, 36 bytes: <Iffffffff>)."""
    return struct.pack('<Iffffffff', 1, 0.1, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


def _make_hr_rec() -> bytes:
    """Pack one valid HR record (stream 3, 12 bytes: <Iff>)."""
    return struct.pack('<Iff', 1, 0.1, 72.5)


# Unix-epoch nanosecond bounds: 2000-01-01 → 2100-01-01
_TS_NS_MIN = 946_684_800 * 1_000_000_000
_TS_NS_MAX = 4_102_444_800 * 1_000_000_000


# ── Test: time.time_ns() produces epoch-relative value ───────────────────────

class TestTimeTimeNsIsEpochBased:
    """time.time_ns() must return a value in the Unix epoch range, unlike
    time.monotonic_ns() which would start near 0 at boot."""

    def test_time_time_ns_is_in_epoch_range(self) -> None:
        ts = time.time_ns()
        assert _TS_NS_MIN < ts < _TS_NS_MAX, (
            f"time.time_ns()={ts} is outside the expected Unix-epoch range "
            f"[{_TS_NS_MIN}, {_TS_NS_MAX}]. "
            "This would indicate a system clock problem."
        )

    def test_monotonic_ns_is_NOT_in_epoch_range(self) -> None:
        """Confirm that monotonic time is much smaller — proving why it was wrong."""
        ts = time.monotonic_ns()
        # A machine must have been running for less than 30 years for this to hold.
        assert ts < _TS_NS_MIN, (
            f"time.monotonic_ns()={ts} unexpectedly looks like a Unix epoch "
            "timestamp (> year 2000). This test's assumption about monotonic "
            "time may need updating."
        )


# ── Test: FileTail uses time.time_ns() ────────────────────────────────────────

class TestFileTailUsesTimeTimeNs:
    """FileTail.tail_once() must store epoch-based recv_ts_ns in the DB."""

    def _write_vrsf_chunk(
        self, path: Path, stream_id: int, session_id: int, records: list[bytes]
    ) -> None:
        """Write one minimal VRSF file containing a single valid chunk."""
        import zlib

        rec_bytes = b"".join(records)
        rec_count = len(records)
        payload = rec_bytes
        payload_crc = zlib.crc32(payload) & 0xFFFFFFFF

        # Build header with CRC fields zeroed, compute header CRC, then set it.
        magic = b"VRSF"
        version = 1
        flags = 0
        chunk_seq = 0
        payload_bytes = len(payload)
        header_crc_placeholder = 0
        reserved = 0
        # Header format: 4s B B H Q I I I I I I = 40 bytes
        # magic(4) version(1) stream_id(1) flags(2) session_id(8)
        # chunk_seq(4) rec_count(4) payload_bytes(4)
        # header_crc(4) payload_crc(4) reserved(4)
        FMT = "<4sBBHQIIIIII"
        hdr = struct.pack(
            FMT,
            magic, version, stream_id, flags,
            session_id, chunk_seq, rec_count, payload_bytes,
            header_crc_placeholder, payload_crc, reserved,
        )
        hdr_for_crc = bytearray(hdr)
        for i in range(28, 36):
            hdr_for_crc[i] = 0
        header_crc = zlib.crc32(bytes(hdr_for_crc)) & 0xFFFFFFFF
        hdr = struct.pack(
            FMT,
            magic, version, stream_id, flags,
            session_id, chunk_seq, rec_count, payload_bytes,
            header_crc, payload_crc, reserved,
        )
        with open(path, "wb") as f:
            f.write(hdr + payload)

    def test_recv_ts_ns_is_unix_epoch(self, tmp_path: Path) -> None:
        """After tail_once(), the recv_ts_ns stored in SQLite must be in the
        Unix epoch range, not a monotonic/uptime value."""
        vrsf = tmp_path / "hr.vrsf"
        self._write_vrsf_chunk(vrsf, 3, 42, [_make_hr_rec()])

        tail = ct.FileTail(str(vrsf), 3, 42, rec_size=12, variable=False)
        ts_ns, records = tail.tail_once()

        assert records is not None and len(records) == 1
        assert _TS_NS_MIN < ts_ns < _TS_NS_MAX, (
            f"tail_once() returned recv_ts_ns={ts_ns}. "
            f"Expected Unix epoch range [{_TS_NS_MIN}, {_TS_NS_MAX}]. "
            "Was time.monotonic_ns() used instead of time.time_ns()?"
        )

    def test_recv_ts_ns_inserted_into_sqlite_is_epoch(self, tmp_path: Path) -> None:
        """The value stored in the SQLite hr table must also be epoch-based."""
        vrsf = tmp_path / "hr2.vrsf"
        self._write_vrsf_chunk(vrsf, 3, 99, [_make_hr_rec()])

        db = tmp_path / "test.db"
        conn = ct.init_db(str(db))

        tail = ct.FileTail(str(vrsf), 3, 99, rec_size=12, variable=False)
        ts_ns, records = tail.tail_once()
        ct.insert_records_batch(conn, 3, 99, ts_ns, records)
        conn.commit()

        cur = conn.cursor()
        cur.execute("SELECT recv_ts_ns FROM hr WHERE session_id=99")
        row = cur.fetchone()
        assert row is not None
        stored_ts = row[0]
        assert _TS_NS_MIN < stored_ts < _TS_NS_MAX, (
            f"Stored recv_ts_ns={stored_ts} is not in Unix epoch range. "
            "SQLite readable views (datetime(recv_ts_ns/1e9,'unixepoch')) "
            "and MSSQL computed columns will show wrong dates."
        )

    def test_monotonic_patch_would_fail(self, tmp_path: Path) -> None:
        """Demonstrates the original bug: patching time.time_ns back to
        monotonic_ns produces a value outside the epoch range."""
        vrsf = tmp_path / "hr3.vrsf"

        import zlib as _zlib
        rec = _make_hr_rec()
        payload = rec
        payload_crc = _zlib.crc32(payload) & 0xFFFFFFFF
        FMT = "<4sBBHQIIIIII"
        hdr = struct.pack(FMT, b"VRSF", 1, 3, 0, 77, 0, 1, len(payload), 0, payload_crc, 0)
        hdr_b = bytearray(hdr)
        for i in range(28, 36):
            hdr_b[i] = 0
        hcrc = _zlib.crc32(bytes(hdr_b)) & 0xFFFFFFFF
        hdr = struct.pack(FMT, b"VRSF", 1, 3, 0, 77, 0, 1, len(payload), hcrc, payload_crc, 0)
        with open(vrsf, "wb") as f:
            f.write(hdr + payload)

        # Simulate the OLD buggy behaviour by patching time.time_ns to return
        # a monotonic-like value (e.g. 2 hours of uptime in nanoseconds).
        fake_monotonic_ns = 2 * 3600 * 1_000_000_000  # 7_200_000_000_000
        with patch("time.time_ns", return_value=fake_monotonic_ns):
            tail = ct.FileTail(str(vrsf), 3, 77, rec_size=12, variable=False)
            ts_ns, records = tail.tail_once()

        assert ts_ns == fake_monotonic_ns
        # This value is NOT in the epoch range — it would decode to ~1970-01-01T02:00
        assert ts_ns < _TS_NS_MIN, (
            "Expected the fake monotonic value to be outside the epoch range."
        )


# ── Test: validate_db detects wrong timestamps ────────────────────────────────

class TestValidateDbTimestamps:
    """validate_db.validate_timestamps() must flag monotonic-based values."""

    def test_flags_monotonic_ns_value(self, tmp_path: Path) -> None:
        from bridge.db.sqlite.validate_db import validate_timestamps

        db = tmp_path / "bad_ts.db"
        conn = ct.init_db(str(db))

        # Insert a row with a monotonic-like recv_ts_ns (2 hours in ns)
        bad_ts = 2 * 3600 * 1_000_000_000
        ct.insert_records_batch(conn, 3, 1, bad_ts, [_make_hr_rec()])
        conn.commit()

        count, problems = validate_timestamps(conn)
        assert count > 0
        assert len(problems) > 0, "validate_timestamps should flag monotonic-based recv_ts_ns"
        assert "monotonic" in problems[0].lower() or "outside valid" in problems[0].lower()

    def test_accepts_real_epoch_value(self, tmp_path: Path) -> None:
        from bridge.db.sqlite.validate_db import validate_timestamps

        db = tmp_path / "good_ts.db"
        conn = ct.init_db(str(db))

        good_ts = time.time_ns()
        ct.insert_records_batch(conn, 3, 2, good_ts, [_make_hr_rec()])
        conn.commit()

        count, problems = validate_timestamps(conn)
        assert count > 0
        assert len(problems) == 0, f"Unexpected problems: {problems}"


# ── Test: mssql_flush parse_jsonl accepts ts_ns round-trip ───────────────────

class TestMssqlFlushTimestampRoundtrip:
    """Verify that a ts_ns produced by time.time_ns() survives JSONL
    serialisation and is passed unchanged to the MSSQL INSERT parameters."""

    def test_ts_ns_preserved_through_jsonl(self, tmp_path: Path) -> None:
        import json
        from bridge.db.mssql.mssql_flush import parse_jsonl

        ts_ns = time.time_ns()
        sid = 55
        record = {
            "stream": 3, "ts_ns": ts_ns, "sid": sid,
            "data": {"seq": 1, "ut": 0.1, "hr_bpm": 72.0},
        }
        jsonl = tmp_path / "session_55.jsonl"
        with open(jsonl, "w") as f:
            f.write(json.dumps(record) + "\n")

        rows = parse_jsonl(jsonl)
        assert len(rows[3]) == 1
        parsed_sid, parsed_ts_ns = rows[3][0][0], rows[3][0][1]
        assert parsed_sid == sid
        assert parsed_ts_ns == ts_ns, (
            f"ts_ns changed during JSONL round-trip: "
            f"wrote {ts_ns}, read back {parsed_ts_ns}"
        )
        # Also verify the value is in the epoch range
        assert _TS_NS_MIN < parsed_ts_ns < _TS_NS_MAX
