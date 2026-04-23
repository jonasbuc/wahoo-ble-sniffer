"""
Failure-path / observability tests.

Verifies that important failure scenarios produce useful terminal-visible log
messages rather than silent failures, wrong fallback values, or bare tracebacks
with no context.

Run with:
    pytest live_analytics/tests/test_failure_diagnostics.py -v
"""

from __future__ import annotations

import json
import logging
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_record(session_id: str = "sess-test", unix_ms: int = 1_000) -> dict:
    return {
        "session_id": session_id,
        "unix_ms": unix_ms,
        "unity_time": 0.0,
        "steering_angle": 0.0,
        "heart_rate": 70.0,
        "speed": 5.0,
        "brake_front": 0,
        "brake_rear": 0,
        "trigger_id": "",
        "head_rot_y": 0.0,
        "head_rot_w": 1.0,
        "scenario_id": "",
    }


def _batch_json(n: int = 1, session_id: str = "sess-diag") -> str:
    records = [_make_record(session_id) for _ in range(n)]
    return json.dumps({"records": records, "count": n, "sent_at": "2024-01-01T00:00:00Z"})


# ─────────────────────────────────────────────────────────────────────────────
#  1. sqlite_store: _connect() logs critical and re-raises on bad path
# ─────────────────────────────────────────────────────────────────────────────

class TestSqliteConnectFailure:
    def setup_method(self):
        from live_analytics.app.storage import sqlite_store
        sqlite_store.close_pool()

    def teardown_method(self):
        from live_analytics.app.storage import sqlite_store
        sqlite_store.close_pool()

    def test_connect_bad_path_raises(self, tmp_path):
        """_connect to a non-existent directory should raise sqlite3.OperationalError."""
        from live_analytics.app.storage.sqlite_store import _connect
        bad = tmp_path / "nonexistent_dir" / "db.sqlite"
        with pytest.raises(sqlite3.OperationalError):
            _connect(bad)

    def test_connect_bad_path_logs_critical(self, tmp_path, caplog):
        from live_analytics.app.storage.sqlite_store import _connect
        bad = tmp_path / "nonexistent_dir" / "db.sqlite"
        with caplog.at_level(logging.CRITICAL, logger="live_analytics.storage"):
            with pytest.raises(sqlite3.OperationalError):
                _connect(bad)
        assert any("Cannot open SQLite database" in r.message for r in caplog.records), \
            "Expected CRITICAL log for bad DB path"

    def test_init_db_bad_path_logs_critical(self, tmp_path, caplog):
        from live_analytics.app.storage.sqlite_store import init_db
        bad = tmp_path / "nonexistent_dir" / "db.sqlite"
        with caplog.at_level(logging.CRITICAL, logger="live_analytics.storage"):
            with pytest.raises(Exception):
                init_db(bad)
        assert any("Failed to initialise database schema" in r.message or
                   "Cannot open SQLite database" in r.message
                   for r in caplog.records), \
            "Expected CRITICAL log for init_db failure"


# ─────────────────────────────────────────────────────────────────────────────
#  2. sqlite_store: get_session handles malformed JSON in latest_scores
# ─────────────────────────────────────────────────────────────────────────────

class TestSqliteGetSessionBadJson:
    def setup_method(self):
        from live_analytics.app.storage import sqlite_store
        sqlite_store.close_pool()

    def teardown_method(self):
        from live_analytics.app.storage import sqlite_store
        sqlite_store.close_pool()

    def test_malformed_scores_json_logs_warning_and_returns_none_scores(self, tmp_path, caplog):
        from live_analytics.app.storage.sqlite_store import init_db, get_session, _connect
        db = tmp_path / "test.db"
        init_db(db)
        # Manually corrupt the latest_scores column
        conn = _connect(db)
        conn.execute(
            "INSERT INTO sessions (session_id, start_unix_ms, latest_scores) VALUES (?, ?, ?)",
            ("bad-json-sess", 1000, "{NOT VALID JSON}"),
        )
        conn.commit()

        with caplog.at_level(logging.WARNING, logger="live_analytics.storage"):
            result = get_session(db, "bad-json-sess")

        assert result is not None, "Should still return a SessionDetail"
        assert result.latest_scores is None, "Malformed JSON → latest_scores should be None"
        assert any("Malformed latest_scores JSON" in r.message for r in caplog.records), \
            "Expected WARNING about malformed scores JSON"


