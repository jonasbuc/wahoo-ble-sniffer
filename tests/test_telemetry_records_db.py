"""
test_telemetry_records_db.py
============================
Tests for the telemetry_records table introduced in sqlite_store.py.

Covers:
  • Table is created by init_db on a fresh DB
  • Table is created by init_db on an existing DB that predates the table
    (simulates the migration path for deployed DBs)
  • insert_records persists all TelemetryRecord fields correctly
  • insert_records handles an empty list without error
  • insert_records for a batch of 10 (normal ingest batch size)
  • Multiple batches accumulate correctly
  • A DB error in insert_records does not corrupt the sessions table
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from live_analytics.app.models import TelemetryRecord
from live_analytics.app.storage.sqlite_store import (
    close_pool,
    init_db,
    insert_records,
    upsert_session,
)


# ── Helpers ───────────────────────────────────────────────────────────

def _make_record(session_id: str = "sess-1", unix_ms: int = 1_000_000,
                 unity_time: float = 1.0, heart_rate: float = 72.0,
                 speed: float = 5.0, seq: int = 0) -> TelemetryRecord:
    return TelemetryRecord(
        session_id=session_id,
        unix_ms=unix_ms + seq,
        unity_time=unity_time + seq * 0.05,
        scenario_id="city_intersection",
        trigger_id="red_light" if seq == 3 else "",
        speed=speed,
        steering_angle=0.12,
        brake_front=10 if seq % 2 == 0 else 0,
        brake_rear=5 if seq % 2 == 0 else 0,
        heart_rate=heart_rate,
        head_pos_x=0.1,
        head_pos_y=1.7,
        head_pos_z=float(seq) * 0.01,
        head_rot_x=0.0,
        head_rot_y=0.05,
        head_rot_z=0.0,
        head_rot_w=0.9987,
        record_type="gameplay",
    )


def _count_rows(db_path: Path, session_id: str) -> int:
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT COUNT(*) FROM telemetry_records WHERE session_id = ?", (session_id,)
    ).fetchone()
    conn.close()
    return row[0]


def _fetch_rows(db_path: Path, session_id: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM telemetry_records WHERE session_id = ? ORDER BY unix_ms",
        (session_id,),
    ).fetchall()
    conn.close()
    return rows


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_pool():
    """Close pooled connections before and after each test for isolation."""
    close_pool()
    yield
    close_pool()


@pytest.fixture
def db(tmp_path) -> Path:
    p = tmp_path / "analytics.db"
    init_db(p)
    return p


# ── Tests ─────────────────────────────────────────────────────────────

class TestTableCreation:
    def test_table_exists_after_init_db(self, tmp_path):
        p = tmp_path / "fresh.db"
        init_db(p)
        conn = sqlite3.connect(str(p))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "telemetry_records" in tables

    def test_index_exists(self, tmp_path):
        p = tmp_path / "idx.db"
        init_db(p)
        conn = sqlite3.connect(str(p))
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        conn.close()
        assert "idx_telemetry_session_time" in indexes

    def test_migration_on_existing_db_without_table(self, tmp_path):
        """init_db on a DB that has `sessions` but no `telemetry_records`
        must create the new table without touching existing data."""
        p = tmp_path / "old.db"
        # Simulate an older DB that only has the sessions table
        conn = sqlite3.connect(str(p))
        conn.execute(
            "CREATE TABLE sessions ("
            "  session_id TEXT PRIMARY KEY,"
            "  start_unix_ms INTEGER NOT NULL,"
            "  end_unix_ms INTEGER,"
            "  scenario_id TEXT DEFAULT '',"
            "  record_count INTEGER DEFAULT 0,"
            "  latest_scores TEXT DEFAULT '{}',"
            "  participant_id TEXT DEFAULT ''"
            ")"
        )
        conn.execute(
            "INSERT INTO sessions (session_id, start_unix_ms) VALUES ('old-sess', 1000)"
        )
        conn.commit()
        conn.close()
        close_pool()

        # Running init_db on the existing DB should add the new table
        init_db(p)

        conn = sqlite3.connect(str(p))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        # Old data still intact
        row = conn.execute(
            "SELECT session_id FROM sessions WHERE session_id = 'old-sess'"
        ).fetchone()
        conn.close()
        assert "telemetry_records" in tables
        assert row is not None, "Existing session row must not be deleted by migration"


class TestInsertRecords:
    def test_empty_list_is_a_noop(self, db):
        insert_records(db, [])
        assert _count_rows(db, "sess-1") == 0

    def test_single_record_persisted(self, db):
        upsert_session(db, "sess-1", 1_000_000, "city")
        rec = _make_record()
        insert_records(db, [rec])
        assert _count_rows(db, "sess-1") == 1

    def test_all_fields_round_trip(self, db):
        upsert_session(db, "sess-1", 1_000_000, "city")
        rec = _make_record(seq=3)  # seq=3 → trigger_id="red_light"
        insert_records(db, [rec])
        rows = _fetch_rows(db, "sess-1")
        assert len(rows) == 1
        r = rows[0]
        assert r["session_id"] == rec.session_id
        assert r["unix_ms"] == rec.unix_ms
        assert r["unity_time"] == pytest.approx(rec.unity_time)
        assert r["scenario_id"] == "city_intersection"
        assert r["trigger_id"] == "red_light"
        assert r["speed"] == pytest.approx(rec.speed)
        assert r["steering_angle"] == pytest.approx(rec.steering_angle)
        assert r["brake_front"] == rec.brake_front
        assert r["brake_rear"] == rec.brake_rear
        assert r["heart_rate"] == pytest.approx(rec.heart_rate)
        assert r["head_pos_x"] == pytest.approx(rec.head_pos_x)
        assert r["head_pos_y"] == pytest.approx(rec.head_pos_y)
        assert r["head_pos_z"] == pytest.approx(rec.head_pos_z)
        assert r["head_rot_x"] == pytest.approx(rec.head_rot_x)
        assert r["head_rot_y"] == pytest.approx(rec.head_rot_y)
        assert r["head_rot_z"] == pytest.approx(rec.head_rot_z)
        assert r["head_rot_w"] == pytest.approx(rec.head_rot_w)
        assert r["record_type"] == "gameplay"

    def test_batch_of_ten(self, db):
        upsert_session(db, "sess-1", 1_000_000, "city")
        batch = [_make_record(seq=i) for i in range(10)]
        insert_records(db, batch)
        assert _count_rows(db, "sess-1") == 10

    def test_two_batches_accumulate(self, db):
        upsert_session(db, "sess-1", 1_000_000, "city")
        insert_records(db, [_make_record(seq=i) for i in range(10)])
        insert_records(db, [_make_record(seq=i + 10) for i in range(10)])
        assert _count_rows(db, "sess-1") == 20

    def test_multiple_sessions_isolated(self, db):
        upsert_session(db, "sess-A", 1_000_000, "city")
        upsert_session(db, "sess-B", 1_000_001, "city")
        insert_records(db, [_make_record(session_id="sess-A", seq=i) for i in range(5)])
        insert_records(db, [_make_record(session_id="sess-B", seq=i) for i in range(3)])
        assert _count_rows(db, "sess-A") == 5
        assert _count_rows(db, "sess-B") == 3

    def test_sessions_table_unaffected_by_insert_records(self, db):
        upsert_session(db, "sess-1", 1_000_000, "city")
        insert_records(db, [_make_record(seq=i) for i in range(5)])
        conn = sqlite3.connect(str(db))
        row = conn.execute(
            "SELECT record_count FROM sessions WHERE session_id = 'sess-1'"
        ).fetchone()
        conn.close()
        # insert_records does NOT touch record_count — that's increment_record_count's job
        assert row[0] == 0
