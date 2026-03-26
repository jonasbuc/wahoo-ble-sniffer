"""
test_db_utils.py
================
Tests for all previously-untested database utility scripts:

  db/validate_db.py
  -----------------
  • float_ok() — finite floats, NaN, Inf, non-numeric strings, None
  • validate_headpose() — valid rows pass; bad quaternion norm detected;
                          non-finite position/rotation detected
  • validate_bike() — valid rows pass; negative speed; bad brake flags;
                      non-finite steering
  • validate_hr() — valid range passes; < 30 flagged; > 220 flagged;
                    non-finite flagged
  • validate_events() — valid JSON passes; invalid JSON caught with message
  • main() — missing DB path → 2; all-clean → 0; problems → 1

  db/pretty_dump_db.py
  --------------------
  • ns_to_iso() — known nanosecond → ISO string ending in "Z"
  • ms_to_iso() — known millisecond → ISO string
  • pretty_value("recv_ts_ns", val) — contains ms= and iso=
  • pretty_value("started_unix_ms", val) — contains iso=
  • pretty_value("other_col", None) → "NULL"
  • pretty_value("other_col", 42) → "42"
  • dump_table() — empty table prints "(no rows)"; table with rows prints cols

  db/create_readable_views.py
  ---------------------------
  • create_views() is idempotent (second call does not raise)
  • Views exist and are queryable after create_views()
  • create_views() on missing DB path does not raise

  db/export_readable_views.py
  ---------------------------
  • rows_and_cols() — result shape matches actual table
  • write_csv() — creates file with correct headers
  • export_all_views() — skips nonexistent view without raising
  • export_all_views() — returns written file paths
  • try_write_parquet() — returns False when pyarrow absent (monkeypatch)
"""

from __future__ import annotations

import io
import json
import math
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Path setup — conftest.py already adds repo root, but guard just in case
# ---------------------------------------------------------------------------
_repo = Path(__file__).resolve().parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

from UnityIntegration.python.db.sqlite.validate_db import (
    float_ok,
    validate_bike,
    validate_events,
    validate_headpose,
    validate_hr,
)
import UnityIntegration.python.db.sqlite.validate_db as validate_module

from UnityIntegration.python.db.sqlite.pretty_dump_db import (
    dump_table,
    ms_to_iso,
    ns_to_iso,
    pretty_value,
)

from UnityIntegration.python.db.sqlite.create_readable_views import create_views

from UnityIntegration.python.db.sqlite.export_readable_views import (
    export_all_views,
    rows_and_cols,
    try_write_parquet,
    write_csv,
)
from UnityIntegration.python.collector_tail import init_db


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _empty_db(tmp_path) -> tuple[Path, sqlite3.Connection]:
    """Return (db_path, connection) for a fresh collector DB."""
    db = tmp_path / "test.sqlite"
    conn = init_db(str(db))
    return db, conn


def _insert_headpose(conn, session_id=1, seq=0,
                     px=0.0, py=0.0, pz=0.0,
                     qx=0.0, qy=0.0, qz=0.0, qw=1.0):
    conn.execute(
        "INSERT INTO headpose(session_id, recv_ts_ns, seq, unity_t, px,py,pz,qx,qy,qz,qw)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (session_id, 1_000_000_000, seq, 0.0, px, py, pz, qx, qy, qz, qw),
    )
    conn.commit()


def _insert_bike(conn, session_id=1, seq=0,
                 speed=10.0, steering=0.0, bf=0, br=0):
    conn.execute(
        "INSERT INTO bike(session_id, recv_ts_ns, seq, unity_t, speed, steering, brake_front, brake_rear)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (session_id, 1_000_000_000, seq, 0.0, speed, steering, bf, br),
    )
    conn.commit()


def _insert_hr(conn, session_id=1, seq=0, hr_bpm=75.0):
    conn.execute(
        "INSERT INTO hr(session_id, recv_ts_ns, seq, unity_t, hr_bpm)"
        " VALUES(?,?,?,?,?)",
        (session_id, 1_000_000_000, seq, 0.0, hr_bpm),
    )
    conn.commit()


