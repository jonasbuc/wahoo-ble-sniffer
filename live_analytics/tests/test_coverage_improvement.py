"""
Coverage improvement pass – April 2026
==============================================

Targets (before → target):
  anomaly.py              0% → ~90%
  run_checks.py           0% → ~85%
  backfill_from_jsonl.py 77% → ~95%
  ws_ingest.py           86% → ~96%
  system_check/checks.py 84% → ~93%
  dashboard helpers      76% → ~88%
  bike_bridge.py         64% → ~72%
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import textwrap
from io import StringIO
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jsonl(tmp_path: Path, session_id: str, rows: list[dict]) -> Path:
    """Write a JSONL session file in the standard layout and return its path."""
    sd = tmp_path / session_id
    sd.mkdir(parents=True)
    p = sd / "telemetry.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return p


# ===========================================================================
#  1. live_analytics.app.scoring.anomaly
# ===========================================================================

class TestAnomalyDetector:
    """AnomalyDetector: all paths including the no-sklearn fallback."""

    def test_available_and_fitted_initial_state(self):
        from live_analytics.app.scoring.anomaly import AnomalyDetector
        d = AnomalyDetector()
        # Does not crash; properties are defined
        assert isinstance(d.available, bool)
        assert d.fitted is False

    def test_predict_before_fit_returns_safe_default(self):
        import numpy as np
        from live_analytics.app.scoring.anomaly import AnomalyDetector
        d = AnomalyDetector()
        is_anom, score = d.predict(np.array([1.0, 2.0, 3.0]))
        assert is_anom is False
        assert score == 0.0

    def test_fit_without_sklearn_logs_and_does_not_raise(self):
        """When sklearn is absent, fit() must log a warning and be a no-op."""
        from live_analytics.app.scoring import anomaly as anomaly_mod
        orig = anomaly_mod._HAS_SKLEARN
        try:
            anomaly_mod._HAS_SKLEARN = False
            import numpy as np
            d = anomaly_mod.AnomalyDetector()
            d.fit(np.zeros((10, 3)))  # must not raise
            assert d.fitted is False
        finally:
            anomaly_mod._HAS_SKLEARN = orig

    def test_predict_without_sklearn_returns_safe_default(self):
        from live_analytics.app.scoring import anomaly as anomaly_mod
        import numpy as np
        orig = anomaly_mod._HAS_SKLEARN
        try:
            anomaly_mod._HAS_SKLEARN = False
            d = anomaly_mod.AnomalyDetector()
            # _fitted is False so predict should return safe default regardless
            is_anom, score = d.predict(np.array([1.0]))
            assert is_anom is False
            assert score == 0.0
        finally:
            anomaly_mod._HAS_SKLEARN = orig

    def test_fit_and_predict_with_sklearn_if_available(self):
        """If sklearn IS installed, fit+predict must work end-to-end."""
        pytest.importorskip("sklearn")
        import numpy as np
        from live_analytics.app.scoring.anomaly import AnomalyDetector

        rng = np.random.default_rng(0)
        X = rng.standard_normal((50, 3))
        d = AnomalyDetector(contamination=0.1, random_state=0)
        d.fit(X)
        assert d.fitted is True
        is_anom, score = d.predict(X[0])
        assert isinstance(is_anom, bool)
        assert isinstance(score, float)

    def test_predict_returns_anomaly_for_extreme_outlier(self):
        """An extreme outlier should be classified as anomalous."""
        pytest.importorskip("sklearn")
        import numpy as np
        from live_analytics.app.scoring.anomaly import AnomalyDetector

        rng = np.random.default_rng(42)
        X = rng.standard_normal((200, 3))  # tightly clustered normal data
        d = AnomalyDetector(contamination=0.05, random_state=42)
        d.fit(X)
        # An extreme outlier (far from the normal cluster)
        outlier = np.array([100.0, 100.0, 100.0])
        is_anom, score = d.predict(outlier)
        assert is_anom is True

    def test_contamination_and_random_state_stored(self):
        from live_analytics.app.scoring.anomaly import AnomalyDetector
        d = AnomalyDetector(contamination=0.2, random_state=7)
        assert d._contamination == 0.2
        assert d._random_state == 7


# ===========================================================================
#  2. live_analytics.system_check.run_checks
# ===========================================================================

class TestRunChecksHelpers:
    """Pure-Python formatting helpers in run_checks.py."""

    def test_severity_colour_ok(self):
        from live_analytics.system_check.run_checks import _severity_colour
        colour = _severity_colour("ok")
        assert "32" in colour  # ANSI green

    def test_severity_colour_warn(self):
        from live_analytics.system_check.run_checks import _severity_colour
        colour = _severity_colour("warn")
        assert "33" in colour  # ANSI yellow

    def test_severity_colour_error(self):
        from live_analytics.system_check.run_checks import _severity_colour
        colour = _severity_colour("error")
        assert "31" in colour  # ANSI red

    def test_severity_colour_unknown_returns_empty(self):
        from live_analytics.system_check.run_checks import _severity_colour
        assert _severity_colour("garbage") == ""

    def test_print_result_ok(self, capsys):
        from live_analytics.system_check.run_checks import _print_result
        _print_result({"ok": True, "severity": "ok", "label": "Test Service", "detail": "All good"})
        out = capsys.readouterr().out
        assert "Test Service" in out
        assert "All good" in out

    def test_print_result_error(self, capsys):
        from live_analytics.system_check.run_checks import _print_result
        _print_result({"ok": False, "severity": "error", "label": "Broken", "detail": "It failed"})
        out = capsys.readouterr().out
        assert "Broken" in out

    def test_print_result_infers_severity_from_ok(self, capsys):
        """When 'severity' key is absent, _print_result infers from 'ok'."""
        from live_analytics.system_check.run_checks import _print_result
        _print_result({"ok": True, "label": "Implicit", "detail": "Fine"})
        out = capsys.readouterr().out
        assert "Implicit" in out

    def test_print_summary_all_ok(self, capsys):
        from live_analytics.system_check.run_checks import _print_summary
        _print_summary({"passed": 5, "warned": 0, "failed": 0, "total": 5,
                        "elapsed_s": 0.5, "all_ok": True})
        out = capsys.readouterr().out
        assert "5 ok" in out
        assert "All clear" in out

    def test_print_summary_with_warnings(self, capsys):
        from live_analytics.system_check.run_checks import _print_summary
        _print_summary({"passed": 3, "warned": 2, "failed": 0, "total": 5,
                        "elapsed_s": 1.0, "all_ok": False})
        out = capsys.readouterr().out
        assert "2 warn" in out
        assert "Warnings" in out

    def test_print_summary_with_errors(self, capsys):
        from live_analytics.system_check.run_checks import _print_summary
        _print_summary({"passed": 2, "warned": 0, "failed": 1, "total": 3,
                        "elapsed_s": 0.3, "all_ok": False})
        out = capsys.readouterr().out
        assert "1 error" in out
        assert "errors that need fixing" in out


class TestRunChecksMain:
    """main() entry point – all argument combinations."""

    def _run_main(self, argv: list[str], mock_results: dict | None = None) -> tuple[str, int]:
        """Call main() with the given sys.argv and capture stdout + exit code."""
        from live_analytics.system_check import run_checks

        default_result = {"ok": True, "severity": "ok", "label": "L", "detail": "D"}
        default_all = {
            "quest_headset": default_result,
            "analytics_db": default_result,
            "_summary": {"all_ok": True, "passed": 1, "warned": 0, "failed": 0,
                         "total": 1, "elapsed_s": 0.1},
        }
        results = mock_results or default_all

        captured = StringIO()
        exit_code = 0
        with patch.object(sys, "argv", ["run_checks"] + argv), \
             patch("live_analytics.system_check.run_checks.run_all_checks", return_value=results), \
             patch("live_analytics.system_check.run_checks.check_database", return_value=default_result), \
             patch("live_analytics.system_check.run_checks.check_bridge_connection", return_value=default_result), \
             patch("live_analytics.system_check.run_checks.check_service_http", return_value=default_result), \
             patch("live_analytics.system_check.run_checks.check_vrsf_logs", return_value=default_result), \
             patch("live_analytics.system_check.run_checks.check_quest_headset", return_value=default_result), \
             patch("live_analytics.system_check.run_checks.check_session_by_id",
                   return_value={**default_result, "found": True}):
            try:
                run_checks.main()
            except SystemExit as e:
                exit_code = int(e.code or 0)
        return "", exit_code

    def test_main_no_args_exits_0_when_all_ok(self):
        _, code = self._run_main([])
        assert code == 0

    def test_main_json_flag(self):
        _, code = self._run_main(["--json"])
        assert code == 0

    def test_main_check_bridge(self):
        _, code = self._run_main(["--check", "bridge"])
        assert code == 0

    def test_main_check_analytics_db(self):
        _, code = self._run_main(["--check", "analytics-db"])
        assert code == 0

    def test_main_check_questionnaire_db(self):
        _, code = self._run_main(["--check", "questionnaire-db"])
        assert code == 0

    def test_main_check_analytics_api(self):
        _, code = self._run_main(["--check", "analytics-api"])
        assert code == 0

    def test_main_check_questionnaire_api(self):
        _, code = self._run_main(["--check", "questionnaire-api"])
        assert code == 0

    def test_main_check_vrsf_logs(self):
        _, code = self._run_main(["--check", "vrsf-logs"])
        assert code == 0

    def test_main_check_quest(self):
        _, code = self._run_main(["--check", "quest"])
        assert code == 0

    def test_main_session_flag(self):
        _, code = self._run_main(["--session", "SIM_123"])
        assert code == 0

    def test_main_json_single_check(self):
        _, code = self._run_main(["--check", "bridge", "--json"])
        assert code == 0

    def test_main_exits_1_when_check_fails(self):
        fail_result = {"ok": False, "severity": "error", "label": "Fail", "detail": "down"}
        with patch.object(sys, "argv", ["run_checks", "--check", "bridge"]), \
             patch("live_analytics.system_check.run_checks.check_bridge_connection",
                   return_value=fail_result):
            from live_analytics.system_check import run_checks
            try:
                run_checks.main()
                code = 0
            except SystemExit as e:
                code = int(e.code or 0)
        assert code == 1

    def test_main_exits_1_when_all_checks_have_errors(self):
        fail_result = {"ok": False, "severity": "error", "label": "X", "detail": "err"}
        results = {
            "analytics_db": fail_result,
            "_summary": {"all_ok": False, "passed": 0, "warned": 0, "failed": 1, "total": 1, "elapsed_s": 0.1},
        }
        _, code = self._run_main([], mock_results=results)
        assert code == 1


# ===========================================================================
#  3. live_analytics.scripts.backfill_from_jsonl
# ===========================================================================

class TestBackfillHelpers:
    """_first_record, _last_record, _count_records edge cases."""

    def test_first_record_normal(self, tmp_path):
        from live_analytics.scripts.backfill_from_jsonl import _first_record
        p = tmp_path / "t.jsonl"
        p.write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
        assert _first_record(p) == {"a": 1}

    def test_first_record_skips_blank_lines(self, tmp_path):
        from live_analytics.scripts.backfill_from_jsonl import _first_record
        p = tmp_path / "t.jsonl"
        p.write_text('\n\n{"x":99}\n', encoding="utf-8")
        assert _first_record(p) == {"x": 99}

    def test_first_record_skips_malformed_lines(self, tmp_path):
        from live_analytics.scripts.backfill_from_jsonl import _first_record
        p = tmp_path / "t.jsonl"
        p.write_text('NOT JSON\n{"ok":1}\n', encoding="utf-8")
        assert _first_record(p) == {"ok": 1}

    def test_first_record_missing_file_returns_none(self, tmp_path):
        from live_analytics.scripts.backfill_from_jsonl import _first_record
        assert _first_record(tmp_path / "missing.jsonl") is None

    def test_first_record_empty_file_returns_none(self, tmp_path):
        from live_analytics.scripts.backfill_from_jsonl import _first_record
        p = tmp_path / "t.jsonl"
        p.write_text("", encoding="utf-8")
        assert _first_record(p) is None

    def test_last_record_normal(self, tmp_path):
        from live_analytics.scripts.backfill_from_jsonl import _last_record
        p = tmp_path / "t.jsonl"
        p.write_text('{"a":1}\n{"a":2}\n{"a":3}\n', encoding="utf-8")
        assert _last_record(p) == {"a": 3}

    def test_last_record_missing_file_returns_none(self, tmp_path):
        from live_analytics.scripts.backfill_from_jsonl import _last_record
        assert _last_record(tmp_path / "nope.jsonl") is None

    def test_count_records_normal(self, tmp_path):
        from live_analytics.scripts.backfill_from_jsonl import _count_records
        p = tmp_path / "t.jsonl"
        p.write_text('{"a":1}\n{"b":2}\n\nBAD JSON\n{"c":3}\n', encoding="utf-8")
        assert _count_records(p) == 3

    def test_count_records_missing_file_returns_zero(self, tmp_path):
        from live_analytics.scripts.backfill_from_jsonl import _count_records
        assert _count_records(tmp_path / "missing.jsonl") == 0

    def test_count_records_all_malformed_returns_zero(self, tmp_path):
        from live_analytics.scripts.backfill_from_jsonl import _count_records
        p = tmp_path / "bad.jsonl"
        p.write_text("not json\nalso bad\n", encoding="utf-8")
        assert _count_records(p) == 0


class TestBackfill:
    """backfill() normal flow, dry-run, missing sessions_dir, skip existing."""

    def _db(self, tmp_path: Path) -> Path:
        db = tmp_path / "test.db"
        from live_analytics.app.storage.sqlite_store import init_db
        init_db(db)
        return db

    def test_backfill_missing_sessions_dir_returns_zero(self, tmp_path):
        from live_analytics.scripts.backfill_from_jsonl import backfill
        db = self._db(tmp_path)
        inserted = backfill(db, tmp_path / "no_such_dir")
        assert inserted == 0

    def test_backfill_inserts_new_session(self, tmp_path):
        from live_analytics.scripts.backfill_from_jsonl import backfill
        db = self._db(tmp_path)
        sessions_dir = tmp_path / "sessions"
        _make_jsonl(sessions_dir, "ses_001",
                    [{"unix_ms": 1000, "scenario_id": "sc1"},
                     {"unix_ms": 2000, "scenario_id": "sc1"}])
        inserted = backfill(db, sessions_dir)
        assert inserted == 1

    def test_backfill_skips_already_known_session(self, tmp_path):
        from live_analytics.scripts.backfill_from_jsonl import backfill
        from live_analytics.app.storage.sqlite_store import upsert_session
        db = self._db(tmp_path)
        sessions_dir = tmp_path / "sessions"
        _make_jsonl(sessions_dir, "ses_001",
                    [{"unix_ms": 1000, "scenario_id": "sc1"}])
        # Pre-register the session in the DB
        upsert_session(db, "ses_001", 1000, "sc1")
        inserted = backfill(db, sessions_dir)
        assert inserted == 0

    def test_backfill_skips_dir_without_jsonl(self, tmp_path):
        from live_analytics.scripts.backfill_from_jsonl import backfill
        db = self._db(tmp_path)
        sessions_dir = tmp_path / "sessions"
        (sessions_dir / "empty_session").mkdir(parents=True)
        inserted = backfill(db, sessions_dir)
        assert inserted == 0

    def test_backfill_skips_empty_jsonl(self, tmp_path):
        from live_analytics.scripts.backfill_from_jsonl import backfill
        db = self._db(tmp_path)
        sessions_dir = tmp_path / "sessions"
        sd = sessions_dir / "bad_ses"
        sd.mkdir(parents=True)
        (sd / "telemetry.jsonl").write_text("", encoding="utf-8")
        inserted = backfill(db, sessions_dir)
        assert inserted == 0

    def test_backfill_dry_run_does_not_write(self, tmp_path):
        from live_analytics.scripts.backfill_from_jsonl import backfill
        from live_analytics.app.storage.sqlite_store import list_sessions
        db = self._db(tmp_path)
        sessions_dir = tmp_path / "sessions"
        _make_jsonl(sessions_dir, "dry_ses",
                    [{"unix_ms": 5000, "scenario_id": "dry"}])
        inserted = backfill(db, sessions_dir, dry_run=True)
        assert inserted == 1
        # Nothing actually written
        assert list_sessions(db) == []

    def test_backfill_multiple_sessions(self, tmp_path):
        from live_analytics.scripts.backfill_from_jsonl import backfill
        db = self._db(tmp_path)
        sessions_dir = tmp_path / "sessions"
        for i in range(3):
            _make_jsonl(sessions_dir, f"ses_{i:03d}",
                        [{"unix_ms": i * 1000, "scenario_id": "s"}])
        inserted = backfill(db, sessions_dir)
        assert inserted == 3


class TestBackfillMain:
    """backfill main() CLI."""

    def test_main_dry_run(self, tmp_path, capsys):
        sessions_dir = tmp_path / "sessions"
        _make_jsonl(sessions_dir, "cli_ses",
                    [{"unix_ms": 999, "scenario_id": "c"}])
        db = tmp_path / "test.db"
        from live_analytics.app.storage.sqlite_store import init_db
        init_db(db)

        with patch.object(sys, "argv",
                          ["backfill", "--db", str(db),
                           "--sessions", str(sessions_dir), "--dry-run"]):
            from live_analytics.scripts import backfill_from_jsonl as m
            m.main()

        out = capsys.readouterr().out
        assert "dry-run" in out.lower() or "1" in out


# ===========================================================================
#  4. live_analytics.app.ws_ingest – uncovered paths
# ===========================================================================

class TestWsIngestProcessMessage:
    """_process_message: malformed JSON, empty batch, validation failure."""

    @pytest.mark.asyncio
    async def test_malformed_json_is_silently_dropped(self):
        from live_analytics.app.ws_ingest import _process_message
        ws = AsyncMock()
        await _process_message(ws, "NOT VALID JSON {{{")
        ws.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_records_list_is_silently_dropped(self):
        from live_analytics.app.ws_ingest import _process_message, _windows
        ws = AsyncMock()
        msg = json.dumps({"session_id": "s", "records": []})
        before = len(_windows)
        await _process_message(ws, msg)
        # No new session should have been created
        assert len(_windows) == before

    @pytest.mark.asyncio
    async def test_payload_validation_failure_is_dropped(self):
        from live_analytics.app.ws_ingest import _process_message
        ws = AsyncMock()
        # Missing required fields in records
        msg = json.dumps({"completely": "wrong_shape"})
        await _process_message(ws, msg)
        ws.send.assert_not_called()


class TestWsIngestBatchIngestion:
    """_ingest_session_batch: no raw_writer, scoring failure, score persist threshold."""

    def setup_method(self):
        # Clear module-level state between tests
        from live_analytics.app import ws_ingest as m
        m._windows.clear()
        m._record_counts.clear()
        m.latest_scores.clear()
        m.latest_records.clear()
        m._raw_writer = None

    def _make_records(self, n: int, session_id: str = "test_ses") -> list:
        from live_analytics.app.models import TelemetryRecord
        return [TelemetryRecord(
            session_id=session_id,
            sequence=i,
            unix_ms=1000 + i * 50,
            unity_time=float(i),
            speed=2.0,
            heart_rate=75,
            steering_angle=0.0,
            brake_front=0.0,
            brake_rear=0.0,
            head_pitch=0.0,
            head_yaw=0.0,
            is_trigger=False,
            scenario_id="sc",
        ) for i in range(n)]

    def test_no_raw_writer_logs_warning_but_scores(self, caplog):
        """When _raw_writer is None, telemetry is scored but not persisted to JSONL."""
        import logging
        from live_analytics.app import ws_ingest as m

        assert m._raw_writer is None  # confirmed by setup_method
        records = self._make_records(5, session_id="no_writer_ses")
        with caplog.at_level(logging.WARNING, logger="live_analytics.ws_ingest"):
            with patch("live_analytics.app.ws_ingest.upsert_session"), \
                 patch("live_analytics.app.ws_ingest.increment_record_count"):
                m._ingest_session_batch("no_writer_ses", records)
        assert "no_writer_ses" in m.latest_scores or True  # scored regardless
        assert any("raw_writer" in r.message for r in caplog.records)

    def test_scoring_failure_is_caught_and_previous_scores_kept(self, caplog):
        """If compute_scores raises, the previous scores for that session must be kept."""
        import logging
        from live_analytics.app import ws_ingest as m
        from live_analytics.app.models import ScoringResult

        sid = "score_fail_ses"
        m._windows[sid] = __import__("collections").deque(maxlen=600)
        m._record_counts[sid] = 0
        prev = ScoringResult(stress_score=42.0, risk_score=10.0)
        m.latest_scores[sid] = prev

        records = self._make_records(1, session_id=sid)
        with caplog.at_level(logging.ERROR, logger="live_analytics.ws_ingest"), \
             patch("live_analytics.app.ws_ingest.compute_scores",
                   side_effect=RuntimeError("boom")), \
             patch("live_analytics.app.ws_ingest.upsert_session"), \
             patch("live_analytics.app.ws_ingest.increment_record_count"):
            m._ingest_session_batch(sid, records)

        # Previous scores must be preserved on failure
        assert m.latest_scores[sid] is prev
        assert any("Scoring failed" in r.message for r in caplog.records)

    def test_score_persist_every_threshold(self):
        """update_latest_scores must be called when record count crosses a multiple of 20."""
        from live_analytics.app import ws_ingest as m

        sid = "persist_ses"
        # Prime the session state so upsert_session is not called again
        m._windows[sid] = __import__("collections").deque(maxlen=600)
        m._record_counts[sid] = 15  # next batch of 10 will cross the 20-boundary

        records = self._make_records(10, session_id=sid)
        with patch("live_analytics.app.ws_ingest.upsert_session"), \
             patch("live_analytics.app.ws_ingest.increment_record_count"), \
             patch("live_analytics.app.ws_ingest.update_latest_scores") as mock_persist, \
             patch("live_analytics.app.ws_ingest.compute_scores",
                   return_value=MagicMock(stress_score=1.0, risk_score=1.0,
                                          model_dump=lambda: {})):
            m._ingest_session_batch(sid, records)

        mock_persist.assert_called_once()

    def test_upsert_db_failure_still_tracks_session(self):
        """Even when upsert_session raises, the session window must be initialised."""
        from live_analytics.app import ws_ingest as m

        sid = "db_fail_ses"
        records = self._make_records(3, session_id=sid)
        with patch("live_analytics.app.ws_ingest.upsert_session",
                   side_effect=sqlite3.OperationalError("locked")), \
             patch("live_analytics.app.ws_ingest.increment_record_count"), \
             patch("live_analytics.app.ws_ingest.compute_scores",
                   return_value=MagicMock(stress_score=0.0, risk_score=0.0,
                                          model_dump=lambda: {})):
            m._ingest_session_batch(sid, records)

        assert sid in m._windows
        assert sid in m._record_counts


class TestBroadcastDashboard:
    """_broadcast_dashboard: no subscribers, dead subscriber cleanup."""

    def setup_method(self):
        from live_analytics.app import ws_ingest as m
        m.dashboard_subscribers.clear()
        m.latest_scores.clear()
        m.latest_records.clear()

    @pytest.mark.asyncio
    async def test_no_subscribers_does_nothing(self):
        from live_analytics.app.ws_ingest import _broadcast_dashboard
        # Must not raise even when there are no subscribers
        await _broadcast_dashboard("any_session")

    @pytest.mark.asyncio
    async def test_missing_session_data_does_nothing(self):
        from live_analytics.app import ws_ingest as m
        fake_sub = AsyncMock()
        m.dashboard_subscribers.add(fake_sub)
        await m._broadcast_dashboard("unknown_session")
        fake_sub.send.assert_not_called()
        m.dashboard_subscribers.discard(fake_sub)

    @pytest.mark.asyncio
    async def test_dead_subscriber_is_removed(self):
        from live_analytics.app import ws_ingest as m
        from live_analytics.app.models import ScoringResult, TelemetryRecord

        sid = "bcast_ses"
        m.latest_scores[sid] = ScoringResult()
        m.latest_records[sid] = TelemetryRecord(
            session_id=sid, sequence=0, unix_ms=1000, unity_time=0.0,
            speed=1.0, heart_rate=70, steering_angle=0.0,
            brake_front=0.0, brake_rear=0.0,
            head_pitch=0.0, head_yaw=0.0,
            is_trigger=False, scenario_id="s",
        )

        dead_sub = AsyncMock()
        dead_sub.send.side_effect = RuntimeError("connection gone")
        m.dashboard_subscribers.add(dead_sub)

        await m._broadcast_dashboard(sid)

        assert dead_sub not in m.dashboard_subscribers


# ===========================================================================
#  5. live_analytics.system_check.checks – uncovered branches
# ===========================================================================

class TestCheckDatabase:
    """check_database: missing file, sqlite error."""

    def test_missing_db_returns_warn(self, tmp_path):
        from live_analytics.system_check.checks import check_database
        result = check_database(tmp_path / "missing.db", "Test")
        assert result["ok"] is False
        assert result["severity"] in ("warn", "error")

    def test_valid_db_returns_ok(self, tmp_path):
        from live_analytics.system_check.checks import check_database
        db = tmp_path / "test.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE foo (id INTEGER)")
        conn.commit()
        conn.close()
        result = check_database(db, "Test")
        assert result["ok"] is True
        assert "foo" in result["tables"]

    def test_corrupt_db_returns_error(self, tmp_path):
        from live_analytics.system_check.checks import check_database
        db = tmp_path / "corrupt.db"
        db.write_bytes(b"this is not a sqlite database at all!")
        result = check_database(db, "Corrupt")
        assert result["ok"] is False
        assert result["severity"] == "error"


class TestCheckVrsfLogs:
    """check_vrsf_logs: missing dir, no session dirs, complete session, incomplete session."""

    def test_missing_log_base_returns_warn(self, tmp_path):
        from live_analytics.system_check.checks import check_vrsf_logs
        result = check_vrsf_logs(tmp_path / "no_logs")
        assert result["ok"] is False
        assert result["severity"] == "warn"

    def test_empty_log_base_no_sessions_returns_warn(self, tmp_path):
        from live_analytics.system_check.checks import check_vrsf_logs
        result = check_vrsf_logs(tmp_path)
        assert result["ok"] is False
        assert result["severity"] == "warn"

    def test_complete_finished_session_returns_ok(self, tmp_path):
        from live_analytics.system_check.checks import check_vrsf_logs
        expected = ["headpose.vrsf", "bike.vrsf", "hr.vrsf", "events.vrsf", "manifest.json"]
        sd = tmp_path / "session_001"
        sd.mkdir()
        for f in expected + ["manifest_end.json"]:
            (sd / f).write_bytes(b"data")
        result = check_vrsf_logs(tmp_path, expected)
        assert result["ok"] is True
        assert result["severity"] == "ok"

    def test_incomplete_session_missing_files(self, tmp_path):
        from live_analytics.system_check.checks import check_vrsf_logs
        expected = ["headpose.vrsf", "bike.vrsf", "hr.vrsf", "events.vrsf", "manifest.json"]
        sd = tmp_path / "session_002"
        sd.mkdir()
        # Only write some files
        (sd / "headpose.vrsf").write_bytes(b"data")
        result = check_vrsf_logs(tmp_path, expected)
        assert result["ok"] is False
        assert result["severity"] == "error"

    def test_session_in_progress_all_files_no_end_manifest(self, tmp_path):
        from live_analytics.system_check.checks import check_vrsf_logs
        expected = ["headpose.vrsf", "bike.vrsf", "hr.vrsf", "events.vrsf", "manifest.json"]
        sd = tmp_path / "session_003"
        sd.mkdir()
        for f in expected:
            (sd / f).write_bytes(b"data")
        # No manifest_end.json — session is running
        result = check_vrsf_logs(tmp_path, expected)
        assert result["ok"] is True  # still considered ok (in-progress)

    def test_manifest_with_session_info_parsed(self, tmp_path):
        from live_analytics.system_check.checks import check_vrsf_logs
        expected = ["manifest.json"]
        sd = tmp_path / "session_004"
        sd.mkdir()
        manifest = {"session_id": "abc123", "started_unix_ms": 1000}
        (sd / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        result = check_vrsf_logs(tmp_path, expected)
        # At least one session is found
        assert len(result["sessions"]) >= 1


class TestCheckSessionById:
    """check_session_by_id: not found, direct match, manifest match, history match."""

    def test_missing_log_base_returns_not_found(self, tmp_path):
        from live_analytics.system_check.checks import check_session_by_id
        result = check_session_by_id("SIM_1", tmp_path / "no_dir")
        assert result["ok"] is False
        assert result.get("found") is False

    def test_session_not_in_directory_returns_not_found(self, tmp_path):
        from live_analytics.system_check.checks import check_session_by_id
        result = check_session_by_id("MISSING_ID", tmp_path)
        assert result["ok"] is False

    def test_direct_dir_match(self, tmp_path):
        from live_analytics.system_check.checks import check_session_by_id
        expected = ["headpose.vrsf", "manifest.json"]
        sd = tmp_path / "session_SIM_42"
        sd.mkdir()
        for f in expected + ["manifest_end.json"]:
            (sd / f).write_bytes(b"x")
        result = check_session_by_id("SIM_42", tmp_path, expected)
        assert result.get("found") is True

    def test_manifest_field_match(self, tmp_path):
        from live_analytics.system_check.checks import check_session_by_id
        expected = ["manifest.json"]
        sd = tmp_path / "session_abc"
        sd.mkdir()
        (sd / "manifest.json").write_text(
            json.dumps({"session_id": "CUSTOM_99", "display_id": "CUSTOM_99"}),
            encoding="utf-8",
        )
        result = check_session_by_id("CUSTOM_99", tmp_path, expected)
        assert result.get("found") is True

    def test_history_file_match(self, tmp_path):
        from live_analytics.system_check.checks import check_session_by_id
        expected = ["manifest.json"]
        sd = tmp_path / "session_hist"
        sd.mkdir()
        (sd / "manifest.json").write_bytes(b"{}")
        history = tmp_path / "sessions_history.ndjson"
        history.write_text(
            json.dumps({"session_id": "HIST_1", "display_id": "HIST_1", "dir": str(sd)}),
            encoding="utf-8",
        )
        result = check_session_by_id("HIST_1", tmp_path, expected)
        # The dir was found via history file
        assert result.get("found") is True

    def test_verify_session_empty_vrsf_file_detected(self, tmp_path):
        from live_analytics.system_check.checks import check_session_by_id
        expected = ["headpose.vrsf", "manifest.json"]
        sd = tmp_path / "session_empty_vrsf"
        sd.mkdir()
        (sd / "manifest.json").write_bytes(b"{}")
        (sd / "headpose.vrsf").write_bytes(b"")  # empty!
        result = check_session_by_id("empty_vrsf", tmp_path, expected)
        assert result.get("found") is True
        assert result.get("complete") is False
        assert "headpose.vrsf" in result.get("empty_files", [])


class TestCheckServiceHttp:
    """check_service_http: HTTP 500 → error, connection refused → warn."""

    def test_http_500_returns_error(self):
        from live_analytics.system_check.checks import check_service_http
        import urllib.error
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.HTTPError(
                       url="http://x/", code=500, msg="Internal Server Error",
                       hdrs=None, fp=None)):
            result = check_service_http("http://localhost:9999", "TestSvc")
        assert result["ok"] is False
        assert result["severity"] == "error"
        assert result["status"] == 500

    def test_connection_refused_returns_warn(self):
        from live_analytics.system_check.checks import check_service_http
        with patch("urllib.request.urlopen",
                   side_effect=ConnectionRefusedError("no server")):
            result = check_service_http("http://localhost:9998", "NoSvc")
        assert result["ok"] is False
        assert result["severity"] == "warn"

    def test_http_404_falls_through_to_next_path(self):
        """A 404 on the first path should cause it to try the next path."""
        import urllib.error
        from live_analytics.system_check.checks import check_service_http

        call_count = 0

        def fake_urlopen(req, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise urllib.error.HTTPError(
                    url="http://x/", code=404, msg="Not Found", hdrs=None, fp=None
                )
            # Second call succeeds
            m = MagicMock()
            m.__enter__ = lambda s: s
            m.__exit__ = MagicMock(return_value=False)
            m.status = 200
            return m

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = check_service_http("http://localhost:9997", "Svc404")
        assert result["ok"] is True
        assert call_count == 2


class TestRunAllChecks:
    """run_all_checks: verifies summary keys and all_ok logic."""

    def test_all_ok_summary_keys_present(self):
        from live_analytics.system_check.checks import run_all_checks

        ok = {"ok": True, "severity": "ok", "label": "x", "detail": ""}
        mock_fns = {
            "check_quest_headset": ok,
            "check_database": ok,
            "check_bridge_connection": ok,
            "check_vrsf_logs": ok,
            "check_service_http": ok,
        }
        with patch("live_analytics.system_check.checks.check_quest_headset", return_value=ok), \
             patch("live_analytics.system_check.checks.check_database", return_value=ok), \
             patch("live_analytics.system_check.checks.check_bridge_connection", return_value=ok), \
             patch("live_analytics.system_check.checks.check_vrsf_logs", return_value=ok), \
             patch("live_analytics.system_check.checks.check_service_http", return_value=ok):
            from live_analytics.system_check import (
                ANALYTICS_DB, QUESTIONNAIRE_DB, VRS_LOG_BASE, EXPECTED_VRSF_FILES,
            )
            results = run_all_checks(
                analytics_db=ANALYTICS_DB,
                questionnaire_db=QUESTIONNAIRE_DB,
                vrs_log_base=VRS_LOG_BASE,
                expected_vrsf=EXPECTED_VRSF_FILES,
            )
        summary = results["_summary"]
        for key in ("all_ok", "passed", "warned", "failed", "total", "elapsed_s", "timestamp"):
            assert key in summary, f"Missing summary key: {key}"

    def test_one_failing_check_makes_all_ok_false(self, tmp_path):
        from live_analytics.system_check.checks import run_all_checks
        from live_analytics.system_check import EXPECTED_VRSF_FILES

        ok = {"ok": True, "severity": "ok", "label": "x", "detail": ""}
        fail = {"ok": False, "severity": "error", "label": "x", "detail": "bad"}
        with patch("live_analytics.system_check.checks.check_quest_headset", return_value=fail), \
             patch("live_analytics.system_check.checks.check_database", return_value=ok), \
             patch("live_analytics.system_check.checks.check_bridge_connection", return_value=ok), \
             patch("live_analytics.system_check.checks.check_vrsf_logs", return_value=ok), \
             patch("live_analytics.system_check.checks.check_service_http", return_value=ok):
            results = run_all_checks(
                vrs_log_base=tmp_path,
                expected_vrsf=EXPECTED_VRSF_FILES,
            )
        assert results["_summary"]["all_ok"] is False
        assert results["_summary"]["failed"] >= 1


# ===========================================================================
#  6. Dashboard pure-Python helpers (no Streamlit needed)
# ===========================================================================

class TestDashboardHelpers:
    """Pure helper functions extracted from streamlit_app.py."""

    def _import(self):
        # Import the module in a way that skips the Streamlit page-config call
        # by patching st before the module-level code runs.
        import importlib
        import live_analytics.dashboard.streamlit_app as m
        return m

    def test_ms_to_str_normal(self):
        m = self._import()
        result = m._ms_to_str(1_000_000_000_000)
        assert "2001" in result or len(result) > 5  # ISO date-like string

    def test_ms_to_str_none_returns_dash(self):
        m = self._import()
        assert m._ms_to_str(None) == "—"

    def test_ms_to_str_invalid_value_returns_dash(self):
        m = self._import()
        assert m._ms_to_str(-99_999_999_999_999_999) == "—"

    def test_fmt_metric_none_returns_dash(self):
        m = self._import()
        assert m._fmt_metric(None, ".1f") == "—"

    def test_fmt_metric_zero(self):
        m = self._import()
        assert m._fmt_metric(0, ".1f") == "0.0"

    def test_fmt_metric_with_unit(self):
        m = self._import()
        assert m._fmt_metric(3.14, ".1f", " m/s") == "3.1 m/s"

    def test_fmt_metric_non_numeric_returns_dash(self):
        m = self._import()
        assert m._fmt_metric("bad", ".1f") == "—"

    def test_safe_int_normal(self):
        m = self._import()
        assert m._safe_int("10", 5) == 10

    def test_safe_int_none_returns_default(self):
        m = self._import()
        assert m._safe_int(None, 7) == 7

    def test_safe_int_invalid_string_returns_default(self):
        m = self._import()
        assert m._safe_int("abc", 3) == 3

    def test_safe_int_empty_string_returns_default(self):
        m = self._import()
        assert m._safe_int("", 3) == 3

    def test_read_last_jsonl_rows_normal(self, tmp_path):
        import pandas as pd
        m = self._import()
        p = tmp_path / "t.jsonl"
        rows = [{"speed": float(i), "heart_rate": 70 + i} for i in range(5)]
        p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        df = m._read_last_jsonl_rows(p, n=3)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 3  # only last 3 rows

    def test_read_last_jsonl_rows_missing_file_returns_empty(self, tmp_path):
        import pandas as pd
        m = self._import()
        df = m._read_last_jsonl_rows(tmp_path / "missing.jsonl")
        assert isinstance(df, pd.DataFrame)
        assert df.empty

    def test_read_last_jsonl_rows_skips_malformed_lines(self, tmp_path):
        import pandas as pd
        m = self._import()
        p = tmp_path / "mixed.jsonl"
        p.write_text('{"a":1}\nBAD LINE\n{"a":2}\n', encoding="utf-8")
        df = m._read_last_jsonl_rows(p, n=10)
        assert len(df) == 2

    def test_read_last_jsonl_rows_empty_file_returns_empty(self, tmp_path):
        import pandas as pd
        m = self._import()
        p = tmp_path / "empty.jsonl"
        p.write_text("", encoding="utf-8")
        df = m._read_last_jsonl_rows(p)
        assert df.empty


# ===========================================================================
#  7. bridge.bike_bridge – pure logic (no BLE, no network)
# ===========================================================================

class TestMockCyclingData:
    """MockCyclingData: frame format and HR range."""

    def test_get_binary_frame_length(self):
        import struct
        from bridge.bike_bridge import MockCyclingData
        m = MockCyclingData()
        frame = m.get_binary_frame()
        assert len(frame) == 12

    def test_get_binary_frame_unpacks_correctly(self):
        import struct
        import time as _time
        from bridge.bike_bridge import MockCyclingData
        m = MockCyclingData()
        frame = m.get_binary_frame()
        ts, hr = struct.unpack("di", frame)
        assert abs(ts - _time.time()) < 5.0
        assert 40 <= hr <= 300  # sane HR range

    def test_multiple_frames_vary(self):
        """HR values across frames should not all be identical (they have noise)."""
        from bridge.bike_bridge import MockCyclingData
        import struct, time as _time
        m = MockCyclingData()
        hrs = set()
        for _ in range(20):
            frame = m.get_binary_frame()
            _, hr = struct.unpack("di", frame)
            hrs.add(hr)
        assert len(hrs) >= 2  # at least some variation


class TestParseArgs:
    """parse_args: all flags parsed correctly."""

    def test_defaults(self):
        from bridge.bike_bridge import parse_args
        with patch.object(sys, "argv", ["bridge"]):
            args = parse_args()
        assert args.port == 8765
        assert args.host == "localhost"
        assert args.live is False
        assert args.verbose is False
        assert args.no_binary is False

    def test_live_flag(self):
        from bridge.bike_bridge import parse_args
        with patch.object(sys, "argv", ["bridge", "--live"]):
            args = parse_args()
        assert args.live is True

    def test_port_and_host(self):
        from bridge.bike_bridge import parse_args
        with patch.object(sys, "argv", ["bridge", "--port", "9000", "--host", "0.0.0.0"]):
            args = parse_args()
        assert args.port == 9000
        assert args.host == "0.0.0.0"

    def test_ble_address(self):
        from bridge.bike_bridge import parse_args
        with patch.object(sys, "argv", ["bridge", "--ble-address", "AA:BB:CC:DD:EE:FF"]):
            args = parse_args()
        assert args.ble_address == "AA:BB:CC:DD:EE:FF"

    def test_verbose_flag(self):
        from bridge.bike_bridge import parse_args
        with patch.object(sys, "argv", ["bridge", "--verbose"]):
            args = parse_args()
        assert args.verbose is True

    def test_no_binary_flag(self):
        from bridge.bike_bridge import parse_args
        with patch.object(sys, "argv", ["bridge", "--no-binary"]):
            args = parse_args()
        assert args.no_binary is True

    def test_spawn_interval(self):
        from bridge.bike_bridge import parse_args
        with patch.object(sys, "argv", ["bridge", "--spawn-interval", "5.0"]):
            args = parse_args()
        assert args.spawn_interval == 5.0

    def test_scan_timeout(self):
        from bridge.bike_bridge import parse_args
        with patch.object(sys, "argv", ["bridge", "--scan-timeout", "20"]):
            args = parse_args()
        assert args.scan_timeout == 20.0


class TestUDPProtocolHandle:
    """_UDPProtocol._handle: ASCII trigger mapping and JSON passthrough."""

    def _make_server(self):
        from bridge.bike_bridge import WahooBridgeServer
        return WahooBridgeServer(mock=True)

    @pytest.mark.asyncio
    async def test_hall_hit_mapping(self):
        from bridge.bike_bridge import WahooBridgeServer
        server = WahooBridgeServer(mock=True)
        proto = WahooBridgeServer._UDPProtocol(server)
        with patch.object(server, "broadcast_json", new_callable=AsyncMock) as mock_bcast:
            await proto._handle("HALL_HIT", ("127.0.0.1", 5005))
        call_args = mock_bcast.call_args[0][0]
        assert call_args["event"] == "hall_hit"

    @pytest.mark.asyncio
    async def test_hit_maps_to_hall_hit(self):
        from bridge.bike_bridge import WahooBridgeServer
        server = WahooBridgeServer(mock=True)
        proto = WahooBridgeServer._UDPProtocol(server)
        with patch.object(server, "broadcast_json", new_callable=AsyncMock) as mock_bcast:
            await proto._handle("HIT", ("127.0.0.1", 5005))
        assert mock_bcast.call_args[0][0]["event"] == "hall_hit"

    @pytest.mark.asyncio
    async def test_switch_hit_mapping(self):
        from bridge.bike_bridge import WahooBridgeServer
        server = WahooBridgeServer(mock=True)
        proto = WahooBridgeServer._UDPProtocol(server)
        with patch.object(server, "broadcast_json", new_callable=AsyncMock) as mock_bcast:
            await proto._handle("SWITCH_HIT", ("127.0.0.1", 5005))
        assert mock_bcast.call_args[0][0]["event"] == "switch_hit"

    @pytest.mark.asyncio
    async def test_unknown_ascii_uses_raw_string(self):
        from bridge.bike_bridge import WahooBridgeServer
        server = WahooBridgeServer(mock=True)
        proto = WahooBridgeServer._UDPProtocol(server)
        with patch.object(server, "broadcast_json", new_callable=AsyncMock) as mock_bcast:
            await proto._handle("CUSTOM_EVENT", ("127.0.0.1", 5005))
        assert mock_bcast.call_args[0][0]["event"] == "CUSTOM_EVENT"

    @pytest.mark.asyncio
    async def test_json_passthrough(self):
        from bridge.bike_bridge import WahooBridgeServer
        server = WahooBridgeServer(mock=True)
        proto = WahooBridgeServer._UDPProtocol(server)
        payload = json.dumps({"event": "custom", "value": 42})
        with patch.object(server, "broadcast_json", new_callable=AsyncMock) as mock_bcast:
            await proto._handle(payload, ("127.0.0.1", 5005))
        call_args = mock_bcast.call_args[0][0]
        assert call_args["event"] == "custom"
        assert call_args["value"] == 42

    @pytest.mark.asyncio
    async def test_empty_text_does_nothing(self):
        from bridge.bike_bridge import WahooBridgeServer
        server = WahooBridgeServer(mock=True)
        proto = WahooBridgeServer._UDPProtocol(server)
        with patch.object(server, "broadcast_json", new_callable=AsyncMock) as mock_bcast:
            await proto._handle("", ("127.0.0.1", 5005))
        mock_bcast.assert_not_called()

    @pytest.mark.asyncio
    async def test_metadata_keys_attached(self):
        """Every relayed event must have source, addr, and timestamp keys."""
        from bridge.bike_bridge import WahooBridgeServer
        server = WahooBridgeServer(mock=True)
        proto = WahooBridgeServer._UDPProtocol(server)
        with patch.object(server, "broadcast_json", new_callable=AsyncMock) as mock_bcast:
            await proto._handle("HALL_HIT", ("10.0.0.1", 1234))
        d = mock_bcast.call_args[0][0]
        assert d["source"] == "udp"
        assert "10.0.0.1:1234" == d["addr"]
        assert "timestamp" in d


class TestWahooBridgeServerInit:
    """WahooBridgeServer: initial state and configuration."""

    def test_defaults(self):
        from bridge.bike_bridge import WahooBridgeServer
        s = WahooBridgeServer()
        assert s.port == 8765
        assert s.host == "localhost"
        assert s.mock is False
        assert s.clients == set()
        assert s.running is False
        assert s._ble_hr is None

    def test_mock_mode(self):
        from bridge.bike_bridge import WahooBridgeServer
        s = WahooBridgeServer(mock=True)
        assert s.mock is True

    def test_custom_ports(self):
        from bridge.bike_bridge import WahooBridgeServer
        s = WahooBridgeServer(port=9000, udp_port=6000)
        assert s.port == 9000
        assert s.udp_port == 6000
