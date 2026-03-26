"""
Tests for the SessionLogger class in collector_tail.py.

Verifies JSONL output format, all four stream types, edge cases, and
integration with the watch_sessions loop.
"""
import json
import os
import struct
import sys
from pathlib import Path
from unittest import mock

import pytest

# ── Ensure the package is importable ──────────────────────────────────────────
_here = Path(__file__).resolve().parent
_repo = _here.parent
sys.path.insert(0, str(_repo / "UnityIntegration" / "python"))

from collector_tail import SessionLogger  # noqa: E402


# ── Binary record helpers (same struct packing as Unity writes) ───────────────

def _make_headpose_rec(seq=1, ut=0.5, px=1.0, py=2.0, pz=3.0,
                       qx=0.0, qy=0.0, qz=0.0, qw=1.0):
    return struct.pack('<Iffffffff', seq, ut, px, py, pz, qx, qy, qz, qw)


def _make_bike_rec(seq=1, ut=0.5, speed=25.0, steering=0.1, bf=0, br=1):
    return struct.pack('<Ifff', seq, ut, speed, steering) + bytes([bf, br, 0, 0])


def _make_hr_rec(seq=1, ut=0.5, hr_bpm=120.0):
    return struct.pack('<Iff', seq, ut, hr_bpm)


# ═══════════════════════════════════════════════════════════════════════════════
# SessionLogger – basic creation
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionLoggerCreation:
    def test_creates_directory_and_file(self, tmp_path):
        logdir = tmp_path / "logs" / "sub"
        logger = SessionLogger(str(logdir), session_id=42)
        assert os.path.isdir(logdir)
        assert os.path.exists(logger.path)
        assert "session_42.jsonl" in logger.path
        logger.close()

    def test_row_count_starts_at_zero(self, tmp_path):
        logger = SessionLogger(str(tmp_path), 1)
        assert logger.row_count == 0
        logger.close()


# ═══════════════════════════════════════════════════════════════════════════════
# SessionLogger – write_records (streams 1–3)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionLoggerWriteRecords:
    def test_headpose_stream(self, tmp_path):
        logger = SessionLogger(str(tmp_path), 42)
        recs = [_make_headpose_rec(seq=1), _make_headpose_rec(seq=2)]
        n = logger.write_records(1, 999999, recs)
        logger.close()

        assert n == 2
        assert logger.row_count == 2
        lines = Path(logger.path).read_text().strip().split("\n")
        assert len(lines) == 2
        obj = json.loads(lines[0])
        assert obj["stream"] == 1
        assert obj["ts_ns"] == 999999
        assert obj["sid"] == 42
        assert obj["data"]["seq"] == 1
        assert obj["data"]["qw"] == 1.0

    def test_bike_stream(self, tmp_path):
        logger = SessionLogger(str(tmp_path), 42)
        recs = [_make_bike_rec(seq=10, speed=30.0, bf=1, br=0)]
        n = logger.write_records(2, 100, recs)
        logger.close()

        assert n == 1
        obj = json.loads(Path(logger.path).read_text().strip())
        assert obj["stream"] == 2
        assert obj["data"]["speed"] == pytest.approx(30.0, abs=0.01)
        assert obj["data"]["bf"] == 1
        assert obj["data"]["br"] == 0

    def test_hr_stream(self, tmp_path):
        logger = SessionLogger(str(tmp_path), 42)
        recs = [_make_hr_rec(seq=5, hr_bpm=72.5)]
        n = logger.write_records(3, 200, recs)
        logger.close()

        assert n == 1
        obj = json.loads(Path(logger.path).read_text().strip())
        assert obj["stream"] == 3
        assert obj["data"]["hr_bpm"] == pytest.approx(72.5, abs=0.01)

    def test_empty_records_writes_nothing(self, tmp_path):
        logger = SessionLogger(str(tmp_path), 42)
        n = logger.write_records(1, 100, [])
        logger.close()

        assert n == 0
        assert logger.row_count == 0
        content = Path(logger.path).read_text()
        assert content == ""


# ═══════════════════════════════════════════════════════════════════════════════
# SessionLogger – write_events (stream 4)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionLoggerWriteEvents:
    def test_single_event(self, tmp_path):
        logger = SessionLogger(str(tmp_path), 42)
        events = [(1, 0.5, '{"evt":"lap","i":1}')]
        n = logger.write_events(300, events)
        logger.close()

        assert n == 1
        obj = json.loads(Path(logger.path).read_text().strip())
        assert obj["stream"] == 4
        assert obj["data"]["json"] == '{"evt":"lap","i":1}'

    def test_multiple_events(self, tmp_path):
        logger = SessionLogger(str(tmp_path), 42)
        events = [
            (1, 0.5, '{"evt":"start"}'),
            (2, 1.5, '{"evt":"stop"}'),
        ]
        n = logger.write_events(300, events)
        logger.close()

        assert n == 2
        lines = Path(logger.path).read_text().strip().split("\n")
        assert len(lines) == 2

    def test_empty_events(self, tmp_path):
        logger = SessionLogger(str(tmp_path), 42)
        n = logger.write_events(300, [])
        logger.close()
        assert n == 0


# ═══════════════════════════════════════════════════════════════════════════════
# SessionLogger – mixed writes and append behaviour
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionLoggerAppend:
    def test_multiple_writes_append(self, tmp_path):
        logger = SessionLogger(str(tmp_path), 42)
        logger.write_records(1, 100, [_make_headpose_rec(seq=1)])
        logger.write_records(2, 200, [_make_bike_rec(seq=1)])
        logger.write_events(300, [(1, 0.5, "{}")])
        logger.close()

        lines = Path(logger.path).read_text().strip().split("\n")
        assert len(lines) == 3
        assert logger.row_count == 3
        streams = [json.loads(l)["stream"] for l in lines]
        assert streams == [1, 2, 4]

    def test_close_is_idempotent(self, tmp_path):
        logger = SessionLogger(str(tmp_path), 42)
        logger.close()
        logger.close()  # should not raise


# ═══════════════════════════════════════════════════════════════════════════════
# SessionLogger – JSONL roundtrip with mssql_flush.parse_jsonl
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionLoggerRoundtrip:
    def test_roundtrip_all_streams(self, tmp_path):
        """Write all 4 stream types and verify parse_jsonl reads them back."""
        from db.mssql.mssql_flush import parse_jsonl

        logger = SessionLogger(str(tmp_path), 42)
        logger.write_records(1, 100, [_make_headpose_rec(seq=1), _make_headpose_rec(seq=2)])
        logger.write_records(2, 100, [_make_bike_rec(seq=1)])
        logger.write_records(3, 100, [_make_hr_rec(seq=1)])
        logger.write_events(100, [(1, 0.5, '{"evt":"test"}')])
        logger.close()

        rows = parse_jsonl(logger.path)
        assert len(rows[1]) == 2
        assert len(rows[2]) == 1
        assert len(rows[3]) == 1
        assert len(rows[4]) == 1

        # Verify the headpose row has all expected columns
        hp_row = rows[1][0]
        assert hp_row[0] == 42     # session_id
        assert hp_row[1] == 100    # recv_ts_ns
        assert hp_row[2] == 1      # seq
