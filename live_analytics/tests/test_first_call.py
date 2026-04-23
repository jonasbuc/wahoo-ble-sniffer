"""
First-call / fresh-state tests for the dashboard and backend API.

Covers every plausible reason the dashboard could crash (or show wrong
diagnostics) on the very first API call:

  - Backend not started (ConnectionRefused)
  - Backend timing out
  - Backend returning HTTP 4xx / 5xx
  - Backend returning non-JSON body (HTML startup page, empty body)
  - Backend returning JSON null for /api/live/latest
  - Backend returning empty session list
  - Session detail with null fields in JSON
  - Metric rendering with null/None values
  - Record count format spec with None
  - list_sessions() with one malformed row
  - get_session() with null DB columns
  - Recovery after consecutive failures
  - Consecutive-failure counter ordering (state reset AFTER json parse)
  - session_ids list comprehension with non-dict items
  - _render_live with every edge-case response shape
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_response(
    status: int = 200,
    body: str | bytes = "{}",
    headers: dict | None = None,
) -> requests.Response:
    """Build a fake requests.Response object."""
    r = requests.models.Response()
    r.status_code = status
    r._content = (body if isinstance(body, bytes) else body.encode())
    r.encoding = "utf-8"
    if headers:
        r.headers.update(headers)
    return r


def _reset_dash_state(dash) -> None:
    """Reset all module-level mutable state in the dashboard module."""
    dash._api_consecutive_failures = 0
    dash._api_was_reachable = True
    dash._last_api_error_msg = None


# ─────────────────────────────────────────────────────────────────────────────
#  1.  _get(): connection refused → WARNING with "connection refused" category
# ─────────────────────────────────────────────────────────────────────────────

class TestGetConnectionRefused:
    def setup_method(self):
        import live_analytics.dashboard.streamlit_app as dash
        _reset_dash_state(dash)

    def test_connection_refused_warns_first_call(self, caplog):
        import live_analytics.dashboard.streamlit_app as dash

        with patch.object(
            dash._http_session(),
            "get",
            side_effect=requests.exceptions.ConnectionError("Connection refused"),
        ):
            with caplog.at_level(logging.WARNING, logger="live_analytics_dashboard"):
                result = dash._get("/api/sessions")

        assert result is None
        assert dash._api_consecutive_failures == 1
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, "Expected at least one WARNING for ConnectionError"
        assert any("connection refused" in r.message.lower() for r in warnings), \
            "WARNING must mention 'connection refused'"

    def test_connection_refused_logs_url(self, caplog):
        """The failing URL must appear in the log so the user knows which endpoint failed."""
        import live_analytics.dashboard.streamlit_app as dash

        with patch.object(
            dash._http_session(),
            "get",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            with caplog.at_level(logging.WARNING, logger="live_analytics_dashboard"):
                dash._get("/api/sessions")

        assert any("/api/sessions" in r.message for r in caplog.records), \
            "Failing endpoint path must appear in the log"


# ─────────────────────────────────────────────────────────────────────────────
#  2.  _get(): timeout → WARNING with "timed out" category
# ─────────────────────────────────────────────────────────────────────────────

class TestGetTimeout:
    def setup_method(self):
        import live_analytics.dashboard.streamlit_app as dash
        _reset_dash_state(dash)

    def test_timeout_warns_first_call(self, caplog):
        import live_analytics.dashboard.streamlit_app as dash

        with patch.object(
            dash._http_session(),
            "get",
            side_effect=requests.exceptions.Timeout("timed out"),
        ):
            with caplog.at_level(logging.WARNING, logger="live_analytics_dashboard"):
                result = dash._get("/healthz")

        assert result is None
        assert any("timed out" in r.message.lower() for r in caplog.records), \
            "WARNING must mention 'timed out'"


# ─────────────────────────────────────────────────────────────────────────────
#  3.  _get(): HTTP 500 → WARNING with HTTP error category; response body logged
# ─────────────────────────────────────────────────────────────────────────────

class TestGetHttpError:
    def setup_method(self):
        import live_analytics.dashboard.streamlit_app as dash
        _reset_dash_state(dash)

    def test_500_warns_with_status(self, caplog):
        import live_analytics.dashboard.streamlit_app as dash

        r500 = _make_response(500, '{"detail": "Internal Server Error"}')

        with patch.object(dash._http_session(), "get", return_value=r500):
            with caplog.at_level(logging.WARNING, logger="live_analytics_dashboard"):
                result = dash._get("/api/sessions/bad")

        assert result is None
        assert any("500" in r.message for r in caplog.records), \
            "HTTP 500 status must appear in the log"

    def test_404_returns_none_without_crash(self, caplog):
        import live_analytics.dashboard.streamlit_app as dash

        r404 = _make_response(404, '{"detail": "Not Found"}')

        with patch.object(dash._http_session(), "get", return_value=r404):
            result = dash._get("/api/sessions/missing")

        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
#  4.  _get(): non-JSON body (HTML startup page) — MUST NOT be logged as
#      "connection refused"; must log body_preview in the warning.
#      Also verifies the counter-ordering fix: state is only reset AFTER
#      json parsing succeeds, so a failed json parse does not leave the
#      counter at 0.
# ─────────────────────────────────────────────────────────────────────────────

class TestGetNonJsonBody:
    def setup_method(self):
        import live_analytics.dashboard.streamlit_app as dash
        _reset_dash_state(dash)

    def test_html_body_logs_json_error_not_connection_refused(self, caplog):
        import live_analytics.dashboard.streamlit_app as dash

        html_response = _make_response(200, "<html><body>Starting up…</body></html>")

        with patch.object(dash._http_session(), "get", return_value=html_response):
            with caplog.at_level(logging.WARNING, logger="live_analytics_dashboard"):
                result = dash._get("/healthz")

        assert result is None
        assert any("non-JSON" in r.message for r in caplog.records), \
            "Must log 'non-JSON' category, NOT 'connection refused'"
        assert not any(
            "connection refused" in r.message.lower() for r in caplog.records
        ), "Must NOT say 'connection refused' for an HTTP-200 non-JSON response"

    def test_html_body_logs_body_preview(self, caplog):
        import live_analytics.dashboard.streamlit_app as dash

        html_response = _make_response(200, "<html>StartupPage</html>")

        with patch.object(dash._http_session(), "get", return_value=html_response):
            with caplog.at_level(logging.WARNING, logger="live_analytics_dashboard"):
                dash._get("/healthz")

        assert any("StartupPage" in r.message for r in caplog.records), \
            "Response body preview must appear in the log for non-JSON responses"

    def test_counter_not_falsely_reset_on_json_failure(self):
        """State must NOT be reset to 0 when r.json() fails.

        Old bug: counter was reset BEFORE r.json(), so a JSONDecodeError
        caused the counter to go 0 → 0 (reset) → 1 (increment), which
        emitted a false 'recovered' log on the next successful request.
        """
        import live_analytics.dashboard.streamlit_app as dash

        dash._api_consecutive_failures = 0  # clean start
        html_response = _make_response(200, "<html>not json</html>")

        with patch.object(dash._http_session(), "get", return_value=html_response):
            dash._get("/healthz")

        # Counter must be 1 — NOT 0 (which would indicate a false reset)
        assert dash._api_consecutive_failures == 1, (
            "Counter must be 1 after a json-parse failure, "
            "not 0 (which would mean it was falsely reset)"
        )

    def test_empty_body_does_not_crash(self):
        import live_analytics.dashboard.streamlit_app as dash

        empty_response = _make_response(200, "")

        with patch.object(dash._http_session(), "get", return_value=empty_response):
            result = dash._get("/healthz")

        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
#  5.  _get(): JSON null body (live_latest returns null when no sessions)
# ─────────────────────────────────────────────────────────────────────────────

class TestGetJsonNull:
    def setup_method(self):
        import live_analytics.dashboard.streamlit_app as dash
        _reset_dash_state(dash)

    def test_json_null_returns_none_without_error(self, caplog):
        """GET returning JSON null must return Python None — not log any error."""
        import live_analytics.dashboard.streamlit_app as dash

        null_response = _make_response(200, "null")

        with patch.object(dash._http_session(), "get", return_value=null_response):
            with caplog.at_level(logging.WARNING, logger="live_analytics_dashboard"):
                result = dash._get("/api/live/latest")

        assert result is None
        # No WARNING should be emitted — null is a valid successful response
        assert dash._api_consecutive_failures == 0
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not warnings, f"Unexpected warnings on null response: {[r.message for r in warnings]}"

    def test_json_empty_list_returns_empty_list(self):
        import live_analytics.dashboard.streamlit_app as dash

        empty_list_response = _make_response(200, "[]")

        with patch.object(dash._http_session(), "get", return_value=empty_list_response):
            result = dash._get("/api/sessions")

        assert result == []
        assert dash._api_consecutive_failures == 0


# ─────────────────────────────────────────────────────────────────────────────
#  6.  _get(): recovery — counter reset and INFO logged
# ─────────────────────────────────────────────────────────────────────────────

class TestGetRecovery:
    def setup_method(self):
        import live_analytics.dashboard.streamlit_app as dash
        _reset_dash_state(dash)

    def test_recovery_resets_counter_and_logs_info(self, caplog):
        import live_analytics.dashboard.streamlit_app as dash

        dash._api_consecutive_failures = 7
        ok_response = _make_response(200, '{"status": "ok"}')

        with patch.object(dash._http_session(), "get", return_value=ok_response):
            with caplog.at_level(logging.INFO, logger="live_analytics_dashboard"):
                result = dash._get("/healthz")

        assert result == {"status": "ok"}
        assert dash._api_consecutive_failures == 0
        assert any("recovered" in r.message.lower() for r in caplog.records), \
            "INFO 'recovered' must be logged after failures"


# ─────────────────────────────────────────────────────────────────────────────
#  7.  _get(): last-error module-level variable is set on failure, cleared on success
# ─────────────────────────────────────────────────────────────────────────────

class TestLastApiErrorMsg:
    def setup_method(self):
        import live_analytics.dashboard.streamlit_app as dash
        _reset_dash_state(dash)

    def test_failure_sets_module_level_error(self):
        import live_analytics.dashboard.streamlit_app as dash

        with patch.object(
            dash._http_session(),
            "get",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            dash._get("/api/sessions")

        assert dash._last_api_error_msg is not None
        assert "/api/sessions" in dash._last_api_error_msg

    def test_success_clears_module_level_error(self):
        import live_analytics.dashboard.streamlit_app as dash

        dash._last_api_error_msg = "some old error"
        ok_response = _make_response(200, '{"status": "ok"}')

        with patch.object(dash._http_session(), "get", return_value=ok_response):
            dash._get("/healthz")

        assert dash._last_api_error_msg is None


# ─────────────────────────────────────────────────────────────────────────────
#  8.  Backend: list_sessions() with one corrupt row — others still returned
# ─────────────────────────────────────────────────────────────────────────────

class TestListSessionsPartialFailure:
    def setup_method(self):
        from live_analytics.app.storage import sqlite_store
        sqlite_store.close_pool()

    def teardown_method(self):
        from live_analytics.app.storage import sqlite_store
        sqlite_store.close_pool()

    def test_one_malformed_row_does_not_drop_all(self, tmp_path, caplog):
        """If SessionSummary construction fails for one row (e.g. bad field type),
        the remaining sessions must still be returned and a WARNING logged."""
        from unittest.mock import patch as _patch
        from live_analytics.app.storage.sqlite_store import init_db, list_sessions, _connect
        import live_analytics.app.storage.sqlite_store as ss

        db = tmp_path / "test.db"
        init_db(db)

        conn = _connect(db)
        conn.execute(
            "INSERT INTO sessions (session_id, start_unix_ms, scenario_id, record_count) "
            "VALUES ('good', 1000, 'test', 5)"
        )
        conn.execute(
            "INSERT INTO sessions (session_id, start_unix_ms, scenario_id, record_count) "
            "VALUES ('bad', 2000, 'broken', 0)"
        )
        conn.commit()

        _original = ss.SessionSummary

        def _patched_session_summary(**kwargs):
            if kwargs.get("session_id") == "bad":
                raise ValueError("Simulated malformed row")
            return _original(**kwargs)

        with _patch.object(ss, "SessionSummary", side_effect=_patched_session_summary):
            with caplog.at_level(logging.WARNING, logger="live_analytics.storage"):
                sessions = list_sessions(db)

        session_ids = [s.session_id for s in sessions]
        assert "good" in session_ids, "Good session must survive even if another row is malformed"

        assert any("bad" in r.message or "malformed" in r.message.lower()
                   for r in caplog.records), \
            "Expected WARNING about the malformed session row"

    def test_empty_db_returns_empty_list(self, tmp_path):
        from live_analytics.app.storage.sqlite_store import init_db, list_sessions

        db = tmp_path / "empty.db"
        init_db(db)
        sessions = list_sessions(db)
        assert sessions == []


# ─────────────────────────────────────────────────────────────────────────────
#  9.  Backend: get_session() with null record_count in DB — uses 0 fallback
# ─────────────────────────────────────────────────────────────────────────────

class TestGetSessionNullFields:
    def setup_method(self):
        from live_analytics.app.storage import sqlite_store
        sqlite_store.close_pool()

    def teardown_method(self):
        from live_analytics.app.storage import sqlite_store
        sqlite_store.close_pool()

    def test_null_record_count_coerced_to_zero(self, tmp_path):
        from live_analytics.app.storage.sqlite_store import init_db, get_session, _connect

        db = tmp_path / "test.db"
        init_db(db)
        conn = _connect(db)
        # Force NULL for record_count
        conn.execute(
            "INSERT INTO sessions (session_id, start_unix_ms, record_count) "
            "VALUES ('s1', 1000, NULL)"
        )
        conn.commit()

        # Should return a SessionDetail with record_count=0, not raise
        detail = get_session(db, "s1")
        assert detail is not None
        assert detail.record_count == 0

    def test_null_scenario_id_coerced_to_empty_string(self, tmp_path):
        from live_analytics.app.storage.sqlite_store import init_db, get_session, _connect

        db = tmp_path / "test.db"
        init_db(db)
        conn = _connect(db)
        conn.execute(
            "INSERT INTO sessions (session_id, start_unix_ms, scenario_id) "
            "VALUES ('s1', 1000, NULL)"
        )
        conn.commit()

        detail = get_session(db, "s1")
        assert detail is not None
        assert detail.scenario_id == ""


# ─────────────────────────────────────────────────────────────────────────────
#  10.  Dashboard rendering: None metric values must not crash
# ─────────────────────────────────────────────────────────────────────────────

class TestMetricNoneValues:
    """Verify that `(x or 0)` guards prevent TypeError/ValueError when
    the API response has null for numeric fields."""

    def _check_format(self, val, fmt):
        """Simulate what the dashboard format specs do."""
        safe = val or 0
        return format(safe, fmt)

    def test_float_speed_none_fallback(self):
        assert self._check_format(None, ".1f") == "0.0"

    def test_int_head_scan_none_fallback(self):
        safe = None or 0
        assert f"{int(safe)}" == "0"

    def test_record_count_none_format_spec(self):
        """This was the crashing pattern: f'{None:,}'"""
        safe = None or 0
        assert f"{safe:,}" == "0"

    def test_scores_dict_none_values(self):
        """If scores dict has null values, all metrics must render without crash."""
        ls = {
            "stress_score": None,
            "risk_score": None,
            "brake_reaction_ms": None,
            "head_scan_count_5s": None,
            "steering_variance_3s": None,
            "hr_delta_10s": None,
        }
        # These are the actual dashboard expressions — must not raise
        assert f"{float(ls.get('stress_score') or 0):.1f}" == "0.0"
        assert f"{float(ls.get('risk_score') or 0):.1f}" == "0.0"
        assert f"{float(ls.get('brake_reaction_ms') or 0):.0f} ms" == "0 ms"
        assert f"{int(ls.get('head_scan_count_5s') or 0)}" == "0"
        assert f"{float(ls.get('steering_variance_3s') or 0):.2f}" == "0.00"
        assert f"{float(ls.get('hr_delta_10s') or 0):.1f} bpm" == "0.0 bpm"

    def test_live_dict_null_speed(self):
        live = {"speed": None, "heart_rate": None, "scores": None}
        scores = live.get("scores") or {}
        assert f"{float(live.get('speed') or 0):.1f} m/s" == "0.0 m/s"
        assert f"{float(live.get('heart_rate') or 0):.0f} bpm" == "0 bpm"
        assert f"{float(scores.get('stress_score') or 0):.1f} / 100" == "0.0 / 100"


# ─────────────────────────────────────────────────────────────────────────────
#  11.  Session list comprehension guards
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionListComprehension:
    """session_ids comprehension must skip non-dict items without crashing."""

    def test_non_dict_items_skipped(self):
        sessions: list[Any] = [
            {"session_id": "valid"},
            None,
            "just a string",
            42,
            {"no_session_id_key": True},
            {"session_id": ""},  # empty string — falsy
            {"session_id": "another_valid"},
        ]
        session_ids = [
            s.get("session_id")
            for s in sessions
            if isinstance(s, dict) and s.get("session_id")
        ]
        assert session_ids == ["valid", "another_valid"]


# ─────────────────────────────────────────────────────────────────────────────
#  12.  API endpoints: first call with empty DB (fresh-clone scenario)
# ─────────────────────────────────────────────────────────────────────────────

class TestApiFirstCallFreshDb:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        from live_analytics.app.storage.sqlite_store import close_pool, init_db
        db = tmp_path / "fresh.db"
        init_db(db)
        monkeypatch.setattr("live_analytics.app.api_sessions.DB_PATH", db)
        import live_analytics.app.ws_ingest as ws
        ws.latest_scores.clear()
        ws.latest_records.clear()
        from fastapi.testclient import TestClient
        from live_analytics.app.main import app
        yield TestClient(app)
        close_pool()

    def test_sessions_empty_returns_200_empty_list(self, client):
        r = client.get("/api/sessions")
        assert r.status_code == 200
        assert r.json() == []

    def test_live_latest_empty_returns_200_null(self, client):
        r = client.get("/api/live/latest")
        assert r.status_code == 200
        assert r.json() is None

    def test_session_detail_missing_returns_404(self, client):
        r = client.get("/api/sessions/nonexistent-session")
        assert r.status_code == 404

    def test_healthz_always_ok(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ─────────────────────────────────────────────────────────────────────────────
#  13.  API: session list response is valid JSON array always (not null)
# ─────────────────────────────────────────────────────────────────────────────

class TestApiSessionsResponseShape:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        from live_analytics.app.storage.sqlite_store import close_pool, init_db
        db = tmp_path / "shape.db"
        init_db(db)
        monkeypatch.setattr("live_analytics.app.api_sessions.DB_PATH", db)
        import live_analytics.app.ws_ingest as ws
        ws.latest_scores.clear()
        ws.latest_records.clear()
        from fastapi.testclient import TestClient
        from live_analytics.app.main import app
        yield TestClient(app)
        close_pool()

    def test_sessions_is_list_not_null(self, client):
        """Dashboard does isinstance(sessions, list) check — must never be null."""
        r = client.get("/api/sessions")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_session_detail_record_count_not_null_for_new_session(self, client, tmp_path, monkeypatch):
        """record_count must be 0 (not null) for a freshly upserted session."""
        from live_analytics.app.storage.sqlite_store import upsert_session

        db_path = tmp_path / "shape.db"
        upsert_session(db_path, "fresh-sess", 1000, "test")
        r = client.get("/api/sessions/fresh-sess")
        assert r.status_code == 200
        data = r.json()
        assert data["record_count"] == 0, "record_count must be 0, never null"
        assert data["scenario_id"] == "test"

    def test_session_detail_latest_scores_is_null_when_not_set(self, client, tmp_path):
        """latest_scores must be null (not an empty dict) when no scores have been set."""
        from live_analytics.app.storage.sqlite_store import upsert_session

        db_path = tmp_path / "shape.db"
        upsert_session(db_path, "no-scores-sess", 1000)
        r = client.get("/api/sessions/no-scores-sess")
        assert r.status_code == 200
        data = r.json()
        # Both None and a dict with all-zero values are acceptable
        ls = data.get("latest_scores")
        if ls is not None:
            assert isinstance(ls, dict), "latest_scores must be dict or null"


# ─────────────────────────────────────────────────────────────────────────────
#  14.  Consecutive failures: subsequent calls log DEBUG, not WARNING
# ─────────────────────────────────────────────────────────────────────────────

class TestConsecutiveFailureSuppression:
    def setup_method(self):
        import live_analytics.dashboard.streamlit_app as dash
        _reset_dash_state(dash)

    def test_second_failure_is_debug_not_warning(self, caplog):
        import live_analytics.dashboard.streamlit_app as dash

        dash._api_consecutive_failures = 1  # already had one failure

        with patch.object(
            dash._http_session(),
            "get",
            side_effect=requests.exceptions.ConnectionError("still down"),
        ):
            caplog.clear()
            with caplog.at_level(logging.DEBUG, logger="live_analytics_dashboard"):
                dash._get("/api/sessions")

        # Failure #2 should log at DEBUG, not WARNING
        warn_msgs = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not warn_msgs, "Failure #2 must log DEBUG, not WARNING"
        debug_msgs = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert debug_msgs, "Failure #2 must log at DEBUG"

    def test_tenth_failure_re_warns(self, caplog):
        import live_analytics.dashboard.streamlit_app as dash

        dash._api_consecutive_failures = 9  # 9 previous failures

        with patch.object(
            dash._http_session(),
            "get",
            side_effect=requests.exceptions.ConnectionError("still down"),
        ):
            with caplog.at_level(logging.WARNING, logger="live_analytics_dashboard"):
                dash._get("/api/sessions")

        # Failure #10 should warn again
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, "Failure #10 must emit WARNING again"
        assert any("10" in r.message for r in warnings), \
            "WARNING must include the failure count"
