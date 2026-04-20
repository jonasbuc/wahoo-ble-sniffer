"""
Tests for FastAPI API endpoints – exercised via TestClient.

Tests cover:
  - /healthz
  - /api/sessions (empty, populated)
  - /api/sessions/{id} (existing, missing)
  - /api/live/latest (empty state, populated state)
  - Schema resilience (malformed latest_scores in DB)
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import pytest

from live_analytics.app.models import ScoringResult, TelemetryRecord
from live_analytics.app.storage.sqlite_store import (
    close_pool,
    get_session,
    init_db,
    update_latest_scores,
    upsert_session,
)


@pytest.fixture()
def db_path(tmp_path: Path) -> Generator[Path, None, None]:
    p = tmp_path / "api_test.db"
    init_db(p)
    yield p
    close_pool()


@pytest.fixture()
def patched_app(db_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Patch DB_PATH and module dicts before importing the FastAPI app."""
    monkeypatch.setattr("live_analytics.app.config.DB_PATH", db_path)
    monkeypatch.setattr("live_analytics.app.api_sessions.DB_PATH", db_path)

    # Clear live state
    import live_analytics.app.ws_ingest as ws
    ws.latest_scores.clear()
    ws.latest_records.clear()

    from fastapi.testclient import TestClient
    from live_analytics.app.main import app
    return TestClient(app)


class TestHealthz:
    def test_healthz_ok(self, patched_app):
        r = patched_app.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestSessionsEndpoints:
    def test_empty_sessions(self, patched_app):
        r = patched_app.get("/api/sessions")
        assert r.status_code == 200
        assert r.json() == []

    def test_sessions_populated(self, db_path, patched_app):
        upsert_session(db_path, "s1", 1000, "city")
        upsert_session(db_path, "s2", 2000, "highway")
        r = patched_app.get("/api/sessions")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 2
        # Ordered by start_unix_ms DESC
        assert data[0]["session_id"] == "s2"

    def test_session_detail_exists(self, db_path, patched_app):
        upsert_session(db_path, "s1", 1000, "city")
        r = patched_app.get("/api/sessions/s1")
        assert r.status_code == 200
        d = r.json()
        assert d["session_id"] == "s1"
        assert d["scenario_id"] == "city"

    def test_session_detail_not_found(self, patched_app):
        r = patched_app.get("/api/sessions/nonexistent")
        assert r.status_code == 404

    def test_session_with_scores(self, db_path, patched_app):
        upsert_session(db_path, "s1", 1000)
        scores = ScoringResult(stress_score=42.0, risk_score=17.5)
        update_latest_scores(db_path, "s1", scores)
        r = patched_app.get("/api/sessions/s1")
        assert r.status_code == 200
        d = r.json()
        assert d["latest_scores"]["stress_score"] == pytest.approx(42.0)

    def test_session_with_empty_scores(self, db_path, patched_app):
        """Session with default empty '{}' scores should not crash."""
        upsert_session(db_path, "s1", 1000)
        r = patched_app.get("/api/sessions/s1")
        assert r.status_code == 200
        # latest_scores can be None or an empty ScoringResult — both are fine


class TestLiveLatest:
    def test_empty_state(self, patched_app):
        r = patched_app.get("/api/live/latest")
        assert r.status_code == 200
        assert r.json() is None

    def test_with_live_data(self, patched_app, monkeypatch):
        import live_analytics.app.ws_ingest as ws

        rec = TelemetryRecord(
            session_id="s1", unix_ms=5000, unity_time=5.0,
            speed=10.0, heart_rate=80.0,
        )
        scores = ScoringResult(stress_score=30.0, risk_score=20.0)

        ws.latest_records["s1"] = rec
        ws.latest_scores["s1"] = scores

        r = patched_app.get("/api/live/latest")
        assert r.status_code == 200
        d = r.json()
        assert d["session_id"] == "s1"
        assert d["speed"] == pytest.approx(10.0)
        assert d["heart_rate"] == pytest.approx(80.0)
        assert d["scores"]["stress_score"] == pytest.approx(30.0)

    def test_with_multiple_sessions(self, patched_app, monkeypatch):
        """Should return the session with the latest unix_ms."""
        import live_analytics.app.ws_ingest as ws

        ws.latest_records["old"] = TelemetryRecord(
            session_id="old", unix_ms=1000, unity_time=1.0,
        )
        ws.latest_scores["old"] = ScoringResult()
        ws.latest_records["new"] = TelemetryRecord(
            session_id="new", unix_ms=9999, unity_time=9.0,
            speed=20.0,
        )
        ws.latest_scores["new"] = ScoringResult()

        r = patched_app.get("/api/live/latest")
        assert r.status_code == 200
        assert r.json()["session_id"] == "new"
