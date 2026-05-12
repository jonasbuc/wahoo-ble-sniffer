"""
Tests for PulseSessionLogger.

Covers:
- Normal start → write_pulse → close_session lifecycle
- Auto-close when a new session starts for the same participant
- write_pulse with no open session (silent drop)
- write_pulse with session_id mismatch (silent drop)
- close_session with no open session (no-op)
- Multiple participants simultaneously
- active_sessions() snapshot
- close_all() shuts all sessions cleanly
- JSONL file content (session_start / pulse / session_end records)
- _safe_filename sanitises special characters
- API router: start, end, current endpoints
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from live_analytics.app.pulse_session_logger import (
    PulseSessionLogger,
    _safe_filename,
    init_pulse_logger,
    get_pulse_logger,
)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture()
def log_dir(tmp_path: Path) -> Path:
    d = tmp_path / "pulse"
    d.mkdir()
    return d


@pytest.fixture()
def psl(log_dir: Path) -> PulseSessionLogger:
    return PulseSessionLogger(log_dir)


# ── Utility ───────────────────────────────────────────────────────────

def _read_jsonl(path: Path) -> list[dict]:
    lines = [l.strip() for l in path.read_text().splitlines() if l.strip()]
    return [json.loads(l) for l in lines]


def _find_log(log_dir: Path, participant_id: str) -> Path:
    """Return the single pulse-log file for a participant (fails if not found)."""
    files = list(log_dir.glob(f"{participant_id}_*_pulse_log.jsonl"))
    assert len(files) == 1, f"Expected 1 log file for {participant_id!r}, found {files}"
    return files[0]


# ── PulseSessionLogger unit tests ─────────────────────────────────────

class TestPulseSessionLoggerLifecycle:
    """Normal lifecycle: start → write → close."""

    def test_start_creates_file(self, psl: PulseSessionLogger, log_dir: Path):
        psl.start_session("TP_001", "session_abc")
        files = list(log_dir.glob("TP_001_*_pulse_log.jsonl"))
        assert len(files) == 1

    def test_session_start_record_written(self, psl: PulseSessionLogger, log_dir: Path):
        psl.start_session("TP_001", "s1", extra={"scenario_id": "forest"})
        log = _find_log(log_dir, "TP_001")
        rows = _read_jsonl(log)
        assert len(rows) == 1
        assert rows[0]["type"] == "session_start"
        assert rows[0]["participant_id"] == "TP_001"
        assert rows[0]["session_id"] == "s1"
        assert rows[0]["scenario_id"] == "forest"

    def test_write_pulse_appends_record(self, psl: PulseSessionLogger, log_dir: Path):
        psl.start_session("TP_001", "s1")
        psl.write_pulse("TP_001", "s1", unix_ms=1_700_000_000_000, pulse=75)
        psl.write_pulse("TP_001", "s1", unix_ms=1_700_000_000_050, pulse=76)
        rows = _read_jsonl(_find_log(log_dir, "TP_001"))
        pulse_rows = [r for r in rows if r["type"] == "pulse"]
        assert len(pulse_rows) == 2
        assert pulse_rows[0]["pulse"] == 75
        assert pulse_rows[1]["pulse"] == 76

    def test_pulse_record_count_tracked(self, psl: PulseSessionLogger):
        psl.start_session("TP_001", "s1")
        for i in range(5):
            psl.write_pulse("TP_001", "s1", unix_ms=1_000 * i, pulse=70 + i)
        active = psl.active_sessions()
        assert active["TP_001"]["pulse_records"] == 5

    def test_close_writes_session_end(self, psl: PulseSessionLogger, log_dir: Path):
        psl.start_session("TP_001", "s1")
        psl.write_pulse("TP_001", "s1", unix_ms=1_000, pulse=80)
        psl.close_session("TP_001")
        rows = _read_jsonl(_find_log(log_dir, "TP_001"))
        end = next((r for r in rows if r["type"] == "session_end"), None)
        assert end is not None
        assert end["participant_id"] == "TP_001"
        assert end["session_id"] == "s1"
        assert end["pulse_record_count"] == 1

    def test_close_removes_from_active(self, psl: PulseSessionLogger):
        psl.start_session("TP_001", "s1")
        psl.close_session("TP_001")
        assert "TP_001" not in psl.active_sessions()

    def test_extra_merged_into_session_end(self, psl: PulseSessionLogger, log_dir: Path):
        psl.start_session("TP_001", "s1")
        psl.close_session("TP_001", extra={"reason": "test_complete"})
        rows = _read_jsonl(_find_log(log_dir, "TP_001"))
        end = next(r for r in rows if r["type"] == "session_end")
        assert end["reason"] == "test_complete"


class TestEdgeCases:
    """Edge case handling."""

    def test_write_pulse_no_session_is_silent(self, psl: PulseSessionLogger, log_dir: Path):
        """write_pulse without an open session must not raise or create files."""
        psl.write_pulse("TP_GHOST", "s99", unix_ms=1_000, pulse=70)
        assert list(log_dir.glob("*.jsonl")) == []

    def test_write_pulse_session_id_mismatch_is_silent(self, psl: PulseSessionLogger, log_dir: Path):
        psl.start_session("TP_001", "s1")
        psl.write_pulse("TP_001", "WRONG_SESSION", unix_ms=1_000, pulse=70)
        rows = _read_jsonl(_find_log(log_dir, "TP_001"))
        assert all(r["type"] != "pulse" for r in rows), "No pulse should be written for wrong session_id"

    def test_close_no_session_is_noop(self, psl: PulseSessionLogger):
        """close_session on an already-closed participant must not raise."""
        psl.close_session("TP_NOBODY")  # should not raise

    def test_new_start_auto_closes_old(self, psl: PulseSessionLogger, log_dir: Path):
        psl.start_session("TP_001", "s1")
        psl.write_pulse("TP_001", "s1", unix_ms=1_000, pulse=70)
        # Start a new session — old should be auto-closed
        psl.start_session("TP_001", "s2")

        # There should now be TWO files: one for s1 (auto-closed) and one for s2
        files = list(log_dir.glob("TP_001_*_pulse_log.jsonl"))
        assert len(files) == 2

        # The older file should have a session_end record
        all_rows: list[list[dict]] = [_read_jsonl(f) for f in sorted(files)]
        session_ends = [r for rows in all_rows for r in rows if r["type"] == "session_end"]
        assert len(session_ends) == 1, "Auto-close should have written one session_end"
        assert session_ends[0]["close_reason"] == "auto_close_on_new_start"

    def test_invalid_participant_id_ignored(self, psl: PulseSessionLogger, log_dir: Path):
        psl.start_session("", "s1")  # empty participant_id — should be ignored
        assert list(log_dir.glob("*.jsonl")) == []

    def test_invalid_session_id_ignored(self, psl: PulseSessionLogger, log_dir: Path):
        psl.start_session("TP_001", "")  # empty session_id — should be ignored
        assert list(log_dir.glob("*.jsonl")) == []


class TestMultipleParticipants:
    """Multiple participants can have simultaneous active sessions."""

    def test_independent_sessions(self, psl: PulseSessionLogger, log_dir: Path):
        psl.start_session("TP_001", "s1")
        psl.start_session("TP_002", "s2")
        psl.write_pulse("TP_001", "s1", unix_ms=1_000, pulse=72)
        psl.write_pulse("TP_002", "s2", unix_ms=2_000, pulse=85)

        active = psl.active_sessions()
        assert "TP_001" in active
        assert "TP_002" in active

        psl.close_session("TP_001")
        psl.close_session("TP_002")
        assert psl.active_sessions() == {}

    def test_close_one_does_not_affect_other(self, psl: PulseSessionLogger):
        psl.start_session("TP_001", "s1")
        psl.start_session("TP_002", "s2")
        psl.close_session("TP_001")
        assert "TP_002" in psl.active_sessions()
        assert "TP_001" not in psl.active_sessions()


class TestCloseAll:
    def test_close_all_writes_end_markers(self, psl: PulseSessionLogger, log_dir: Path):
        psl.start_session("TP_001", "s1")
        psl.start_session("TP_002", "s2")
        psl.close_all(extra={"close_reason": "server_shutdown"})
        assert psl.active_sessions() == {}
        for pid in ("TP_001", "TP_002"):
            rows = _read_jsonl(_find_log(log_dir, pid))
            end = next((r for r in rows if r["type"] == "session_end"), None)
            assert end is not None, f"No session_end for {pid}"


class TestSafeFilename:
    def test_alphanum_unchanged(self):
        assert _safe_filename("TP001") == "TP001"

    def test_spaces_replaced(self):
        result = _safe_filename("Test Person 01")
        assert " " not in result

    def test_slashes_replaced(self):
        result = _safe_filename("TP/001")
        assert "/" not in result

    def test_dots_and_dashes_kept(self):
        result = _safe_filename("TP-001.alpha")
        assert result == "TP-001.alpha"

    def test_long_id_truncated(self):
        long_id = "A" * 100
        assert len(_safe_filename(long_id)) <= 64


class TestModuleSingleton:
    def test_init_returns_instance(self, tmp_path: Path):
        psl = init_pulse_logger(tmp_path / "pulse")
        assert psl is get_pulse_logger()

    def test_creates_directory(self, tmp_path: Path):
        target = tmp_path / "new" / "nested" / "pulse"
        init_pulse_logger(target)
        assert target.is_dir()


# ── API router tests ───────────────────────────────────────────────────

@pytest.fixture()
def api_client(tmp_path: Path):
    """Build a TestClient with the pulse_session router and a live PulseSessionLogger."""
    from fastapi import FastAPI
    from live_analytics.app.api_pulse_session import router
    import live_analytics.app.pulse_session_logger as psl_mod

    app = FastAPI()
    app.include_router(router)

    # Inject a fresh logger instance
    psl_mod._pulse_logger = PulseSessionLogger(tmp_path / "pulse")

    with TestClient(app) as client:
        yield client

    # Clean up singleton
    psl_mod._pulse_logger = None


class TestApiPulseSession:
    def test_start_endpoint_creates_session(self, api_client):
        resp = api_client.post("/api/pulse-session/start", json={"test_person_id": "TP_001"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "started"
        assert body["test_person_id"] == "TP_001"
        assert body["session_id"]
        assert body["log_file"].endswith("_pulse_log.jsonl")

    def test_start_with_explicit_session_id(self, api_client):
        resp = api_client.post(
            "/api/pulse-session/start",
            json={"test_person_id": "TP_002", "session_id": "custom_123"},
        )
        assert resp.status_code == 200
        assert resp.json()["session_id"] == "custom_123"

    def test_end_endpoint_closes_session(self, api_client):
        api_client.post("/api/pulse-session/start", json={"test_person_id": "TP_001", "session_id": "s1"})
        resp = api_client.post("/api/pulse-session/end", json={"test_person_id": "TP_001"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ended"
        assert resp.json()["session_id"] == "s1"

    def test_end_returns_404_when_no_session(self, api_client):
        resp = api_client.post("/api/pulse-session/end", json={"test_person_id": "NOBODY"})
        assert resp.status_code == 404

    def test_current_returns_all_active(self, api_client):
        api_client.post("/api/pulse-session/start", json={"test_person_id": "TP_001", "session_id": "sA"})
        api_client.post("/api/pulse-session/start", json={"test_person_id": "TP_002", "session_id": "sB"})
        resp = api_client.get("/api/pulse-session/current")
        assert resp.status_code == 200
        body = resp.json()
        assert body["active_session_count"] == 2
        ids = {s["participant_id"] for s in body["sessions"]}
        assert ids == {"TP_001", "TP_002"}

    def test_current_participant_endpoint(self, api_client):
        api_client.post("/api/pulse-session/start", json={"test_person_id": "TP_001", "session_id": "sX"})
        resp = api_client.get("/api/pulse-session/current/TP_001")
        assert resp.status_code == 200
        assert resp.json()["session_id"] == "sX"

    def test_current_participant_404_when_not_found(self, api_client):
        resp = api_client.get("/api/pulse-session/current/NONEXISTENT")
        assert resp.status_code == 404

    def test_503_when_logger_not_initialised(self, tmp_path: Path):
        from fastapi import FastAPI
        from live_analytics.app.api_pulse_session import router
        import live_analytics.app.pulse_session_logger as psl_mod

        app = FastAPI()
        app.include_router(router)
        psl_mod._pulse_logger = None  # simulate un-initialised

        with TestClient(app) as client:
            resp = client.post("/api/pulse-session/start", json={"test_person_id": "TP_001"})
        assert resp.status_code == 503
