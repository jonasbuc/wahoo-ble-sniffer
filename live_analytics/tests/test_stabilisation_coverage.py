"""
Regression and coverage tests written after the b4514ff stabilisation pass.

Each test targets one of the specific bugs fixed (C2–C9) or a previously
uncovered branch in the code paths that were hardened.  The file is
deliberately standalone – no external services are needed.

Coverage targets
----------------
- live_analytics/questionnaire/app.py      80 % → ≥ 95 %
- live_analytics/questionnaire/config.py  100 %  (regression guard C4)
- live_analytics/app/api_sessions.py       88 % → ≥ 95 %
- live_analytics/app/ws_ingest.py          88 % → ≥ 93 %
- live_analytics/app/scoring/anomaly.py    76 % → ≥ 90 %
- live_analytics/scripts/simulate_ride.py  argparse (C9)
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ══════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════

def _make_telemetry_record(session_id: str = "s1", unix_ms: int = 1_000, **kw):
    from live_analytics.app.models import TelemetryRecord
    defaults = dict(
        session_id=session_id,
        unix_ms=unix_ms,
        unity_time=1.0,
        speed=5.0,
        heart_rate=80,
        steering_angle=0.0,
        brake_front=0.0,
        brake_rear=0.0,
        scenario_id="sc",
    )
    defaults.update(kw)
    return TelemetryRecord(**defaults)


# ══════════════════════════════════════════════════════════════════════
#  C4 – questionnaire/config.py ensure_dirs() creates DB_PATH.parent
# ══════════════════════════════════════════════════════════════════════

class TestEnsureDirsCreatesDbParent:
    """Regression guard for bug C4: ensure_dirs() must create DB_PATH.parent."""

    def test_ensure_dirs_creates_data_dir(self, tmp_path):
        """DATA_DIR is created when it does not exist."""
        new_data = tmp_path / "nested" / "data"
        new_db   = new_data / "questionnaire.db"
        with (
            patch.dict(os.environ, {"QS_DATA_DIR": str(new_data), "QS_DB_PATH": str(new_db)}),
        ):
            # Re-import so env vars are picked up by module-level Path() calls
            import importlib
            import live_analytics.questionnaire.config as cfg
            importlib.reload(cfg)
            cfg.ensure_dirs()
            assert cfg.DATA_DIR.exists()

    def test_ensure_dirs_creates_db_parent_when_custom_path(self, tmp_path):
        """DB_PATH.parent is created even when it differs from DATA_DIR (C4 regression)."""
        custom_db_dir = tmp_path / "custom" / "very" / "deep"
        custom_db     = custom_db_dir / "qs.db"
        assert not custom_db_dir.exists(), "pre-condition: directory must not exist yet"

        with patch.dict(os.environ, {"QS_DB_PATH": str(custom_db)}):
            import importlib
            import live_analytics.questionnaire.config as cfg
            importlib.reload(cfg)
            cfg.ensure_dirs()

        assert custom_db_dir.exists(), "DB_PATH.parent must be created by ensure_dirs()"

    def test_ensure_dirs_idempotent(self, tmp_path):
        """Calling ensure_dirs() twice must not raise."""
        db_path = tmp_path / "sub" / "qs.db"
        with patch.dict(os.environ, {"QS_DATA_DIR": str(tmp_path / "sub"), "QS_DB_PATH": str(db_path)}):
            import importlib
            import live_analytics.questionnaire.config as cfg
            importlib.reload(cfg)
            cfg.ensure_dirs()
            cfg.ensure_dirs()  # second call must be idempotent


# ══════════════════════════════════════════════════════════════════════
#  C6 – questionnaire/app.py healthz DB probe
# ══════════════════════════════════════════════════════════════════════

# Patch the DB path to an in-memory-equivalent temp file before the app module
# is imported so the module-level DB_PATH picks up the override.
_QS_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_QS_TMP_DB.close()
os.environ.setdefault("QS_DB_PATH", _QS_TMP_DB.name)

from fastapi.testclient import TestClient              # noqa: E402
from live_analytics.questionnaire import db as qs_db  # noqa: E402
from live_analytics.questionnaire.app import app as qs_app  # noqa: E402
from live_analytics.questionnaire.config import DB_PATH as QS_DB_PATH  # noqa: E402

# Initialise the DB once for this module
qs_db.init_db(QS_DB_PATH)
_qs_client = TestClient(qs_app)


@pytest.fixture(autouse=True)
def _clean_qs_db():
    """Wipe and re-initialise questionnaire DB tables between tests."""
    conn = sqlite3.connect(str(QS_DB_PATH))
    conn.executescript(
        "DROP TABLE IF EXISTS questionnaire_responses; "
        "DROP TABLE IF EXISTS participants;"
    )
    conn.close()
    qs_db.init_db(QS_DB_PATH)
    yield


class TestHealthzDbProbe:
    """C6 regression: healthz must report db_ok and a db_path."""

    def test_healthz_contains_db_ok_field(self):
        r = _qs_client.get("/api/healthz")
        assert r.status_code == 200
        body = r.json()
        assert "db_ok" in body, "healthz must expose db_ok (C6 regression)"
        assert "db_path" in body
        assert "db_detail" in body

    def test_healthz_db_ok_true_when_db_is_healthy(self):
        r = _qs_client.get("/api/healthz")
        assert r.json()["db_ok"] is True

    def test_healthz_db_ok_false_when_db_is_broken(self):
        """Simulate a broken DB so db_ok becomes False."""
        # healthz now uses _connect() from the questionnaire db pool; patch that
        # to raise so the exception path is exercised.
        with patch("live_analytics.questionnaire.db._connect",
                   side_effect=sqlite3.OperationalError("unable to open database file")):
            r = _qs_client.get("/api/healthz")
        assert r.status_code == 200, "healthz itself must always return 200"
        body = r.json()
        assert body["status"] == "ok"
        assert body["db_ok"] is False
        assert "unable to open" in body["db_detail"]

    def test_healthz_status_always_ok_even_on_db_failure(self):
        """The HTTP status code must be 200 even when the DB is down (C6)."""
        with patch("live_analytics.questionnaire.db._connect", side_effect=Exception("disk full")):
            r = _qs_client.get("/api/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ══════════════════════════════════════════════════════════════════════
#  C7 – questionnaire/app.py error branches (503 on DB failure)
# ══════════════════════════════════════════════════════════════════════

class TestListParticipantsErrorHandling:
    """C7 regression: list_participants must return 503 on DB error."""

    def test_list_participants_503_on_db_error(self):
        with patch(
            "live_analytics.questionnaire.app.list_participants",
            side_effect=sqlite3.OperationalError("locked"),
        ):
            r = _qs_client.get("/api/participants")
        assert r.status_code == 503
        assert "Failed to list participants" in r.json()["detail"]

    def test_list_participants_returns_200_normally(self):
        _qs_client.post("/api/participants", json={"participant_id": "P1"})
        r = _qs_client.get("/api/participants")
        assert r.status_code == 200
        assert len(r.json()) == 1


class TestGetAllAnswersErrorHandling:
    """C7 regression: get_all_answers must return 503 on DB error."""

    def test_get_all_answers_503_on_db_error(self):
        _qs_client.post("/api/participants", json={"participant_id": "P1"})
        with patch(
            "live_analytics.questionnaire.app.get_answers",
            side_effect=sqlite3.OperationalError("corrupt"),
        ):
            r = _qs_client.get("/api/participants/P1/answers")
        assert r.status_code == 503
        assert "Failed to load answers" in r.json()["detail"]

    def test_get_all_answers_returns_all_phases(self):
        """GET /api/participants/{id}/answers (no phase) must return both phases."""
        _qs_client.post("/api/participants", json={"participant_id": "P1"})
        _qs_client.post("/api/participants/P1/answers/pre",  json={"question_id": "q1", "answer": "a"})
        _qs_client.post("/api/participants/P1/answers/post", json={"question_id": "q2", "answer": "b"})
        r = _qs_client.get("/api/participants/P1/answers")
        assert r.status_code == 200
        phases = {row["phase"] for row in r.json()}
        assert "pre" in phases
        assert "post" in phases


class TestQuestionnaireMiscErrorPaths:
    """Additional branches not previously covered."""

    def test_create_participant_db_error_returns_500(self):
        with patch(
            "live_analytics.questionnaire.app.create_participant",
            side_effect=RuntimeError("constraint violation"),
        ):
            r = _qs_client.post("/api/participants", json={"participant_id": "P1"})
        assert r.status_code == 500

    def test_create_participant_returns_none_gives_500(self):
        """If create_participant returns None the endpoint must raise 500."""
        with patch("live_analytics.questionnaire.app.create_participant", return_value=None):
            r = _qs_client.post("/api/participants", json={"participant_id": "P1"})
        assert r.status_code == 500

    def test_link_session_404_for_missing_participant(self):
        r = _qs_client.put("/api/participants/GHOST/session", json={"session_id": "s42"})
        assert r.status_code == 404

    def test_get_participant_not_found(self):
        r = _qs_client.get("/api/participants/NOBODY")
        assert r.status_code == 404

    def test_save_answer_404_for_missing_participant(self):
        r = _qs_client.post(
            "/api/participants/GHOST/answers/pre",
            json={"question_id": "q1", "answer": "x"},
        )
        assert r.status_code == 404

    def test_bulk_save_404_for_missing_participant(self):
        r = _qs_client.put(
            "/api/participants/GHOST/answers/pre",
            json={"answers": {"q1": "a"}},
        )
        assert r.status_code == 404

    def test_answers_by_phase_db_error_returns_500(self):
        _qs_client.post("/api/participants", json={"participant_id": "P1"})
        with patch(
            "live_analytics.questionnaire.app.get_answers",
            side_effect=RuntimeError("boom"),
        ):
            r = _qs_client.get("/api/participants/P1/answers/pre")
        assert r.status_code == 500

    def test_save_answer_db_error_returns_500(self):
        _qs_client.post("/api/participants", json={"participant_id": "P1"})
        with patch(
            "live_analytics.questionnaire.app.save_answer",
            side_effect=RuntimeError("boom"),
        ):
            r = _qs_client.post(
                "/api/participants/P1/answers/pre",
                json={"question_id": "q1", "answer": "x"},
            )
        assert r.status_code == 500

    def test_bulk_save_db_error_returns_500(self):
        _qs_client.post("/api/participants", json={"participant_id": "P1"})
        with patch(
            "live_analytics.questionnaire.app.save_answers_bulk",
            side_effect=RuntimeError("boom"),
        ):
            r = _qs_client.put(
                "/api/participants/P1/answers/pre",
                json={"answers": {"q1": "a"}},
            )
        assert r.status_code == 500

    def test_list_questionnaires(self):
        r = _qs_client.get("/api/questionnaire")
        assert r.status_code == 200
        assert "phases" in r.json()
        assert len(r.json()["phases"]) > 0


# ══════════════════════════════════════════════════════════════════════
#  api_sessions.py – uncovered error branches
# ══════════════════════════════════════════════════════════════════════

class TestApiSessionsErrorBranches:
    """Tests for api_sessions.py lines 101-105 (session_detail DB error) and
    lines 132-134 (live_latest RuntimeError)."""

    def _make_app_client(self):
        """Return a FastAPI TestClient wired to the real app."""
        from fastapi.testclient import TestClient
        from live_analytics.app.main import app
        return TestClient(app, raise_server_exceptions=False)

    def test_session_detail_db_error_returns_500(self):
        """DB exception inside session_detail must yield HTTP 500."""
        client = self._make_app_client()
        with patch(
            "live_analytics.app.api_sessions.get_session",
            side_effect=RuntimeError("disk full"),
        ):
            r = client.get("/api/sessions/any-session")
        assert r.status_code == 500

    def test_session_detail_not_found_returns_404(self):
        client = self._make_app_client()
        with patch("live_analytics.app.api_sessions.get_session", return_value=None):
            r = client.get("/api/sessions/nonexistent")
        assert r.status_code == 404

    def test_live_latest_returns_none_when_no_state(self):
        """When latest_scores is empty, /api/live/latest must return null."""
        import live_analytics.app.api_sessions as api
        client = self._make_app_client()
        api.latest_scores.clear()
        api.latest_records.clear()
        r = client.get("/api/live/latest")
        assert r.status_code == 200
        assert r.json() is None

    def test_live_latest_returns_most_recent_session(self):
        """live_latest picks the session whose latest record has the highest unix_ms."""
        import live_analytics.app.api_sessions as api
        from live_analytics.app.models import ScoringResult

        rec_old = _make_telemetry_record("s_old", unix_ms=1_000)
        rec_new = _make_telemetry_record("s_new", unix_ms=9_000)
        api.latest_records.update({"s_old": rec_old, "s_new": rec_new})
        api.latest_scores.update({
            "s_old": ScoringResult(stress_score=10.0, risk_score=5.0),
            "s_new": ScoringResult(stress_score=20.0, risk_score=8.0),
        })
        try:
            client = self._make_app_client()
            r = client.get("/api/live/latest")
            assert r.status_code == 200
            body = r.json()
            assert body is not None
            assert body["session_id"] == "s_new"
            assert body["unix_ms"] == 9_000
        finally:
            api.latest_records.clear()
            api.latest_scores.clear()

    def test_live_latest_exception_during_snapshot_returns_none(self):
        """RuntimeError inside the try block must return None, not 500 (line 121)."""
        import live_analytics.app.api_sessions as api
        from live_analytics.app.models import ScoringResult

        api.latest_records["s1"] = _make_telemetry_record("s1", unix_ms=5_000)
        api.latest_scores["s1"] = ScoringResult()
        try:
            client = self._make_app_client()
            # Patch `max` (used inside the try block) to raise so we enter the except branch
            with patch("live_analytics.app.api_sessions.max", side_effect=RuntimeError("boom")):
                r = client.get("/api/live/latest")
            assert r.status_code == 200
            assert r.json() is None
        finally:
            api.latest_records.clear()
            api.latest_scores.clear()


# ══════════════════════════════════════════════════════════════════════
#  ws_ingest.py – uncovered branches
# ══════════════════════════════════════════════════════════════════════

class TestWsIngestUncoveredBranches:
    """Tests for the paths in ws_ingest.py that were ≥88% before this pass."""

    def setup_method(self):
        from live_analytics.app import ws_ingest as m
        m._windows.clear()
        m._record_counts.clear()
        m.latest_scores.clear()
        m.latest_records.clear()
        m._raw_writer = None

    def _make_records(self, n: int = 1, session_id: str = "s_test") -> list:
        return [_make_telemetry_record(session_id=session_id, unix_ms=1_000 + i * 50)
                for i in range(n)]

    # ── _handle_connection ────────────────────────────────────────────

    async def test_handle_connection_unexpected_exception_is_logged(self, caplog):
        """The outer except Exception in _handle_connection must log and not re-raise."""
        import logging
        from live_analytics.app.ws_ingest import _handle_connection

        ws = MagicMock()
        ws.remote_address = ("127.0.0.1", 12345)

        async def _bad_iter():
            raise RuntimeError("unexpected network error")
            # make this an async generator so `async for` works
            yield  # pragma: no cover

        ws.__aiter__ = lambda self: _bad_iter()
        with caplog.at_level(logging.ERROR, logger="live_analytics.ws_ingest"):
            await _handle_connection(ws)
        assert any("Unexpected error" in r.message for r in caplog.records)

    async def test_handle_connection_connection_closed_is_logged(self, caplog):
        """ConnectionClosed from the iterator must be logged as INFO (not an error)."""
        import logging
        from websockets.exceptions import ConnectionClosed, ConnectionClosedOK
        from live_analytics.app.ws_ingest import _handle_connection

        ws = MagicMock()
        ws.remote_address = ("127.0.0.1", 12346)

        rcv_exc = ConnectionClosedOK(None, None)

        async def _closed_iter():
            raise rcv_exc
            yield  # pragma: no cover

        ws.__aiter__ = lambda self: _closed_iter()
        with caplog.at_level(logging.INFO, logger="live_analytics.ws_ingest"):
            await _handle_connection(ws)
        info_msgs = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("disconnected" in m for m in info_msgs)

    # ── _process_message: feedback send failure ───────────────────────

    async def test_feedback_send_failure_is_swallowed(self, caplog):
        """ws.send() failure after scoring must be logged at DEBUG, not re-raise."""
        import logging
        from live_analytics.app import ws_ingest as m
        from live_analytics.app.ws_ingest import _process_message
        from live_analytics.app.models import ScoringResult, TelemetryBatch

        sid = "fb_fail_ses"
        rec = self._make_records(1, session_id=sid)[0]
        batch = TelemetryBatch(records=[rec])

        # Pre-populate latest_scores so the send branch is reached
        m.latest_scores[sid] = ScoringResult(stress_score=5.0, risk_score=2.0)

        ws = AsyncMock()
        ws.send = AsyncMock(side_effect=Exception("connection reset"))

        raw = batch.model_dump_json()
        with caplog.at_level(logging.DEBUG, logger="live_analytics.ws_ingest"), \
             patch("live_analytics.app.ws_ingest.upsert_session"), \
             patch("live_analytics.app.ws_ingest.increment_record_count"), \
             patch("live_analytics.app.ws_ingest._broadcast_dashboard"):
            await _process_message(ws, raw)

        # send was called and the exception swallowed
        ws.send.assert_called_once()
        assert any("Failed to send feedback" in r.message for r in caplog.records)

    async def test_handle_connection_processes_one_message(self, caplog):
        """Yield one valid message then close – covers the _process_message call (line 82)."""
        import json
        import logging
        from live_analytics.app.ws_ingest import _handle_connection
        from live_analytics.app.models import TelemetryBatch

        rec = self._make_records(1, session_id="one_msg")[0]
        batch = TelemetryBatch(records=[rec])
        payload = batch.model_dump_json()

        async def _yield_one_then_close():
            yield payload
            # No more messages – ends the async for loop cleanly

        ws = MagicMock()
        ws.remote_address = ("127.0.0.1", 12347)
        ws.__aiter__ = lambda self: _yield_one_then_close()
        ws.send = AsyncMock()

        with patch("live_analytics.app.ws_ingest.upsert_session"), \
             patch("live_analytics.app.ws_ingest.increment_record_count"), \
             patch("live_analytics.app.ws_ingest._broadcast_dashboard"):
            await _handle_connection(ws)

    def test_raw_writer_append_called_when_writer_is_set(self):
        """When _raw_writer IS set, append_many must be called (line 179 coverage)."""
        from live_analytics.app import ws_ingest as m

        mock_writer = MagicMock()
        m._raw_writer = mock_writer
        sid = "has_writer_ses"
        records = self._make_records(2, session_id=sid)
        with patch("live_analytics.app.ws_ingest.upsert_session"), \
             patch("live_analytics.app.ws_ingest.increment_record_count"):
            m._ingest_session_batch(sid, records)

        mock_writer.append_many.assert_called_once_with(records)
        m._raw_writer = None  # restore degraded state

    # ── _ingest_session_batch: no raw_writer degraded mode ──────────

    def test_no_raw_writer_emits_warning_and_still_scores(self, caplog):
        """When _raw_writer is None the else-branch at ws_ingest.py:179 must run."""
        import logging
        from live_analytics.app import ws_ingest as m

        assert m._raw_writer is None  # setup_method sets this to None
        sid = "degraded_ses"
        records = self._make_records(3, session_id=sid)
        with caplog.at_level(logging.WARNING, logger="live_analytics.ws_ingest"), \
             patch("live_analytics.app.ws_ingest.upsert_session"), \
             patch("live_analytics.app.ws_ingest.increment_record_count"):
            m._ingest_session_batch(sid, records)

        # The warning must have been emitted
        assert any("raw_writer" in r.message and "degraded mode" in r.message
                   for r in caplog.records), \
            "Expected degraded-mode raw_writer warning"
        # Scoring still happened despite no raw_writer
        assert sid in m.latest_scores

    # ── _ingest_session_batch: DB upsert failure ─────────────────────

    def test_upsert_failure_logs_but_session_still_scored(self, caplog):
        """DB error on upsert_session must not abort scoring for the session."""
        import logging
        from live_analytics.app import ws_ingest as m

        sid = "upsert_fail"
        records = self._make_records(3, session_id=sid)
        with caplog.at_level(logging.ERROR, logger="live_analytics.ws_ingest"), \
             patch("live_analytics.app.ws_ingest.upsert_session",
                   side_effect=RuntimeError("db locked")), \
             patch("live_analytics.app.ws_ingest.increment_record_count"):
            m._ingest_session_batch(sid, records)

        assert any("DB error" in r.message for r in caplog.records)
        # Session was still initialised and scored
        assert sid in m._windows
        assert sid in m.latest_scores

    # ── _ingest_session_batch: increment_record_count failure ────────

    def test_increment_failure_does_not_abort_ingest(self, caplog):
        """DB error on increment_record_count must log and continue."""
        import logging
        from live_analytics.app import ws_ingest as m

        sid = "incr_fail"
        records = self._make_records(5, session_id=sid)
        with caplog.at_level(logging.ERROR, logger="live_analytics.ws_ingest"), \
             patch("live_analytics.app.ws_ingest.upsert_session"), \
             patch("live_analytics.app.ws_ingest.increment_record_count",
                   side_effect=RuntimeError("db busy")):
            m._ingest_session_batch(sid, records)

        assert any("increment" in r.message.lower() for r in caplog.records)
        assert sid in m.latest_scores

    # ── _process_message: whole batch dropped on ingest exception ────

    async def test_ingest_exception_drops_batch_and_logs(self, caplog):
        """If _ingest_session_batch raises, the batch is dropped and logged."""
        import logging
        from live_analytics.app.ws_ingest import _process_message
        from live_analytics.app.models import TelemetryBatch

        rec = self._make_records(1, session_id="drop_ses")[0]
        batch = TelemetryBatch(records=[rec])
        ws = AsyncMock()

        with caplog.at_level(logging.ERROR, logger="live_analytics.ws_ingest"), \
             patch("live_analytics.app.ws_ingest._ingest_session_batch",
                   side_effect=RuntimeError("fatal")), \
             patch("live_analytics.app.ws_ingest._broadcast_dashboard"):
            await _process_message(ws, batch.model_dump_json())

        assert any("batch dropped" in r.message for r in caplog.records)


# ══════════════════════════════════════════════════════════════════════
#  anomaly.py – sklearn-absent and fit/predict paths
# ══════════════════════════════════════════════════════════════════════

class TestAnomalyDetector:
    """Cover the branches in scoring/anomaly.py (76 % before this pass)."""

    def test_available_reflects_sklearn_presence(self):
        from live_analytics.app.scoring.anomaly import AnomalyDetector, _HAS_SKLEARN
        det = AnomalyDetector()
        assert det.available is _HAS_SKLEARN

    def test_predict_before_fit_returns_safe_default(self):
        """predict() before fit() must return (False, 0.0) regardless of sklearn."""
        import numpy as np
        from live_analytics.app.scoring.anomaly import AnomalyDetector
        det = AnomalyDetector()
        assert det.fitted is False
        is_anomaly, score = det.predict(np.array([1.0, 2.0, 3.0]))
        assert is_anomaly is False
        assert score == 0.0

    def test_fit_disabled_without_sklearn(self, caplog):
        """fit() must log a warning and return without error when sklearn absent."""
        import logging
        import numpy as np
        from live_analytics.app.scoring.anomaly import AnomalyDetector
        det = AnomalyDetector()
        with patch("live_analytics.app.scoring.anomaly._HAS_SKLEARN", False), \
             caplog.at_level(logging.WARNING, logger="live_analytics.anomaly"):
            det.fit(np.zeros((10, 3)))
        assert det.fitted is False
        assert any("scikit-learn" in r.message.lower() or "cannot fit" in r.message.lower()
                   for r in caplog.records)

    def test_predict_when_not_fitted_and_sklearn_absent(self):
        """predict() with _HAS_SKLEARN=False and unfitted must still return safe default."""
        import numpy as np
        from live_analytics.app.scoring.anomaly import AnomalyDetector
        det = AnomalyDetector()
        with patch("live_analytics.app.scoring.anomaly._HAS_SKLEARN", False):
            is_anomaly, score = det.predict(np.array([1.0, 2.0]))
        assert is_anomaly is False
        assert score == 0.0

    @pytest.mark.skipif(
        not __import__("importlib").util.find_spec("sklearn"),
        reason="scikit-learn not installed",
    )
    def test_fit_and_predict_with_sklearn(self):
        """When sklearn is available, fit+predict must work end-to-end."""
        import numpy as np
        from live_analytics.app.scoring.anomaly import AnomalyDetector

        rng = __import__("numpy").random.default_rng(0)
        X = rng.standard_normal((100, 4))
        det = AnomalyDetector(contamination=0.1, random_state=0)
        det.fit(X)
        assert det.fitted is True

        # Normal point – should NOT be flagged as anomaly most of the time
        normal_pt = rng.standard_normal(4)
        is_anomaly, score = det.predict(normal_pt)
        assert isinstance(is_anomaly, bool)
        assert isinstance(score, float)

        # Extreme outlier
        outlier = np.array([100.0, 100.0, 100.0, 100.0])
        is_anom_out, _ = det.predict(outlier)
        assert is_anom_out is True


# ══════════════════════════════════════════════════════════════════════
#  C9 – simulate_ride.py argparse
# ══════════════════════════════════════════════════════════════════════

class TestSimulateRideArgparse:
    """C9 regression: --duration and --hz must be parsed and forwarded to simulate()."""

    def _get_parser(self):
        """Import the module and return an argparse.ArgumentParser."""
        import importlib
        spec = importlib.util.spec_from_file_location(
            "simulate_ride",
            Path(__file__).resolve().parents[2] / "live_analytics" / "scripts" / "simulate_ride.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_default_duration_is_45(self):
        mod = self._get_parser()
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--duration", type=float, default=45.0)
        parser.add_argument("--hz", type=float, default=20.0)
        args = parser.parse_args([])
        assert args.duration == 45.0

    def test_default_hz_is_20(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--duration", type=float, default=45.0)
        parser.add_argument("--hz", type=float, default=20.0)
        args = parser.parse_args([])
        assert args.hz == 20.0

    def test_custom_duration_and_hz(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--duration", type=float, default=45.0)
        parser.add_argument("--hz", type=float, default=20.0)
        args = parser.parse_args(["--duration", "120", "--hz", "10"])
        assert args.duration == 120.0
        assert args.hz == 10.0

    def test_simulate_called_with_parsed_args(self):
        """simulate_ride.py --help must document --duration and --hz (C9 regression)."""
        import subprocess
        simulate_path = (
            Path(__file__).resolve().parents[2]
            / "live_analytics" / "scripts" / "simulate_ride.py"
        )
        result = subprocess.run(
            [sys.executable, str(simulate_path), "--help"],
            capture_output=True, text=True,
        )
        # argparse --help exits 0 and prints the flag names
        assert result.returncode == 0
        assert "--duration" in result.stdout
        assert "--hz" in result.stdout


# ══════════════════════════════════════════════════════════════════════
#  requirements.txt / pyproject.toml – fresh-clone dependency guards
# ══════════════════════════════════════════════════════════════════════

class TestDependencyDeclarations:
    """Smoke-tests that ensure the packages needed by preflight are importable
    and that the key deps added in the stabilisation pass are present."""

    def test_httpx_is_importable(self):
        """httpx must be installed (C2 regression: was missing from requirements.txt)."""
        import httpx  # noqa: F401

    def test_pytest_cov_is_importable(self):
        """pytest-cov must be installed (C3 regression: was missing from dev deps)."""
        import pytest_cov  # noqa: F401

    def test_httpx_in_requirements_txt(self):
        req_path = Path(__file__).resolve().parents[2] / "requirements.txt"
        text = req_path.read_text()
        assert "httpx" in text, "httpx must appear in requirements.txt (C2 regression)"

    def test_pytest_cov_in_requirements_txt(self):
        req_path = Path(__file__).resolve().parents[2] / "requirements.txt"
        text = req_path.read_text()
        assert "pytest-cov" in text, "pytest-cov must appear in requirements.txt (C3 regression)"

    def test_httpx_in_pyproject_toml(self):
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        text = pyproject.read_text()
        assert "httpx" in text, "httpx must appear in pyproject.toml (C2 regression)"

    def test_asyncio_mode_auto_in_pyproject(self):
        """asyncio_mode = 'auto' must be set in [tool.pytest.ini_options] (C8 regression)."""
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        text = pyproject.read_text()
        assert 'asyncio_mode' in text and 'auto' in text, (
            "asyncio_mode = 'auto' must be set in pyproject.toml (C8 regression)"
        )