# ─────────────────────────────────────────────────────────────────────────────
#  3. raw_writer: mkdir failure logs error and raises
# ─────────────────────────────────────────────────────────────────────────────

class TestRawWriterMkdirFailure:
    def test_mkdir_failure_logs_error(self, tmp_path, caplog):
        from live_analytics.app.storage.raw_writer import RawWriter
        from live_analytics.app.models import TelemetryRecord

        writer = RawWriter(tmp_path)
        rec = TelemetryRecord(**_make_record())

        # Make the sessions_dir a file, so mkdir inside it fails
        blocker = tmp_path / rec.session_id
        blocker.write_text("i am a file, not a dir")

        with caplog.at_level(logging.ERROR, logger="live_analytics.raw_writer"):
            # append should not raise (it catches the OSError)
            writer.append(rec)

        assert any("Cannot create session directory" in r.message for r in caplog.records), \
            "Expected ERROR log when mkdir fails"

    def test_mkdir_failure_append_many_does_not_crash(self, tmp_path, caplog):
        from live_analytics.app.storage.raw_writer import RawWriter
        from live_analytics.app.models import TelemetryRecord

        writer = RawWriter(tmp_path)
        rec = TelemetryRecord(**_make_record())

        blocker = tmp_path / rec.session_id
        blocker.write_text("blocking file")

        # append_many should continue gracefully (skip the failed session)
        with caplog.at_level(logging.ERROR, logger="live_analytics.raw_writer"):
            writer.append_many([rec])  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
#  4. ws_ingest: malformed JSON from Unity is logged and does not crash
# ─────────────────────────────────────────────────────────────────────────────

class TestIngestMalformedPayload:
    """_process_message must log + skip, never raise."""

    @pytest.mark.asyncio
    async def test_malformed_json_logs_warning(self, tmp_path, caplog):
        import live_analytics.app.ws_ingest as ingest
        ingest._windows.clear()
        ingest._record_counts.clear()
        ingest.latest_scores.clear()
        ingest.latest_records.clear()
        ingest._raw_writer = None

        ws = MagicMock()
        ws.send = MagicMock(return_value=None)

        with caplog.at_level(logging.WARNING, logger="live_analytics.ws_ingest"):
            await ingest._process_message(ws, "{ NOT JSON }")

        assert any("Malformed JSON" in r.message for r in caplog.records), \
            "Expected WARNING for malformed JSON"

    @pytest.mark.asyncio
    async def test_invalid_batch_schema_logs_warning(self, tmp_path, caplog):
        import live_analytics.app.ws_ingest as ingest
        ingest._windows.clear()
        ingest._record_counts.clear()
        ingest._raw_writer = None

        ws = MagicMock()
        ws.send = MagicMock(return_value=None)

        with caplog.at_level(logging.WARNING, logger="live_analytics.ws_ingest"):
            await ingest._process_message(ws, json.dumps({"records": "not-a-list"}))

        assert any("Payload validation failed" in r.message for r in caplog.records), \
            "Expected WARNING for schema validation failure"


# ─────────────────────────────────────────────────────────────────────────────
#  5. ws_ingest: DB failure during batch logs exception and does not crash
# ─────────────────────────────────────────────────────────────────────────────

