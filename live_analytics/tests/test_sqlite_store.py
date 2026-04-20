"""
Tests for live_analytics.app.storage.sqlite_store
"""

from __future__ import annotations

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from live_analytics.app.models import ScoringResult
from live_analytics.app.storage.sqlite_store import (
    close_pool,
    end_session,
    get_recent_events,
    get_session,
    increment_record_count,
    init_db,
    insert_event,
    list_sessions,
    update_latest_scores,
    upsert_session,
)


@pytest.fixture()
def db_path(tmp_path: Path) -> Generator[Path, None, None]:
    p = tmp_path / "test.db"
    init_db(p)
    yield p
    close_pool()


class TestSqliteStore:
    def test_init_creates_tables(self, db_path: Path) -> None:
        # Should not raise
        init_db(db_path)

    def test_upsert_and_list(self, db_path: Path) -> None:
        upsert_session(db_path, "s1", 1000, "scenario_a")
        sessions = list_sessions(db_path)
        assert len(sessions) == 1
        assert sessions[0].session_id == "s1"

    def test_increment_record_count(self, db_path: Path) -> None:
        upsert_session(db_path, "s1", 1000)
        increment_record_count(db_path, "s1", 5)
        increment_record_count(db_path, "s1", 3)
        detail = get_session(db_path, "s1")
        assert detail is not None
        assert detail.record_count == 8

    def test_update_latest_scores(self, db_path: Path) -> None:
        upsert_session(db_path, "s1", 1000)
        scores = ScoringResult(stress_score=42.0, risk_score=17.5)
        update_latest_scores(db_path, "s1", scores)
        detail = get_session(db_path, "s1")
        assert detail is not None
        assert detail.latest_scores is not None
        assert detail.latest_scores.stress_score == pytest.approx(42.0)

    def test_end_session(self, db_path: Path) -> None:
        upsert_session(db_path, "s1", 1000)
        end_session(db_path, "s1", 2000)
        detail = get_session(db_path, "s1")
        assert detail is not None
        assert detail.end_unix_ms == 2000

    def test_get_nonexistent_session(self, db_path: Path) -> None:
        assert get_session(db_path, "nope") is None

    def test_insert_and_get_events(self, db_path: Path) -> None:
        upsert_session(db_path, "s1", 1000)
        insert_event(db_path, "s1", 1001, "trigger", {"type": "stop_sign"})
        insert_event(db_path, "s1", 1002, "brake", {"force": 200})
        events = get_recent_events(db_path, "s1")
        assert len(events) == 2
        assert events[0]["event_type"] == "brake"  # most recent first
