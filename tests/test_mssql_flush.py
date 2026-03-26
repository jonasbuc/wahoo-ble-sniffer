"""
Tests for mssql_flush.py — JSONL parser and MSSQL bulk-insert.

All tests mock pyodbc so no real database is needed.
"""
import json
import os
import sys
import textwrap
from pathlib import Path
from unittest import mock

import pytest

# ── Ensure the package is importable ──────────────────────────────────────────
_here = Path(__file__).resolve().parent
_repo = _here.parent
sys.path.insert(0, str(_repo / "UnityIntegration" / "python"))

from db.mssql_flush import (  # noqa: E402
    _build_row,
    _bulk_insert,
    _ensure_session,
    _INSERT_SQL,
    flush_all,
    flush_session,
    parse_jsonl,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _headpose_line(sid=42, ts=1000, seq=1, ut=0.1):
    return json.dumps({
        "stream": 1, "ts_ns": ts, "sid": sid,
        "data": {"seq": seq, "ut": ut, "px": 1.0, "py": 2.0, "pz": 3.0,
                 "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0},
    })


def _bike_line(sid=42, ts=1000, seq=1, ut=0.1):
    return json.dumps({
        "stream": 2, "ts_ns": ts, "sid": sid,
        "data": {"seq": seq, "ut": ut, "speed": 25.0, "steering": 0.5,
                 "bf": 0, "br": 1},
    })


def _hr_line(sid=42, ts=1000, seq=1, ut=0.1):
    return json.dumps({
        "stream": 3, "ts_ns": ts, "sid": sid,
        "data": {"seq": seq, "ut": ut, "hr_bpm": 120.5},
    })


def _event_line(sid=42, ts=1000, seq=1, ut=0.1, payload='{"evt":"lap","i":1}'):
    return json.dumps({
        "stream": 4, "ts_ns": ts, "sid": sid,
        "data": {"seq": seq, "ut": ut, "json": payload},
    })


def _write_logfile(tmp_path, lines, name="session_42.jsonl"):
    """Write JSONL lines to a temp file and return its path."""
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


# ═══════════════════════════════════════════════════════════════════════════════
# _build_row
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildRow:
    def test_headpose(self):
        data = {"seq": 1, "ut": 0.5, "px": 1, "py": 2, "pz": 3,
                "qx": 0, "qy": 0, "qz": 0, "qw": 1}
        row = _build_row(1, 42, 9999, data)
        assert row == (42, 9999, 1, 0.5, 1, 2, 3, 0, 0, 0, 1)

    def test_bike(self):
        data = {"seq": 2, "ut": 1.0, "speed": 30.0, "steering": -0.5, "bf": 1, "br": 0}
        row = _build_row(2, 42, 9999, data)
        assert row == (42, 9999, 2, 1.0, 30.0, -0.5, 1, 0)

    def test_hr(self):
        data = {"seq": 3, "ut": 2.0, "hr_bpm": 80.0}
        row = _build_row(3, 42, 9999, data)
        assert row == (42, 9999, 3, 2.0, 80.0)

    def test_events(self):
        data = {"seq": 4, "ut": 3.0, "json": '{"x":1}'}
        row = _build_row(4, 42, 9999, data)
        assert row == (42, 9999, 4, 3.0, '{"x":1}')

    def test_missing_key_returns_none(self):
        data = {"seq": 1}  # missing "ut" etc.
        assert _build_row(1, 42, 9999, data) is None

    def test_unknown_stream_returns_none(self):
        assert _build_row(99, 42, 9999, {"seq": 1}) is None


# ═══════════════════════════════════════════════════════════════════════════════
# parse_jsonl
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseJsonl:
    def test_all_streams(self, tmp_path):
        lines = [
            _headpose_line(seq=1),
            _headpose_line(seq=2),
            _bike_line(seq=1),
            _hr_line(seq=1),
            _event_line(seq=1),
        ]
        path = _write_logfile(tmp_path, lines)
        rows = parse_jsonl(path)
        assert len(rows[1]) == 2
        assert len(rows[2]) == 1
        assert len(rows[3]) == 1
        assert len(rows[4]) == 1

    def test_empty_file(self, tmp_path):
        path = _write_logfile(tmp_path, [""])
        rows = parse_jsonl(path)
        assert all(len(v) == 0 for v in rows.values())

    def test_corrupt_json_skipped(self, tmp_path):
        lines = [
            _headpose_line(seq=1),
            "NOT VALID JSON {{{",
            _bike_line(seq=1),
        ]
        path = _write_logfile(tmp_path, lines)
        rows = parse_jsonl(path)
        assert len(rows[1]) == 1
        assert len(rows[2]) == 1

    def test_missing_field_skipped(self, tmp_path):
        # A line with stream but missing "data"
        lines = [json.dumps({"stream": 1, "ts_ns": 100, "sid": 42})]
        path = _write_logfile(tmp_path, lines)
        rows = parse_jsonl(path)
        assert len(rows[1]) == 0

    def test_invalid_stream_skipped(self, tmp_path):
        lines = [json.dumps({"stream": 99, "ts_ns": 100, "sid": 42, "data": {}})]
        path = _write_logfile(tmp_path, lines)
        rows = parse_jsonl(path)
        assert all(len(v) == 0 for v in rows.values())

    def test_blank_lines_ignored(self, tmp_path):
        lines = ["", _headpose_line(seq=1), "", ""]
        path = _write_logfile(tmp_path, lines)
        rows = parse_jsonl(path)
        assert len(rows[1]) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# _ensure_session
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnsureSession:
    def test_calls_execute(self):
        cursor = mock.MagicMock()
        _ensure_session(cursor, 42, 1000, "/session_42")
        cursor.execute.assert_called_once()
        args = cursor.execute.call_args
        assert "sessions" in args[0][0]
        assert args[0][1] == (42, 42, 1000, "/session_42")


# ═══════════════════════════════════════════════════════════════════════════════
# _bulk_insert
# ═══════════════════════════════════════════════════════════════════════════════

class TestBulkInsert:
    def test_inserts_rows(self):
        cursor = mock.MagicMock()
        rows = [(42, 100, 1, 0.1, 1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 1.0)] * 3
        n = _bulk_insert(cursor, 1, rows)
        assert n == 3
        cursor.executemany.assert_called_once()

    def test_empty_rows(self):
        cursor = mock.MagicMock()
        n = _bulk_insert(cursor, 1, [])
        assert n == 0
        cursor.executemany.assert_not_called()

    def test_unknown_stream(self):
        cursor = mock.MagicMock()
        n = _bulk_insert(cursor, 99, [(1, 2, 3)])
        assert n == 0

    def test_batching(self):
        cursor = mock.MagicMock()
        rows = [(42, 100, i, 0.1, 80.0) for i in range(12)]
        n = _bulk_insert(cursor, 3, rows, batch_size=5)
        assert n == 12
        assert cursor.executemany.call_count == 3  # 5 + 5 + 2


# ═══════════════════════════════════════════════════════════════════════════════
# flush_session (with mocked pyodbc)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFlushSession:
    def _mock_pyodbc(self):
        """Return a mock pyodbc module with connect()→conn→cursor chain."""
        cursor = mock.MagicMock()
        cursor.fast_executemany = False
        conn = mock.MagicMock()
        conn.cursor.return_value = cursor
        m = mock.MagicMock()
        m.connect.return_value = conn
        return m, conn, cursor

    def test_full_flush(self, tmp_path):
        lines = [
            _headpose_line(seq=1),
            _bike_line(seq=1),
            _hr_line(seq=1),
            _event_line(seq=1),
        ]
        path = _write_logfile(tmp_path, lines)
        mock_pyodbc, conn, cursor = self._mock_pyodbc()

        with mock.patch("db.mssql_flush.pyodbc", mock_pyodbc), \
             mock.patch("db.mssql_flush.HAVE_PYODBC", True):
            result = flush_session(path, "FAKE_CONN", rename_done=False)

        assert result == {"headpose": 1, "bike": 1, "hr": 1, "events": 1}
        conn.commit.assert_called_once()

    def test_rename_done(self, tmp_path):
        lines = [_headpose_line(seq=1)]
        path = _write_logfile(tmp_path, lines)
        mock_pyodbc, conn, cursor = self._mock_pyodbc()

        with mock.patch("db.mssql_flush.pyodbc", mock_pyodbc), \
             mock.patch("db.mssql_flush.HAVE_PYODBC", True):
            flush_session(path, "FAKE_CONN", rename_done=True)

        assert not os.path.exists(path)
        assert os.path.exists(path + ".done")

    def test_rollback_on_error(self, tmp_path):
        lines = [_headpose_line(seq=1)]
        path = _write_logfile(tmp_path, lines)
        mock_pyodbc, conn, cursor = self._mock_pyodbc()
        cursor.executemany.side_effect = Exception("DB error")

        with mock.patch("db.mssql_flush.pyodbc", mock_pyodbc), \
             mock.patch("db.mssql_flush.HAVE_PYODBC", True):
            with pytest.raises(Exception, match="DB error"):
                flush_session(path, "FAKE_CONN", rename_done=False)

        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_empty_file_returns_zeros(self, tmp_path):
        path = _write_logfile(tmp_path, [""])
        mock_pyodbc, conn, cursor = self._mock_pyodbc()

        with mock.patch("db.mssql_flush.pyodbc", mock_pyodbc), \
             mock.patch("db.mssql_flush.HAVE_PYODBC", True):
            result = flush_session(path, "FAKE_CONN", rename_done=False)

        assert result == {"headpose": 0, "bike": 0, "hr": 0, "events": 0}

    def test_file_not_found(self, tmp_path):
        mock_pyodbc = mock.MagicMock()
        with mock.patch("db.mssql_flush.pyodbc", mock_pyodbc), \
             mock.patch("db.mssql_flush.HAVE_PYODBC", True):
            with pytest.raises(FileNotFoundError):
                flush_session(tmp_path / "nope.jsonl", "FAKE_CONN")

    def test_import_error_without_pyodbc(self, tmp_path):
        lines = [_headpose_line(seq=1)]
        path = _write_logfile(tmp_path, lines)
        with mock.patch("db.mssql_flush.HAVE_PYODBC", False):
            with pytest.raises(ImportError, match="pyodbc"):
                flush_session(path, "FAKE_CONN")

    def test_session_id_override(self, tmp_path):
        lines = [_headpose_line(sid=42, seq=1)]
        path = _write_logfile(tmp_path, lines)
        mock_pyodbc, conn, cursor = self._mock_pyodbc()

        with mock.patch("db.mssql_flush.pyodbc", mock_pyodbc), \
             mock.patch("db.mssql_flush.HAVE_PYODBC", True):
            flush_session(path, "FAKE_CONN", session_id=99, rename_done=False)

        # _ensure_session should have been called with sid=99
        exec_call = cursor.execute.call_args
        assert exec_call[0][1][0] == 99  # first positional param = sid


# ═══════════════════════════════════════════════════════════════════════════════
# flush_all
# ═══════════════════════════════════════════════════════════════════════════════

class TestFlushAll:
    def test_flushes_multiple_files(self, tmp_path):
        for i in (1, 2, 3):
            _write_logfile(tmp_path, [_headpose_line(sid=i, seq=1)],
                           name=f"session_{i}.jsonl")
        mock_pyodbc, conn, cursor = mock.MagicMock(), mock.MagicMock(), mock.MagicMock()
        cursor.fast_executemany = False
        conn.cursor.return_value = cursor
        mock_pyodbc.connect.return_value = conn

        with mock.patch("db.mssql_flush.pyodbc", mock_pyodbc), \
             mock.patch("db.mssql_flush.HAVE_PYODBC", True):
            n = flush_all(tmp_path, "FAKE_CONN")

        assert n == 3

    def test_skips_done_files(self, tmp_path):
        _write_logfile(tmp_path, [_headpose_line(seq=1)], name="session_1.jsonl")
        (tmp_path / "session_2.jsonl.done").write_text("already done")

        mock_pyodbc, conn, cursor = mock.MagicMock(), mock.MagicMock(), mock.MagicMock()
        cursor.fast_executemany = False
        conn.cursor.return_value = cursor
        mock_pyodbc.connect.return_value = conn

        with mock.patch("db.mssql_flush.pyodbc", mock_pyodbc), \
             mock.patch("db.mssql_flush.HAVE_PYODBC", True):
            n = flush_all(tmp_path, "FAKE_CONN")

        assert n == 1

    def test_renames_failed_on_error(self, tmp_path):
        _write_logfile(tmp_path, [_headpose_line(seq=1)], name="session_1.jsonl")

        mock_pyodbc = mock.MagicMock()
        mock_pyodbc.connect.side_effect = Exception("Connection refused")

        with mock.patch("db.mssql_flush.pyodbc", mock_pyodbc), \
             mock.patch("db.mssql_flush.HAVE_PYODBC", True):
            n = flush_all(tmp_path, "FAKE_CONN")

        assert n == 0
        # The original file should have been renamed to .failed.*
        remaining = list(tmp_path.glob("session_1.jsonl.failed.*"))
        assert len(remaining) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# INSERT SQL constants
# ═══════════════════════════════════════════════════════════════════════════════

class TestInsertSql:
    @pytest.mark.parametrize("stream,table", [
        (1, "headpose"), (2, "bike"), (3, "hr"), (4, "events"),
    ])
    def test_all_streams_have_sql(self, stream, table):
        sql = _INSERT_SQL[stream]
        assert f"INSERT INTO {table}" in sql
        assert "VALUES" in sql