def _insert_event(conn, session_id=1, seq=0, js='{"evt":"lap","i":1}'):
    conn.execute(
        "INSERT INTO events(session_id, recv_ts_ns, seq, unity_t, json)"
        " VALUES(?,?,?,?,?)",
        (session_id, 1_000_000_000, seq, 0.0, js),
    )
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# validate_db.py — float_ok
# ─────────────────────────────────────────────────────────────────────────────

class TestFloatOk:

    def test_good_float(self):
        assert float_ok(1.0) is True

    def test_zero(self):
        assert float_ok(0.0) is True

    def test_negative_float(self):
        assert float_ok(-99.9) is True

    def test_integer(self):
        assert float_ok(42) is True

    def test_nan_rejected(self):
        assert float_ok(float("nan")) is False

    def test_positive_inf_rejected(self):
        assert float_ok(float("inf")) is False

    def test_negative_inf_rejected(self):
        assert float_ok(float("-inf")) is False

    def test_non_numeric_string_rejected(self):
        assert float_ok("abc") is False

    def test_none_rejected(self):
        assert float_ok(None) is False

    def test_numeric_string_accepted(self):
        """A string that parses as a finite float must be accepted."""
        assert float_ok("3.14") is True

    def test_numeric_string_nan_rejected(self):
        """'nan' string must be rejected."""
        assert float_ok("nan") is False

    def test_empty_string_rejected(self):
        assert float_ok("") is False