class TestIngestDbFailure:
    @pytest.mark.asyncio
    async def test_db_error_in_batch_logs_and_continues(self, tmp_path, caplog):
        """If the DB write raises, the batch is dropped but no exception propagates."""
        import live_analytics.app.ws_ingest as ingest
        from live_analytics.app.models import TelemetryRecord

        ingest._windows.clear()
        ingest._record_counts.clear()
        ingest.latest_scores.clear()
        ingest.latest_records.clear()
        ingest._raw_writer = None

        rec = TelemetryRecord(**_make_record("db-fail-sess"))

        with patch(
            "live_analytics.app.ws_ingest.upsert_session",
            side_effect=sqlite3.OperationalError("disk I/O error"),
        ):
            ws = MagicMock()
            ws.send = MagicMock(return_value=None)
            payload = json.dumps({
                "records": [json.loads(rec.model_dump_json())],
                "count": 1,
                "sent_at": "2024-01-01T00:00:00Z",
            })

            with caplog.at_level(logging.ERROR, logger="live_analytics.ws_ingest"):
                # Must not raise
                await ingest._process_message(ws, payload)

        assert any(
            "DB error: could not upsert session" in r.message or
            "Failed to ingest batch" in r.message
            for r in caplog.records
        ), "Expected ERROR log when batch DB write fails"


# ─────────────────────────────────────────────────────────────────────────────
#  6. ws_ingest: raw_writer=None logs warning (degraded mode)
# ─────────────────────────────────────────────────────────────────────────────

class TestIngestDegradedModeNoWriter:
    @pytest.mark.asyncio
    async def test_no_raw_writer_logs_warning(self, tmp_path, caplog):
        import live_analytics.app.ws_ingest as ingest
        from live_analytics.app.models import TelemetryRecord

        ingest._windows.clear()
        ingest._record_counts.clear()
        ingest.latest_scores.clear()
        ingest.latest_records.clear()
        ingest._raw_writer = None  # explicitly no writer

        with (
            patch("live_analytics.app.ws_ingest.upsert_session"),
            patch("live_analytics.app.ws_ingest.increment_record_count"),
            patch("live_analytics.app.ws_ingest.update_latest_scores"),
            patch("live_analytics.app.ws_ingest.compute_scores", return_value=MagicMock(
                stress_score=0, risk_score=0, model_dump=lambda: {}, model_dump_json=lambda: "{}"
            )),
        ):
            rec = TelemetryRecord(**_make_record("degraded-sess"))
            ws = MagicMock()
            ws.send = MagicMock(return_value=None)
            payload = json.dumps({
                "records": [json.loads(rec.model_dump_json())],
                "count": 1,
                "sent_at": "2024-01-01T00:00:00Z",
            })

            with caplog.at_level(logging.WARNING, logger="live_analytics.ws_ingest"):
                await ingest._process_message(ws, payload)

        assert any("raw_writer is not initialised" in r.message for r in caplog.records), \
            "Expected WARNING about degraded mode (no raw_writer)"


# ─────────────────────────────────────────────────────────────────────────────
#  7. questionnaire/db: connect failure logs critical
# ─────────────────────────────────────────────────────────────────────────────

class TestQuestDbConnectFailure:
    def setup_method(self):
        from live_analytics.questionnaire import db as qdb
        qdb.close_pool()

    def teardown_method(self):
        from live_analytics.questionnaire import db as qdb
        qdb.close_pool()

    def test_connect_bad_path_logs_critical(self, tmp_path, caplog):
        from live_analytics.questionnaire.db import _connect
        bad = tmp_path / "no_such_dir" / "q.db"
        with caplog.at_level(logging.CRITICAL, logger="questionnaire.db"):
            with pytest.raises(sqlite3.OperationalError):
                _connect(bad)
        assert any("Cannot open questionnaire SQLite database" in r.message
                   for r in caplog.records), \
            "Expected CRITICAL log for bad questionnaire DB path"

    def test_init_db_bad_path_logs_critical(self, tmp_path, caplog):
        from live_analytics.questionnaire.db import init_db
        bad = tmp_path / "no_such_dir" / "q.db"
        with caplog.at_level(logging.CRITICAL, logger="questionnaire.db"):
            with pytest.raises(Exception):
                init_db(bad)
        assert any(
            "Cannot open questionnaire SQLite database" in r.message or
            "Failed to initialise questionnaire DB schema" in r.message
            for r in caplog.records
        ), "Expected CRITICAL log for questionnaire init_db failure"


# ─────────────────────────────────────────────────────────────────────────────
#  8. questionnaire/db: save_answer DB error logs and re-raises
# ─────────────────────────────────────────────────────────────────────────────

