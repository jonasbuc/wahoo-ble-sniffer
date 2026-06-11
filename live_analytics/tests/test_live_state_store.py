"""
Tests for live_state CRUD helpers in sqlite_store.py.

All tests use an in-memory or tmp-file SQLite database so they are:
  - isolated  (no shared state between tests)
  - fast      (no disk I/O contention)
  - safe      (never touch the real /data/analytics.db)
"""
from __future__ import annotations

import json
import time
import pytest

from live_analytics.app.storage.sqlite_store import (
    init_db,
    upsert_live_state,
    get_live_latest,
    delete_live_state,
    get_active_session_ids,
    close_pool,
)


# ── Fixture ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    """Return a fresh per-test SQLite database path and ensure the schema is initialised."""
    db_path = tmp_path / "test_analytics.db"
    init_db(db_path)
    yield db_path
    # Release the connection-pool entries for this path so the tmp dir can be
    # cleaned up on Windows (file lock).
    close_pool()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_ms() -> int:
    return int(time.time() * 1000)


def _upsert(db, session_id: str = "sess-1", heart_rate: float = 72.0,
            speed: float | None = 25.0, ts_offset_ms: int = 0) -> int:
    """Convenience wrapper that fills in sensible defaults."""
    ts = _now_ms() + ts_offset_ms
    upsert_live_state(
        db_path=db,
        session_id=session_id,
        unix_ms=ts,
        heart_rate=heart_rate,
        speed=speed,
        scores_json=json.dumps({"hr_z": 2, "effort": 0.5}),
        updated_at_ms=ts,
    )
    return ts


# ── upsert_live_state ─────────────────────────────────────────────────────────

class TestUpsertLiveState:
    def test_insert_creates_row(self, db):
        _upsert(db, "sess-A")
        result = get_live_latest(db)
        assert result is not None
        assert result["session_id"] == "sess-A"

    def test_upsert_overwrites_existing_row(self, db):
        _upsert(db, "sess-A", heart_rate=60.0)
        _upsert(db, "sess-A", heart_rate=80.0)

        result = get_live_latest(db)
        assert result["heart_rate"] == pytest.approx(80.0)

    def test_multiple_sessions_coexist(self, db):
        _upsert(db, "sess-A")
        _upsert(db, "sess-B")

        ids = get_active_session_ids(db)
        assert set(ids) == {"sess-A", "sess-B"}

    def test_speed_can_be_none(self, db):
        ts = _now_ms()
        upsert_live_state(
            db_path=db,
            session_id="sess-no-speed",
            unix_ms=ts,
            heart_rate=55.0,
            speed=None,
            scores_json="{}",
            updated_at_ms=ts,
        )
        row = get_live_latest(db)
        assert row is not None
        assert row["speed"] is None

    def test_scores_json_round_trips(self, db):
        payload = {"hr_z": 4, "effort": 0.9, "tag": "peak"}
        ts = _now_ms()
        upsert_live_state(
            db_path=db,
            session_id="sess-json",
            unix_ms=ts,
            heart_rate=155.0,
            speed=38.5,
            scores_json=json.dumps(payload),
            updated_at_ms=ts,
        )
        row = get_live_latest(db)
        assert json.loads(row["scores_json"]) == payload


# ── get_live_latest ────────────────────────────────────────────────────────────

class TestGetLiveLatest:
    def test_returns_none_on_empty_table(self, db):
        assert get_live_latest(db) is None

    def test_returns_most_recently_updated_row(self, db):
        _upsert(db, "sess-old", ts_offset_ms=0)
        _upsert(db, "sess-new", ts_offset_ms=1000)

        result = get_live_latest(db)
        assert result["session_id"] == "sess-new"

    def test_returns_dict_with_expected_keys(self, db):
        _upsert(db, "sess-keys")
        row = get_live_latest(db)
        expected_keys = {"session_id", "unix_ms", "heart_rate", "speed",
                         "scores_json", "updated_at_ms"}
        assert expected_keys.issubset(set(row.keys()))

    def test_latest_changes_after_upsert(self, db):
        _upsert(db, "sess-X", heart_rate=70.0, ts_offset_ms=0)
        _upsert(db, "sess-X", heart_rate=90.0, ts_offset_ms=500)

        row = get_live_latest(db)
        assert row["heart_rate"] == pytest.approx(90.0)


# ── delete_live_state ─────────────────────────────────────────────────────────

class TestDeleteLiveState:
    def test_delete_removes_row(self, db):
        _upsert(db, "sess-del")
        assert get_live_latest(db) is not None

        delete_live_state(db, "sess-del")
        assert get_live_latest(db) is None

    def test_delete_nonexistent_is_idempotent(self, db):
        # Should not raise even when the session doesn't exist
        delete_live_state(db, "does-not-exist")

    def test_delete_leaves_other_sessions_intact(self, db):
        _upsert(db, "sess-keep")
        _upsert(db, "sess-gone")

        delete_live_state(db, "sess-gone")

        ids = get_active_session_ids(db)
        assert "sess-gone" not in ids
        assert "sess-keep" in ids

    def test_delete_idempotent_on_second_call(self, db):
        _upsert(db, "sess-twice")
        delete_live_state(db, "sess-twice")
        delete_live_state(db, "sess-twice")  # must not raise

    def test_latest_reflects_deletion(self, db):
        _upsert(db, "sess-only", ts_offset_ms=0)
        delete_live_state(db, "sess-only")
        assert get_live_latest(db) is None


# ── get_active_session_ids ─────────────────────────────────────────────────────

class TestGetActiveSessionIds:
    def test_empty_when_no_live_state(self, db):
        assert get_active_session_ids(db) == []

    def test_returns_all_active_ids(self, db):
        for sid in ("a", "b", "c"):
            _upsert(db, sid)
        assert set(get_active_session_ids(db)) == {"a", "b", "c"}

    def test_deleted_session_not_returned(self, db):
        _upsert(db, "active")
        _upsert(db, "ended")
        delete_live_state(db, "ended")

        ids = get_active_session_ids(db)
        assert "active" in ids
        assert "ended" not in ids

    def test_returns_list_of_strings(self, db):
        _upsert(db, "str-sess")
        ids = get_active_session_ids(db)
        assert isinstance(ids, list)
        assert all(isinstance(sid, str) for sid in ids)


# ── Cross-function integration ─────────────────────────────────────────────────

class TestLiveStateIntegration:
    def test_full_session_lifecycle(self, db):
        """upsert → read → update → read → delete → empty."""
        ts1 = _now_ms()
        upsert_live_state(db, "lifecycle", ts1, 65.0, 20.0, "{}", ts1)
        assert get_live_latest(db)["heart_rate"] == pytest.approx(65.0)

        ts2 = ts1 + 2000
        upsert_live_state(db, "lifecycle", ts2, 145.0, 35.0, "{}", ts2)
        assert get_live_latest(db)["heart_rate"] == pytest.approx(145.0)

        delete_live_state(db, "lifecycle")
        assert get_live_latest(db) is None
        assert get_active_session_ids(db) == []

    def test_concurrent_sessions_independent(self, db):
        _upsert(db, "rider-1", heart_rate=70.0, ts_offset_ms=0)
        _upsert(db, "rider-2", heart_rate=120.0, ts_offset_ms=100)

        ids = set(get_active_session_ids(db))
        assert ids == {"rider-1", "rider-2"}

        delete_live_state(db, "rider-1")
        ids_after = set(get_active_session_ids(db))
        assert ids_after == {"rider-2"}
        assert get_live_latest(db)["session_id"] == "rider-2"