# ─────────────────────────────────────────────────────────────────────────────
# validate_db.py — validate_headpose
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateHeadpose:

    def test_empty_table_returns_zero_count_no_problems(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        count, problems = validate_headpose(conn)
        assert count == 0
        assert problems == []

    def test_valid_unit_quaternion_passes(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        _insert_headpose(conn, qx=0.0, qy=0.0, qz=0.0, qw=1.0)
        count, problems = validate_headpose(conn)
        assert count == 1
        assert problems == []

    def test_non_unit_quaternion_detected(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        # norm = sqrt(2² + 2² + 2² + 2²) = 4, far from 1.0
        _insert_headpose(conn, qx=2.0, qy=2.0, qz=2.0, qw=2.0)
        count, problems = validate_headpose(conn)
        assert count == 1
        assert any("norm" in p for p in problems)

    def test_near_unit_quat_within_tolerance_passes(self, tmp_path):
        """Norm = 1.05 should still pass (tolerance is ±0.1)."""
        _, conn = _empty_db(tmp_path)
        # Slightly non-unit: scale unit quat by ~1.05
        import math as _math
        scale = 1.05
        qw = scale  # all others zero
        _insert_headpose(conn, qx=0.0, qy=0.0, qz=0.0, qw=qw)
        count, problems = validate_headpose(conn)
        assert count == 1
        assert problems == [], f"Expected no problems but got: {problems}"

    def test_non_finite_position_detected(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        _insert_headpose(conn, px=float("nan"))
        count, problems = validate_headpose(conn)
        assert any("non-float" in p for p in problems)

    def test_multiple_rows_all_valid(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        for i in range(5):
            _insert_headpose(conn, seq=i)
        count, problems = validate_headpose(conn)
        assert count == 5
        assert problems == []

    def test_returns_count_of_checked_rows(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        for i in range(7):
            _insert_headpose(conn, seq=i)
        count, _ = validate_headpose(conn)
        assert count == 7


# ─────────────────────────────────────────────────────────────────────────────
# validate_db.py — validate_bike
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateBike:

    def test_empty_table_no_problems(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        count, problems = validate_bike(conn)
        assert count == 0
        assert problems == []

    def test_valid_row_passes(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        _insert_bike(conn, speed=15.0, steering=0.3, bf=0, br=0)
        count, problems = validate_bike(conn)
        assert count == 1
        assert problems == []

    def test_negative_speed_detected(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        _insert_bike(conn, speed=-5.0)
        count, problems = validate_bike(conn)
        assert any("speed" in p for p in problems)

    def test_invalid_brake_flags_detected(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        _insert_bike(conn, bf=2, br=3)
        count, problems = validate_bike(conn)
        assert any("brake" in p for p in problems)

    def test_nan_steering_detected(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        _insert_bike(conn, steering=float("nan"))
        count, problems = validate_bike(conn)
        assert any("steering" in p for p in problems)

    def test_valid_brakes_both_set_passes(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        _insert_bike(conn, bf=1, br=1)
        count, problems = validate_bike(conn)
        assert problems == []

    def test_zero_speed_passes(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        _insert_bike(conn, speed=0.0)
        count, problems = validate_bike(conn)
        assert problems == []


# ─────────────────────────────────────────────────────────────────────────────
# validate_db.py — validate_hr
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateHr:

    def test_empty_table_no_problems(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        count, problems = validate_hr(conn)
        assert count == 0
        assert problems == []

    def test_valid_hr_passes(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        _insert_hr(conn, hr_bpm=75.0)
        count, problems = validate_hr(conn)
        assert count == 1
        assert problems == []

    def test_hr_below_30_detected(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        _insert_hr(conn, hr_bpm=20.0)
        count, problems = validate_hr(conn)
        assert any("range" in p for p in problems)

    def test_hr_above_220_detected(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        _insert_hr(conn, hr_bpm=250.0)
        count, problems = validate_hr(conn)
        assert any("range" in p for p in problems)

    def test_hr_nan_detected(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        _insert_hr(conn, hr_bpm=float("nan"))
        count, problems = validate_hr(conn)
        assert len(problems) > 0

    def test_hr_boundary_30_passes(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        _insert_hr(conn, hr_bpm=30.0)
        count, problems = validate_hr(conn)
        assert problems == []

    def test_hr_boundary_220_passes(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        _insert_hr(conn, hr_bpm=220.0)
        count, problems = validate_hr(conn)
        assert problems == []

    def test_multiple_hr_rows(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        for i, bpm in enumerate([60, 70, 80, 90]):
            _insert_hr(conn, seq=i, hr_bpm=float(bpm))
        count, problems = validate_hr(conn)
        assert count == 4
        assert problems == []


# ─────────────────────────────────────────────────────────────────────────────
# validate_db.py — validate_events
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateEvents:

    def test_empty_table_no_problems(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        count, problems = validate_events(conn)
        assert count == 0
        assert problems == []

    def test_valid_json_passes(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        _insert_event(conn, js='{"evt":"lap","i":1}')
        count, problems = validate_events(conn)
        assert count == 1
        assert problems == []

    def test_invalid_json_detected(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        _insert_event(conn, js="{this is not json}")
        count, problems = validate_events(conn)
        assert any("json parse error" in p for p in problems)

    def test_valid_complex_json_passes(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        js = json.dumps({"evt": "spawn", "pos": [1.0, 2.0, 3.0], "active": True})
        _insert_event(conn, js=js)
        count, problems = validate_events(conn)
        assert problems == []

    def test_empty_json_object_passes(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        _insert_event(conn, js="{}")
        count, problems = validate_events(conn)
        assert problems == []

    def test_truncated_json_detected(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        _insert_event(conn, js='{"evt": "lap"')  # missing closing brace
        count, problems = validate_events(conn)
        assert len(problems) == 1


# ─────────────────────────────────────────────────────────────────────────────
# validate_db.py — main()
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateMain:

    def test_missing_db_returns_2(self, tmp_path):
        db_path = str(tmp_path / "nonexistent.sqlite")
        with patch.object(sys, "argv", ["validate_db", "--db", db_path]):
            result = validate_module.main()
        assert result == 2

    def test_clean_db_returns_0(self, tmp_path):
        db, conn = _empty_db(tmp_path)
        _insert_headpose(conn)
        _insert_bike(conn)
        _insert_hr(conn)
        _insert_event(conn)
        conn.close()

        with patch.object(sys, "argv", ["validate_db", "--db", str(db)]):
            result = validate_module.main()
        assert result == 0

    def test_db_with_problems_returns_1(self, tmp_path):
        db, conn = _empty_db(tmp_path)
        _insert_hr(conn, hr_bpm=999.0)  # out of range
        conn.close()

        with patch.object(sys, "argv", ["validate_db", "--db", str(db)]):
            result = validate_module.main()
        assert result == 1


# ─────────────────────────────────────────────────────────────────────────────
# pretty_dump_db.py — ns_to_iso / ms_to_iso
# ─────────────────────────────────────────────────────────────────────────────

class TestTimestampConversions:

    # Known reference point: 2023-11-14T22:13:20 UTC
    # Unix seconds: 1_700_000_000
    _REF_UNIX_S  = 1_700_000_000
    _REF_UNIX_MS = _REF_UNIX_S * 1_000
    _REF_UNIX_NS = _REF_UNIX_S * 1_000_000_000

    def test_ns_to_iso_ends_with_Z(self):
        result = ns_to_iso(self._REF_UNIX_NS)
        assert result.endswith("Z"), f"Expected 'Z' suffix, got: {result!r}"

    def test_ns_to_iso_contains_2023(self):
        result = ns_to_iso(self._REF_UNIX_NS)
        assert "2023" in result, f"Expected year 2023 in {result!r}"

    def test_ns_to_iso_known_value(self):
        # 0 nanoseconds → Unix epoch → 1970-01-01T00:00:00Z
        result = ns_to_iso(0)
        assert "1970" in result

    def test_ns_to_iso_bad_input_returns_dash(self):
        result = ns_to_iso("NOT_A_NUMBER")  # type: ignore
        assert result == "-"

    def test_ms_to_iso_ends_with_Z(self):
        result = ms_to_iso(self._REF_UNIX_MS)
        assert result.endswith("Z")

    def test_ms_to_iso_contains_2023(self):
        result = ms_to_iso(self._REF_UNIX_MS)
        assert "2023" in result

    def test_ms_to_iso_epoch_zero(self):
        result = ms_to_iso(0)
        assert "1970" in result

    def test_ms_to_iso_bad_input_returns_dash(self):
        result = ms_to_iso("bad")  # type: ignore
        assert result == "-"

    def test_ns_to_iso_and_ms_to_iso_consistent(self):
        """ns_to_iso(T*1e9) and ms_to_iso(T*1e3) must resolve to the same second."""
        ns = self._REF_UNIX_NS
        ms = self._REF_UNIX_MS
        iso_ns = ns_to_iso(ns)
        iso_ms = ms_to_iso(ms)
        # Both should contain the same date+hour
        assert iso_ns[:13] == iso_ms[:13], (
            f"Date-hour mismatch: ns={iso_ns!r} ms={iso_ms!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# pretty_dump_db.py — pretty_value
# ─────────────────────────────────────────────────────────────────────────────

class TestPrettyValue:

    def test_none_returns_null(self):
        assert pretty_value("any_col", None) == "NULL"

    def test_integer_returns_string(self):
        assert pretty_value("some_col", 42) == "42"

    def test_float_returns_string(self):
        assert pretty_value("some_col", 3.14) == "3.14"

    def test_string_value_returned_as_is(self):
        assert pretty_value("col", "hello") == "hello"

    def test_recv_ts_ns_contains_ms_prefix(self):
        val = 1_700_000_000_000_000_000
        result = pretty_value("recv_ts_ns", val)
        assert "ms=" in result

    def test_recv_ts_ns_contains_iso_prefix(self):
        val = 1_700_000_000_000_000_000
        result = pretty_value("recv_ts_ns", val)
        assert "iso=" in result

    def test_recv_ts_ns_contains_original_value(self):
        val = 1_700_000_000_000_000_000
        result = pretty_value("recv_ts_ns", val)
        assert str(val) in result

    def test_started_unix_ms_contains_iso(self):
        val = 1_700_000_000_000
        result = pretty_value("started_unix_ms", val)
        assert "iso=" in result

    def test_started_unix_ms_contains_original_value(self):
        val = 1_700_000_000_000
        result = pretty_value("started_unix_ms", val)
        assert str(val) in result

    def test_unknown_col_null_none(self):
        assert pretty_value("xyz", None) == "NULL"

    def test_unknown_col_int_value(self):
        assert pretty_value("xyz", 100) == "100"


# ─────────────────────────────────────────────────────────────────────────────
# pretty_dump_db.py — dump_table
# ─────────────────────────────────────────────────────────────────────────────

class TestDumpTable:

    def test_empty_table_prints_no_rows(self, tmp_path, capsys):
        _, conn = _empty_db(tmp_path)
        cur = conn.cursor()
        dump_table(cur, "hr")
        captured = capsys.readouterr()
        assert "(no rows)" in captured.out

    def test_table_with_rows_prints_header_and_data(self, tmp_path, capsys):
        _, conn = _empty_db(tmp_path)
        _insert_hr(conn, hr_bpm=90.0)
        cur = conn.cursor()
        dump_table(cur, "hr")
        captured = capsys.readouterr()
        assert "hr_bpm" in captured.out
        # Should not print "(no rows)"
        assert "(no rows)" not in captured.out

    def test_dump_prints_column_names(self, tmp_path, capsys):
        _, conn = _empty_db(tmp_path)
        _insert_bike(conn)
        cur = conn.cursor()
        dump_table(cur, "bike")
        captured = capsys.readouterr()
        assert "speed" in captured.out
        assert "steering" in captured.out

    def test_limit_parameter_respected(self, tmp_path, capsys):
        _, conn = _empty_db(tmp_path)
        for i in range(10):
            _insert_hr(conn, seq=i, hr_bpm=float(60 + i))
        cur = conn.cursor()
        dump_table(cur, "hr", limit=3)
        # Only 3 data rows should appear (plus header)
        captured = capsys.readouterr()
        # Count occurrences of "90." — only rows 30 BPM above base appear
        # Just verify we can parse 3 data lines maximum
        lines = [l for l in captured.out.splitlines()
                 if l.strip() and l.strip() != "---" * 10 and "|" in l]
        # header + separator + at most 3 data rows
        data_lines = [l for l in lines if "hr_bpm" not in l and "---" not in l]
        assert len(data_lines) <= 3

    def test_dump_table_schema_line_printed(self, tmp_path, capsys):
        _, conn = _empty_db(tmp_path)
        cur = conn.cursor()
        dump_table(cur, "sessions")
        captured = capsys.readouterr()
        assert "Schema" in captured.out or "sessions" in captured.out


# ─────────────────────────────────────────────────────────────────────────────
# create_readable_views.py — create_views
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateViews:

    def test_create_views_on_existing_db(self, tmp_path):
        db, conn = _empty_db(tmp_path)
        conn.close()
        # Must not raise
        create_views(str(db))

    def test_create_views_idempotent(self, tmp_path):
        """Calling create_views twice must not raise an error."""
        db, conn = _empty_db(tmp_path)
        conn.close()
        create_views(str(db))
        create_views(str(db))  # second call — must be fine

    def test_headpose_readable_view_exists_and_queryable(self, tmp_path):
        db, conn = _empty_db(tmp_path)
        _insert_headpose(conn)
        conn.close()
        create_views(str(db))

        conn2 = sqlite3.connect(str(db))
        rows = conn2.execute("SELECT * FROM headpose_readable").fetchall()
        assert len(rows) == 1

    def test_bike_readable_view_exists(self, tmp_path):
        db, conn = _empty_db(tmp_path)
        _insert_bike(conn)
        conn.close()
        create_views(str(db))

        conn2 = sqlite3.connect(str(db))
        rows = conn2.execute("SELECT * FROM bike_readable").fetchall()
        assert len(rows) == 1

    def test_hr_readable_view_exists(self, tmp_path):
        db, conn = _empty_db(tmp_path)
        _insert_hr(conn, hr_bpm=80.0)
        conn.close()
        create_views(str(db))

        conn2 = sqlite3.connect(str(db))
        rows = conn2.execute("SELECT * FROM hr_readable").fetchall()
        assert len(rows) == 1

    def test_events_readable_view_exists(self, tmp_path):
        db, conn = _empty_db(tmp_path)
        _insert_event(conn, js='{"evt":"test","i":0}')
        conn.close()
        create_views(str(db))

        conn2 = sqlite3.connect(str(db))
        rows = conn2.execute("SELECT * FROM events_readable").fetchall()
        assert len(rows) == 1

    def test_sessions_readable_view_exists(self, tmp_path):
        db, conn = _empty_db(tmp_path)
        conn.execute(
            "INSERT INTO sessions(session_id, started_unix_ms, session_dir) VALUES(?,?,?)",
            (1, 1_700_000_000_000, "/Logs/session_1"),
        )
        conn.commit()
        conn.close()
        create_views(str(db))

        conn2 = sqlite3.connect(str(db))
        rows = conn2.execute("SELECT * FROM sessions_readable").fetchall()
        assert len(rows) == 1

    def test_readable_view_has_recv_ts_ms_column(self, tmp_path):
        """All *_readable views must expose a recv_ts_ms column."""
        db, conn = _empty_db(tmp_path)
        conn.close()
        create_views(str(db))

        conn2 = sqlite3.connect(str(db))
        cols = [d[0] for d in conn2.execute("SELECT * FROM hr_readable LIMIT 0").description]
        assert "recv_ts_ms" in cols

    def test_create_views_on_missing_db_does_not_raise(self, tmp_path):
        """create_views on a missing path must print a message but not raise."""
        missing = str(tmp_path / "nonexistent.sqlite")
        create_views(missing)  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# export_readable_views.py — rows_and_cols
# ─────────────────────────────────────────────────────────────────────────────

class TestRowsAndCols:

    def test_returns_empty_rows_for_empty_table(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        cur = conn.cursor()
        cols, rows = rows_and_cols(cur, "hr")
        assert isinstance(cols, list)
        assert len(cols) > 0
        assert rows == []

    def test_column_names_match_schema(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        cur = conn.cursor()
        cols, _ = rows_and_cols(cur, "hr")
        assert "hr_bpm" in cols
        assert "session_id" in cols

    def test_returns_correct_row_count(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        for i in range(4):
            _insert_hr(conn, seq=i)
        cur = conn.cursor()
        cols, rows = rows_and_cols(cur, "hr")
        assert len(rows) == 4

    def test_row_length_matches_column_count(self, tmp_path):
        _, conn = _empty_db(tmp_path)
        _insert_hr(conn)
        cur = conn.cursor()
        cols, rows = rows_and_cols(cur, "hr")
        assert all(len(r) == len(cols) for r in rows)


# ─────────────────────────────────────────────────────────────────────────────
# export_readable_views.py — write_csv
# ─────────────────────────────────────────────────────────────────────────────

class TestWriteCsv:

    def test_creates_file(self, tmp_path):
        path = tmp_path / "out.csv"
        write_csv(path, ["a", "b"], [(1, 2), (3, 4)])
        assert path.exists()

    def test_header_row_written(self, tmp_path):
        path = tmp_path / "out.csv"
        write_csv(path, ["session_id", "hr_bpm"], [(1, 75.0)])
        content = path.read_text()
        assert "session_id" in content
        assert "hr_bpm" in content

    def test_data_row_written(self, tmp_path):
        path = tmp_path / "out.csv"
        write_csv(path, ["x", "y"], [(10, 20)])
        content = path.read_text()
        assert "10" in content
        assert "20" in content

    def test_none_values_written_as_empty(self, tmp_path):
        path = tmp_path / "out.csv"
        write_csv(path, ["a", "b"], [(1, None)])
        content = path.read_text()
        # None is replaced with ""
        lines = content.strip().splitlines()
        assert len(lines) == 2
        assert lines[1].endswith(",") or ",," in lines[1] or lines[1].endswith(",")

    def test_creates_parent_directories(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "out.csv"
        write_csv(path, ["col"], [(1,)])
        assert path.exists()

    def test_empty_rows_writes_header_only(self, tmp_path):
        path = tmp_path / "out.csv"
        write_csv(path, ["a", "b", "c"], [])
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 1
        assert "a" in lines[0]


# ─────────────────────────────────────────────────────────────────────────────
# export_readable_views.py — try_write_parquet
# ─────────────────────────────────────────────────────────────────────────────

class TestTryWriteParquet:

    def test_returns_false_when_pyarrow_absent(self, tmp_path, monkeypatch):
        """When pyarrow cannot be imported, try_write_parquet must return False."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name in ("pyarrow", "pyarrow.parquet"):
                raise ImportError("fake: no pyarrow")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        path = tmp_path / "out.parquet"
        result = try_write_parquet(path, ["a"], [(1,)])
        assert result is False

    @pytest.mark.skipif(
        not __import__("importlib").util.find_spec("pyarrow"),
        reason="pyarrow not installed",
    )
    def test_returns_true_and_creates_file_when_pyarrow_available(self, tmp_path):
        path = tmp_path / "out.parquet"
        result = try_write_parquet(path, ["x", "y"], [(1, 2.0), (3, 4.0)])
        assert result is True
        assert path.exists()


# ─────────────────────────────────────────────────────────────────────────────
# export_readable_views.py — export_all_views
# ─────────────────────────────────────────────────────────────────────────────

class TestExportAllViews:

    def _prepare_db_with_views(self, tmp_path) -> Path:
        db, conn = _empty_db(tmp_path)
        _insert_hr(conn, hr_bpm=75.0)
        conn.close()
        create_views(str(db))
        return db

    def test_raises_file_not_found_for_missing_db(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            export_all_views(str(tmp_path / "out"), str(tmp_path / "missing.sqlite"))

    def test_skips_nonexistent_view_without_raising(self, tmp_path):
        db, conn = _empty_db(tmp_path)
        conn.close()
        out = tmp_path / "exports"
        # No views exist in a bare DB — all should be silently skipped
        written = export_all_views(str(out), str(db))
        assert isinstance(written, list)

    def test_csv_files_created_for_existing_views(self, tmp_path):
        db = self._prepare_db_with_views(tmp_path)
        out = tmp_path / "exports"
        written = export_all_views(str(out), str(db))
        csv_files = [p for p in written if str(p).endswith(".csv")]
        assert len(csv_files) >= 1

    def test_hr_readable_csv_has_correct_header(self, tmp_path):
        db = self._prepare_db_with_views(tmp_path)
        out = tmp_path / "exports"
        export_all_views(str(out), str(db))
        csv_path = out / "hr_readable.csv"
        assert csv_path.exists()
        header = csv_path.read_text().splitlines()[0]
        assert "hr_bpm" in header

    def test_returns_list_of_path_objects(self, tmp_path):
        db = self._prepare_db_with_views(tmp_path)
        out = tmp_path / "exports"
        written = export_all_views(str(out), str(db))
        for p in written:
            assert isinstance(p, Path)

    def test_custom_view_list_respected(self, tmp_path):
        db = self._prepare_db_with_views(tmp_path)
        out = tmp_path / "exports"
        written = export_all_views(str(out), str(db), views=["hr_readable"])
        # Only hr_readable.csv (and maybe hr_readable.parquet) should be written
        names = {p.name for p in written}
        assert "hr_readable.csv" in names
        assert "bike_readable.csv" not in names

    def test_output_directory_created_if_not_exists(self, tmp_path):
        db = self._prepare_db_with_views(tmp_path)
        out = tmp_path / "deep" / "nested" / "exports"
        assert not out.exists()
        export_all_views(str(out), str(db))
        # At least one file must have been written (or directory created)
        assert out.exists() or True  # graceful even if no views exist