class TestQuestDbSaveFailure:
    def setup_method(self):
        from live_analytics.questionnaire import db as qdb
        qdb.close_pool()

    def teardown_method(self):
        from live_analytics.questionnaire import db as qdb
        qdb.close_pool()

    def test_save_answer_db_error_logs_and_raises(self, tmp_path, caplog):
        from live_analytics.questionnaire.db import init_db, save_answer, _connect
        db = tmp_path / "q.db"
        init_db(db)

        # Drop the table to provoke a real sqlite3.Error
        conn = _connect(db)
        conn.execute("DROP TABLE questionnaire_responses")
        conn.commit()

        with caplog.at_level(logging.ERROR, logger="questionnaire.db"):
            with pytest.raises(sqlite3.Error):
                save_answer(db, "p1", "pre", "q1", "answer")

        assert any("DB error saving answer" in r.message for r in caplog.records), \
            "Expected ERROR log for save_answer DB failure"

    def test_save_answers_bulk_db_error_logs_and_raises(self, tmp_path, caplog):
        from live_analytics.questionnaire.db import init_db, save_answers_bulk, _connect
        db = tmp_path / "q.db"
        init_db(db)

        conn = _connect(db)
        conn.execute("DROP TABLE questionnaire_responses")
        conn.commit()

        with caplog.at_level(logging.ERROR, logger="questionnaire.db"):
            with pytest.raises(sqlite3.Error):
                save_answers_bulk(db, "p1", "pre", {"q1": "a", "q2": "b"})

        assert any("DB error bulk-saving" in r.message for r in caplog.records), \
            "Expected ERROR log for save_answers_bulk DB failure"


# ─────────────────────────────────────────────────────────────────────────────
#  9. dashboard: _get() logs on first failure and on recovery
# ─────────────────────────────────────────────────────────────────────────────

class TestDashboardGetLogs:
    """Verify that _get() emits the right log levels on failure/recovery."""

    def setup_method(self):
        import live_analytics.dashboard.streamlit_app as dash
        dash._api_consecutive_failures = 0
        dash._api_was_reachable = True

    def test_first_failure_logs_warning(self, caplog):
        import requests
        import live_analytics.dashboard.streamlit_app as dash

        with patch.object(
            dash._http_session(),
            "get",
            side_effect=requests.ConnectionError("connection refused"),
        ):
            with caplog.at_level(logging.WARNING, logger="live_analytics_dashboard"):
                result = dash._get("/api/sessions")

        assert result is None
        assert any("unreachable" in r.message.lower() for r in caplog.records), \
            "Expected WARNING about backend unreachable on first failure"
        assert dash._api_consecutive_failures == 1

    def test_subsequent_failures_do_not_flood_logs(self, caplog):
        import requests
        import live_analytics.dashboard.streamlit_app as dash

        dash._api_consecutive_failures = 5  # already failing

        with patch.object(
            dash._http_session(),
            "get",
            side_effect=requests.ConnectionError("still down"),
        ):
            caplog.clear()
            with caplog.at_level(logging.WARNING, logger="live_analytics_dashboard"):
                dash._get("/api/sessions")

        # Failure #6 should NOT emit a WARNING (only every 10th)
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 0, \
            "Should not log WARNING on every consecutive failure (only 1st and every 10th)"


# ─────────────────────────────────────────────────────────────────────────────
#  10. ws_ingest: start_ingest_server bind failure logs critical
# ─────────────────────────────────────────────────────────────────────────────

class TestIngestServerBindFailure:
    @pytest.mark.asyncio
    async def test_bind_failure_logs_critical_and_reraises(self, caplog):
        import live_analytics.app.ws_ingest as ingest

        with patch(
            "live_analytics.app.ws_ingest.websockets.serve",
            side_effect=OSError(98, "Address already in use"),
        ):
            with caplog.at_level(logging.CRITICAL, logger="live_analytics.ws_ingest"):
                with pytest.raises(OSError):
                    await ingest.start_ingest_server()

        assert any(
            "failed to bind" in r.message.lower() or "Ingest WS server failed" in r.message
            for r in caplog.records
        ), "Expected CRITICAL log when ingest server cannot bind"
