"""
Tests for pulse-data persistence: sqlite_store.insert_pulse_data /
get_pulse_data, and the ws_ingest integration path.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest

from live_analytics.app.storage.sqlite_store import (
    close_pool,
    get_pulse_data,
    init_db,
    insert_pulse_data,
    upsert_session,
)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture()
def db_path(tmp_path: Path) -> Generator[Path, None, None]:
    p = tmp_path / "test.db"
    init_db(p)
    yield p
    close_pool()


@pytest.fixture()
def seeded_db(db_path: Path) -> Path:
    upsert_session(db_path, "sess-1", 1_000_000)
    return db_path


# ── insert_pulse_data ─────────────────────────────────────────────────

class TestInsertPulseData:
    def test_happy_path_stores_row(self, seeded_db: Path) -> None:
        insert_pulse_data(seeded_db, "sess-1", 1_000_100, pulse=72)
        rows = get_pulse_data(seeded_db, "sess-1")
        assert len(rows) == 1
        assert rows[0]["pulse"] == 72
        assert rows[0]["unix_ms"] == 1_000_100
        assert rows[0]["user_id"] is None

    def test_with_user_id(self, seeded_db: Path) -> None:
        insert_pulse_data(seeded_db, "sess-1", 1_000_200, pulse=85, user_id=7)
        rows = get_pulse_data(seeded_db, "sess-1")
        assert rows[0]["user_id"] == 7

    def test_multiple_samples_ordered_newest_first(self, seeded_db: Path) -> None:
        insert_pulse_data(seeded_db, "sess-1", 1_000_100, pulse=70)
        insert_pulse_data(seeded_db, "sess-1", 1_000_200, pulse=75)
        insert_pulse_data(seeded_db, "sess-1", 1_000_300, pulse=80)
        rows = get_pulse_data(seeded_db, "sess-1")
        assert [r["pulse"] for r in rows] == [80, 75, 70]

    def test_zero_pulse_ignored(self, seeded_db: Path) -> None:
        insert_pulse_data(seeded_db, "sess-1", 1_000_100, pulse=0)
        assert get_pulse_data(seeded_db, "sess-1") == []

    def test_negative_pulse_ignored(self, seeded_db: Path) -> None:
        insert_pulse_data(seeded_db, "sess-1", 1_000_100, pulse=-5)
        assert get_pulse_data(seeded_db, "sess-1") == []

    def test_db_error_propagates(self, seeded_db: Path) -> None:
        """A genuine DB failure (e.g. locked file) must propagate so callers can log it."""
        with patch(
            "live_analytics.app.storage.sqlite_store._connect",
            side_effect=sqlite3.OperationalError("disk full"),
        ):
            with pytest.raises(sqlite3.OperationalError):
                insert_pulse_data(seeded_db, "sess-1", 1_000_100, pulse=72)

    def test_limit_respected(self, seeded_db: Path) -> None:
        for i in range(10):
            insert_pulse_data(seeded_db, "sess-1", 1_000_000 + i, pulse=60 + i)
        rows = get_pulse_data(seeded_db, "sess-1", limit=3)
        assert len(rows) == 3

    def test_separate_sessions_do_not_mix(self, db_path: Path) -> None:
        upsert_session(db_path, "sess-A", 1_000_000)
        upsert_session(db_path, "sess-B", 2_000_000)
        insert_pulse_data(db_path, "sess-A", 1_000_100, pulse=65)
        insert_pulse_data(db_path, "sess-B", 2_000_100, pulse=90)
        assert len(get_pulse_data(db_path, "sess-A")) == 1
        assert len(get_pulse_data(db_path, "sess-B")) == 1
        assert get_pulse_data(db_path, "sess-A")[0]["pulse"] == 65
        assert get_pulse_data(db_path, "sess-B")[0]["pulse"] == 90


# ── Schema: table must exist after init_db ────────────────────────────

def test_pulse_data_table_created(db_path: Path) -> None:
    """init_db() must create the pulse_data table."""
    from live_analytics.app.storage.sqlite_store import _connect
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pulse_data'"
    ).fetchone()
    assert row is not None, "pulse_data table was not created by init_db()"


# ── ws_ingest integration: pulse written per batch ────────────────────

def test_ingest_batch_persists_pulse(tmp_path: Path) -> None:
    """_ingest_session_batch() must call insert_pulse_data() for HR > 0."""
    import live_analytics.app.ws_ingest as ingest
    from live_analytics.app.models import TelemetryRecord
    from live_analytics.app.storage.sqlite_store import init_db, get_pulse_data, close_pool

    db = tmp_path / "ingest_test.db"
    init_db(db)

    # Patch DB_PATH so the ingest module writes to our temp DB.
    with patch.object(ingest, "DB_PATH", db):
        # Reset module-level state so there's no leftover session data.
        ingest._windows.clear()
        ingest._record_counts.clear()
        ingest.latest_scores.clear()
        ingest.latest_records.clear()

        records = [
            TelemetryRecord(
                session_id="test-sess",
                unix_ms=1_000_000 + i * 50,
                unity_time=float(i),
                scenario_id="test",
                speed=10.0,
                heart_rate=75.0,
            )
            for i in range(5)
        ]
        ingest._ingest_session_batch("test-sess", records)

    rows = get_pulse_data(db, "test-sess")
    assert len(rows) == 1, "Expected exactly one pulse row per batch"
    assert rows[0]["pulse"] == 75
    close_pool()


def test_ingest_batch_skips_zero_hr(tmp_path: Path) -> None:
    """No pulse row must be written when all records have heart_rate == 0."""
    import live_analytics.app.ws_ingest as ingest
    from live_analytics.app.models import TelemetryRecord
    from live_analytics.app.storage.sqlite_store import init_db, get_pulse_data, close_pool

    db = tmp_path / "ingest_zero_hr.db"
    init_db(db)

    with patch.object(ingest, "DB_PATH", db):
        ingest._windows.clear()
        ingest._record_counts.clear()
        ingest.latest_scores.clear()
        ingest.latest_records.clear()

        records = [
            TelemetryRecord(
                session_id="zero-sess",
                unix_ms=1_000_000 + i * 50,
                unity_time=float(i),
                scenario_id="test",
                speed=5.0,
                heart_rate=0.0,  # missing HR
            )
            for i in range(3)
        ]
        ingest._ingest_session_batch("zero-sess", records)

    assert get_pulse_data(db, "zero-sess") == []
    close_pool()


def test_ingest_batch_db_failure_does_not_crash(tmp_path: Path) -> None:
    """A DB failure in insert_pulse_data must be logged, not re-raised."""
    import live_analytics.app.ws_ingest as ingest
    from live_analytics.app.models import TelemetryRecord
    from live_analytics.app.storage.sqlite_store import init_db, close_pool

    db = tmp_path / "ingest_fail.db"
    init_db(db)

    with patch.object(ingest, "DB_PATH", db):
        with patch.object(ingest, "insert_pulse_data", side_effect=sqlite3.OperationalError("forced")):
            ingest._windows.clear()
            ingest._record_counts.clear()
            ingest.latest_scores.clear()
            ingest.latest_records.clear()

            records = [
                TelemetryRecord(
                    session_id="fail-sess",
                    unix_ms=1_000_000,
                    unity_time=0.0,
                    scenario_id="test",
                    speed=8.0,
                    heart_rate=80.0,
                )
            ]
            # Must NOT raise — failure is logged and swallowed.
            ingest._ingest_session_batch("fail-sess", records)

    close_pool()
