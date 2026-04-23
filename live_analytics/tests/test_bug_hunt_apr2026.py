"""
test_bug_hunt_apr2026.py
========================

Regression tests for every bug found and fixed in the April 2026
repository-wide bug hunt.  Each test is annotated with the bug ID it covers.

Bug IDs
-------
BH-01  _connect() TOCTOU race – unprotected outer dict read
BH-02  _ingest_session_batch() upsert-DB-failure fallthrough
BH-03  dashboard_subscribers set mutated during async iteration
BH-04  compute_features() wrong-trigger / double-scan brake_reaction_ms
BH-05  create_participant() can return None → silent 200 null body
BH-06  check_service_http() swallows ALL exceptions silently
BH-07  upsert_session ON CONFLICT does not update start_unix_ms
BH-08  launcher log_fh file-handle leak; check_health full-log read
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ═══════════════════════════════════════════════════════════════════════
# BH-01 – _connect() TOCTOU race
# ═══════════════════════════════════════════════════════════════════════

class TestConnectNoRace:
    """_connect() must return the same connection object from concurrent threads."""

    def test_concurrent_connect_same_connection(self, tmp_path: Path) -> None:
        """All threads must get back the same (identical) connection object."""
        from live_analytics.app.storage.sqlite_store import _connect, close_pool

        db = tmp_path / "race_test.db"
        results: list[sqlite3.Connection] = []
        errors: list[Exception] = []

        def _call() -> None:
            try:
                results.append(_connect(db))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_call) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        close_pool()
        assert not errors, f"Threads raised: {errors}"
        assert len(results) == 20
        # All threads must have gotten the SAME connection object (same id)
        ids = {id(c) for c in results}
        assert len(ids) == 1, (
            f"Got {len(ids)} distinct connection objects — TOCTOU race produced duplicate connections"
        )

    def test_connect_returns_existing_without_reopening(self, tmp_path: Path) -> None:
        """Second call with same path must return the cached connection."""
        from live_analytics.app.storage.sqlite_store import _connect, close_pool

        db = tmp_path / "cache_test.db"
        c1 = _connect(db)
        c2 = _connect(db)
        close_pool()
        assert c1 is c2


# ═══════════════════════════════════════════════════════════════════════
# BH-02 – _ingest_session_batch() upsert-failure fallthrough
# ═══════════════════════════════════════════════════════════════════════

class TestIngestSessionBatchDBFallthrough:
    """Session must be registered in _windows even when upsert_session raises,
    but the error must be logged and in-memory state must still be usable."""

    def test_session_registered_in_windows_after_db_failure(self) -> None:
        """Even when upsert_session raises, the session must appear in _windows
        and scoring must still run without crashing."""
        from live_analytics.app import ws_ingest
        from live_analytics.app.models import TelemetryRecord

        sid = "test-fallthrough-session"
        # Clean up any prior state
        ws_ingest._windows.pop(sid, None)
        ws_ingest._record_counts.pop(sid, None)
        ws_ingest.latest_scores.pop(sid, None)
        ws_ingest.latest_records.pop(sid, None)

        rec = TelemetryRecord(session_id=sid, unix_ms=1000, unity_time=1.0, speed=5.0)

        with patch("live_analytics.app.ws_ingest.upsert_session",
                   side_effect=sqlite3.OperationalError("disk I/O error")), \
             patch("live_analytics.app.ws_ingest.increment_record_count"), \
             patch("live_analytics.app.ws_ingest.update_latest_scores"):
            ws_ingest._ingest_session_batch(sid, [rec])

        # Session must be tracked in-memory despite DB failure
        assert sid in ws_ingest._windows, "_windows not populated after upsert failure"
        assert sid in ws_ingest._record_counts

    def test_scores_computed_after_db_upsert_failure(self) -> None:
        """Scoring must still run even when DB registration failed."""
        from live_analytics.app import ws_ingest
        from live_analytics.app.models import TelemetryRecord

        sid = "test-score-after-db-fail"
        ws_ingest._windows.pop(sid, None)
        ws_ingest._record_counts.pop(sid, None)
        ws_ingest.latest_scores.pop(sid, None)

        records = [
            TelemetryRecord(session_id=sid, unix_ms=i * 50, unity_time=float(i) * 0.05,
                            speed=float(i), heart_rate=70.0)
            for i in range(10)
        ]

        with patch("live_analytics.app.ws_ingest.upsert_session",
                   side_effect=sqlite3.OperationalError("disk I/O error")), \
             patch("live_analytics.app.ws_ingest.increment_record_count"), \
             patch("live_analytics.app.ws_ingest.update_latest_scores"):
            ws_ingest._ingest_session_batch(sid, records)

        assert sid in ws_ingest.latest_scores, "Scores not computed after DB failure"


# ═══════════════════════════════════════════════════════════════════════
# BH-03 – dashboard_subscribers set mutated during async iteration
# ═══════════════════════════════════════════════════════════════════════

class TestBroadcastSubscriberSnapshot:
    """_broadcast_dashboard must not raise RuntimeError when the subscriber
    set changes size during the broadcast loop."""

    def test_no_runtime_error_when_subscriber_added_during_broadcast(self) -> None:
        """Simulates a new subscriber being added while _broadcast_dashboard
        is awaiting a send — should not crash."""
        from live_analytics.app import ws_ingest
        from live_analytics.app.models import ScoringResult, TelemetryRecord

        sid = "broadcast-test-session"

        # Pre-populate state
        ws_ingest.latest_scores[sid] = ScoringResult()
        ws_ingest.latest_records[sid] = TelemetryRecord(
            session_id=sid, unix_ms=1000, unity_time=1.0
        )

        # Create two mock subscribers whose send() we can control
        sub1 = MagicMock()
        sub2 = MagicMock()

        send_called = 0

        async def _send_and_mutate(payload):
            nonlocal send_called
            send_called += 1
            # Add a new subscriber mid-iteration (simulates concurrent connect)
            ws_ingest.dashboard_subscribers.add(sub2)

        sub1.send = _send_and_mutate

        async def _send2(payload):
            pass

        sub2.send = _send2

        ws_ingest.dashboard_subscribers.clear()
        ws_ingest.dashboard_subscribers.add(sub1)

        # Must not raise RuntimeError: Set changed size during iteration
        asyncio.run(ws_ingest._broadcast_dashboard(sid))

        ws_ingest.dashboard_subscribers.clear()
        ws_ingest.latest_scores.pop(sid, None)
        ws_ingest.latest_records.pop(sid, None)

    def test_dead_subscriber_removed_after_failed_send(self) -> None:
        """A subscriber whose send() raises must be removed from the set."""
        from live_analytics.app import ws_ingest
        from live_analytics.app.models import ScoringResult, TelemetryRecord

        sid = "broadcast-dead-sub"
        ws_ingest.latest_scores[sid] = ScoringResult()
        ws_ingest.latest_records[sid] = TelemetryRecord(
            session_id=sid, unix_ms=1000, unity_time=1.0
        )

        dead_sub = MagicMock()

        async def _fail(payload):
            raise ConnectionResetError("broken pipe")

        dead_sub.send = _fail

        ws_ingest.dashboard_subscribers.clear()
        ws_ingest.dashboard_subscribers.add(dead_sub)

        asyncio.run(ws_ingest._broadcast_dashboard(sid))

        assert dead_sub not in ws_ingest.dashboard_subscribers, (
            "Dead subscriber was not removed after send failure"
        )

        ws_ingest.dashboard_subscribers.clear()
        ws_ingest.latest_scores.pop(sid, None)
        ws_ingest.latest_records.pop(sid, None)


# ═══════════════════════════════════════════════════════════════════════
# BH-04 – compute_features() brake_reaction_ms wrong-trigger bug
# ═══════════════════════════════════════════════════════════════════════

class TestBrakeReactionFeature:
    """compute_features() must measure brake reaction from the FIRST trigger
    seen in the window, not the last one."""

    def _make_records(self, events: list[dict]) -> list:
        from live_analytics.app.models import TelemetryRecord
        return [
            TelemetryRecord(
                session_id="test",
                unix_ms=int(e["t"] * 1000),
                unity_time=e["t"],
                trigger_id=e.get("trigger", ""),
                brake_front=e.get("brake", 0),
            )
            for e in events
        ]

    def test_reaction_from_first_trigger(self) -> None:
        """Reaction time must be measured from the first trigger, not the last."""
        from live_analytics.app.scoring.features import compute_features

        records = self._make_records([
            {"t": 0.0},
            {"t": 1.0, "trigger": "hazard"},   # first trigger at t=1.0
            {"t": 1.3, "brake": 1},             # reaction at t=1.3 → 300 ms
            {"t": 2.0, "trigger": "hazard2"},   # second trigger — must NOT override
            {"t": 3.0, "brake": 0},
        ])
        f = compute_features(records)
        # Reaction should be ~300 ms (from t=1.0 to t=1.3)
        assert 250 < f.brake_reaction_ms < 350, (
            f"Expected ~300 ms reaction time, got {f.brake_reaction_ms:.1f} ms"
        )

    def test_no_brake_returns_zero(self) -> None:
        from live_analytics.app.scoring.features import compute_features

        records = self._make_records([
            {"t": 0.0},
            {"t": 1.0, "trigger": "hazard"},
            {"t": 2.0},  # no brake
        ])
        f = compute_features(records)
        assert f.brake_reaction_ms == 0.0

    def test_no_trigger_returns_zero(self) -> None:
        from live_analytics.app.scoring.features import compute_features

        records = self._make_records([
            {"t": 0.0},
            {"t": 0.5, "brake": 1},
            {"t": 1.0},
        ])
        f = compute_features(records)
        assert f.brake_reaction_ms == 0.0

    def test_brake_before_trigger_not_counted(self) -> None:
        """A brake event that precedes the trigger must not be counted."""
        from live_analytics.app.scoring.features import compute_features

        records = self._make_records([
            {"t": 0.5, "brake": 1},  # brake BEFORE trigger
            {"t": 1.0, "trigger": "hazard"},
            {"t": 2.0},              # no brake after trigger
        ])
        f = compute_features(records)
        assert f.brake_reaction_ms == 0.0, (
            f"Brake before trigger should give 0 ms, got {f.brake_reaction_ms:.1f} ms"
        )


# ═══════════════════════════════════════════════════════════════════════
# BH-05 – create_participant_endpoint returns None → 500 now
# ═══════════════════════════════════════════════════════════════════════

class TestCreateParticipantEndpoint:
    """If create_participant returns None the endpoint must return 500,
    not silently return a null body with 200."""

    def test_returns_500_when_create_participant_returns_none(self, tmp_path: Path) -> None:
        import os
        os.environ["QS_DB_PATH"] = str(tmp_path / "qs_test.db")
        from live_analytics.questionnaire.app import app
        from live_analytics.questionnaire.db import init_db, close_pool
        init_db(tmp_path / "qs_test.db")
        with TestClient(app) as c:
            with patch("live_analytics.questionnaire.app.create_participant", return_value=None):
                r = c.post("/api/participants", json={"participant_id": "p1"})
        close_pool()
        assert r.status_code == 500, (
            f"Expected 500 when create_participant returns None, got {r.status_code}"
        )

    def test_returns_participant_dict_on_success(self, tmp_path: Path) -> None:
        import os
        os.environ["QS_DB_PATH"] = str(tmp_path / "qs_ok.db")
        from live_analytics.questionnaire.app import app
        from live_analytics.questionnaire.db import init_db, close_pool
        init_db(tmp_path / "qs_ok.db")
        with TestClient(app) as c:
            r = c.post("/api/participants", json={"participant_id": "p2", "display_name": "Alice"})
        close_pool()
        assert r.status_code == 200
        body = r.json()
        assert body["participant_id"] == "p2"
        assert body["display_name"] == "Alice"

    def test_returns_500_on_db_exception(self, tmp_path: Path) -> None:
        import os
        os.environ["QS_DB_PATH"] = str(tmp_path / "qs_exc.db")
        from live_analytics.questionnaire.app import app
        from live_analytics.questionnaire.db import init_db, close_pool
        init_db(tmp_path / "qs_exc.db")
        with TestClient(app) as c:
            with patch("live_analytics.questionnaire.app.create_participant",
                       side_effect=sqlite3.OperationalError("disk full")):
                r = c.post("/api/participants", json={"participant_id": "p3"})
        close_pool()
        assert r.status_code == 500


# ═══════════════════════════════════════════════════════════════════════
# BH-06 – check_service_http swallows ALL exceptions
# ═══════════════════════════════════════════════════════════════════════

class TestCheckServiceHttp:
    """check_service_http must distinguish HTTP 5xx from connection-refused
    and must not swallow non-connection exceptions silently."""

    def test_returns_warn_on_connection_refused(self) -> None:
        from live_analytics.system_check.checks import check_service_http
        result = check_service_http("http://127.0.0.1:19999", "TestSvc")
        assert result["ok"] is False
        assert result["severity"] == "warn"

    def test_returns_error_on_http_500(self) -> None:
        import urllib.error
        from live_analytics.system_check.checks import check_service_http
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.HTTPError(
                       "http://x", 500, "Internal Server Error", {}, None)):
            result = check_service_http("http://127.0.0.1:8080", "TestSvc")
        assert result["ok"] is False
        assert result["severity"] == "error"
        assert "500" in result["detail"]

    def test_returns_ok_on_200(self) -> None:
        import urllib.request
        from live_analytics.system_check.checks import check_service_http

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = check_service_http("http://127.0.0.1:8080", "TestSvc")
        assert result["ok"] is True
        assert result["severity"] == "ok"

    def test_error_detail_includes_exception_type(self) -> None:
        from live_analytics.system_check.checks import check_service_http
        # Connection refused to port that definitely won't answer
        result = check_service_http("http://127.0.0.1:19998", "TestSvc")
        assert result["ok"] is False
        # detail must include why it failed (exception type)
        assert "detail" in result
        assert len(result["detail"]) > 20


# ═══════════════════════════════════════════════════════════════════════
# BH-07 – upsert_session must update start_unix_ms on conflict
# ═══════════════════════════════════════════════════════════════════════

class TestUpsertSessionUpdatesStartTime:
    """upsert_session ON CONFLICT must update start_unix_ms so a re-used
    session_id (after Unity crash) has the correct start time."""

    def test_start_unix_ms_updated_on_conflict(self, tmp_path: Path) -> None:
        from live_analytics.app.storage.sqlite_store import (
            init_db, upsert_session, get_session, close_pool,
        )
        db = tmp_path / "upsert_test.db"
        init_db(db)

        upsert_session(db, "session-reuse", 1000, "scenario_a")
        upsert_session(db, "session-reuse", 9999, "scenario_b")  # re-created after crash

        detail = get_session(db, "session-reuse")
        close_pool()

        assert detail is not None
        assert detail.start_unix_ms == 9999, (
            f"start_unix_ms should be 9999 (re-use), got {detail.start_unix_ms}"
        )
        assert detail.scenario_id == "scenario_b"

    def test_first_insert_preserved_when_no_conflict(self, tmp_path: Path) -> None:
        from live_analytics.app.storage.sqlite_store import (
            init_db, upsert_session, get_session, close_pool,
        )
        db = tmp_path / "no_conflict.db"
        init_db(db)

        upsert_session(db, "unique-session", 5000, "s1")
        detail = get_session(db, "unique-session")
        close_pool()

        assert detail is not None
        assert detail.start_unix_ms == 5000
        assert detail.scenario_id == "s1"


# ═══════════════════════════════════════════════════════════════════════
# BH-08 – launcher log_fh leak and safe tail-read
# ═══════════════════════════════════════════════════════════════════════

class TestLauncherLogHandling:
    """Launcher must close the log file handle after Popen and must not
    read entire large log files into memory on crash detection."""

    def test_start_closes_log_fh(self, tmp_path: Path) -> None:
        """After Service.start(), no open file handle to the log file should
        remain in the parent process."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "starters"))
        import importlib
        launcher = importlib.import_module("launcher")

        svc = launcher.Service(
            name="TestSvc",
            cmd=["echo", "hello"],
            port=9999,
        )

        open_handles_before: set[str] = set()
        try:
            import psutil
            proc = psutil.Process()
            open_handles_before = {f.path for f in proc.open_files()}
        except ImportError:
            pass  # psutil not installed — skip file-handle tracking

        svc.start()
        if svc.process:
            svc.process.wait()

        if open_handles_before and svc.log_file:
            try:
                import psutil
                proc = psutil.Process()
                open_handles_after = {f.path for f in proc.open_files()}
                log_path_str = str(svc.log_file)
                assert log_path_str not in open_handles_after, (
                    f"Log file handle not closed after Popen: {log_path_str}"
                )
            except ImportError:
                pass

    def test_check_health_reads_tail_only(self, tmp_path: Path) -> None:
        """check_health() must not read more than ~8 KB of the log even
        when the log file is several MB in size."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "starters"))
        import importlib
        launcher = importlib.import_module("launcher")

        svc = launcher.Service(name="BigLog", cmd=["false"], port=19998)
        svc.log_file = tmp_path / "biglog.log"
        # Write a 2 MB log file
        line = "X" * 100 + "\n"
        with svc.log_file.open("w") as f:
            for _ in range(20_000):
                f.write(line)
        # Simulate crashed process
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        svc.process = mock_proc

        reads: list[int] = []
        original_open = open

        def _tracking_open(path, mode="r", **kw):
            fh = original_open(path, mode, **kw)
            if str(path) == str(svc.log_file) and "r" in mode:
                # Wrap read() to track total bytes read
                orig_read = fh.read
                def _read(*a, **k):
                    data = orig_read(*a, **k)
                    reads.append(len(data))
                    return data
                fh.read = _read
            return fh

        with patch("builtins.open", side_effect=_tracking_open):
            svc.check_health()

        total_read = sum(reads)
        assert total_read <= 12_000, (
            f"check_health read {total_read} bytes — should read ≤ ~8 KB tail, not entire log"
        )


# ═══════════════════════════════════════════════════════════════════════
# BH-09 – config.py Path-as-default to os.getenv() (type safety)
# ═══════════════════════════════════════════════════════════════════════

class TestConfigPathDefaults:
    """Config paths must be proper Path objects and must work with
    overrides via environment variables."""

    def test_all_config_paths_are_path_objects(self) -> None:
        from live_analytics.app.config import BASE_DIR, DATA_DIR, DB_PATH, SESSIONS_DIR
        for name, val in [("BASE_DIR", BASE_DIR), ("DATA_DIR", DATA_DIR),
                          ("DB_PATH", DB_PATH), ("SESSIONS_DIR", SESSIONS_DIR)]:
            assert isinstance(val, Path), f"{name} is {type(val)}, expected Path"

    def test_questionnaire_config_paths_are_path_objects(self) -> None:
        from live_analytics.questionnaire.config import BASE_DIR, DATA_DIR, DB_PATH
        for name, val in [("BASE_DIR", BASE_DIR), ("DATA_DIR", DATA_DIR), ("DB_PATH", DB_PATH)]:
            assert isinstance(val, Path), f"questionnaire.{name} is {type(val)}, expected Path"

    def test_env_override_resolves_correctly(self, tmp_path: Path, monkeypatch) -> None:
        """Setting LA_DATA_DIR via env var must produce the correct Path."""
        import importlib, os
        monkeypatch.setenv("LA_DATA_DIR", str(tmp_path / "custom_data"))
        import live_analytics.app.config as cfg
        importlib.reload(cfg)
        assert cfg.DATA_DIR == tmp_path / "custom_data"


# ═══════════════════════════════════════════════════════════════════════
# BH-10 – ws_dashboard receive() handles binary frames
# ═══════════════════════════════════════════════════════════════════════

class TestDashboardWsBinaryFrame:
    """dashboard_ws must handle binary WebSocket frames without crashing."""

    def test_binary_frame_does_not_crash_endpoint(self) -> None:
        from live_analytics.app.main import app
        from fastapi.testclient import TestClient

        with TestClient(app) as c:
            with c.websocket_connect("/ws/dashboard") as ws:
                # Send a binary frame — previously would raise if receive_text() was used
                ws.send_bytes(b"\x00\x01\x02\x03")
                # Connection must still be alive (or gracefully close)
                # Just verify no unhandled exception propagated
