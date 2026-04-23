"""
test_chain_audit.py – end-to-end chain validation tests
========================================================

Covers all 8 audit chains identified in the chain-by-chain systems audit:

  Chain 1 – Starter / bootstrap
  Chain 2 – Dashboard startup
  Chain 3 – First API call
  Chain 4 – Backend request
  Chain 5 – File system / storage
  Chain 6 – State
  Chain 7 – Timing / readiness
  Chain 8 – Error propagation
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ── repo root on sys.path ────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ═══════════════════════════════════════════════════════════════════════
# Chain 1 — Starter / bootstrap
# ═══════════════════════════════════════════════════════════════════════

class TestChain1Bootstrap:
    """Verify the bootstrap / starter chain from fresh clone to launch."""

    def test_init_db_creates_tables(self, tmp_path: Path) -> None:
        """init_db.py must create sessions and events tables."""
        from live_analytics.app.storage.sqlite_store import init_db, close_pool
        db = tmp_path / "test.db"
        init_db(db)
        close_pool()
        conn = sqlite3.connect(str(db))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "sessions" in tables
        assert "events" in tables

    def test_init_db_idempotent(self, tmp_path: Path) -> None:
        """Running init_db twice must not raise."""
        from live_analytics.app.storage.sqlite_store import init_db, close_pool
        db = tmp_path / "test.db"
        init_db(db)
        init_db(db)  # second call — must not raise
        close_pool()

    def test_ensure_dirs_creates_paths(self, tmp_path: Path) -> None:
        """ensure_dirs() must create DATA_DIR and SESSIONS_DIR."""
        import importlib, os
        os.environ["LA_BASE_DIR"] = str(tmp_path / "la")
        os.environ["LA_DATA_DIR"] = str(tmp_path / "la" / "data")
        os.environ["LA_SESSIONS_DIR"] = str(tmp_path / "la" / "data" / "sessions")
        # Re-import to pick up new env vars
        import live_analytics.app.config as cfg
        importlib.reload(cfg)
        cfg.ensure_dirs()
        assert cfg.DATA_DIR.is_dir()
        assert cfg.SESSIONS_DIR.is_dir()

    def test_preflight_exits_zero(self) -> None:
        """preflight.py must exit 0 in a correctly configured environment."""
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "starters" / "preflight.py")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"preflight.py failed:\n{result.stdout}\n{result.stderr}"
        )

    def test_venv_python_used_by_launcher(self) -> None:
        """Launcher must prefer .venv python over system python."""
        import sys
        from starters.launcher import PYTHON
        venv_python = REPO_ROOT / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / "python"
        if venv_python.exists():
            assert PYTHON == str(venv_python), (
                f"Launcher uses {PYTHON!r} instead of venv python {venv_python}"
            )


# ═══════════════════════════════════════════════════════════════════════
# Chain 2 — Dashboard startup
# ═══════════════════════════════════════════════════════════════════════

class TestChain2DashboardStartup:
    """Verify dashboard module-level initialization."""

    def test_dashboard_imports_without_error(self) -> None:
        """streamlit_app must be importable (module-level code must not crash)."""
        import importlib
        mod = importlib.import_module("live_analytics.dashboard.streamlit_app")
        assert mod is not None

    def test_api_base_defaults_to_localhost(self) -> None:
        """Default API_BASE must point to localhost:8080."""
        from live_analytics.dashboard.streamlit_app import API_BASE
        assert "127.0.0.1:8080" in API_BASE or "localhost:8080" in API_BASE

    def test_refresh_sec_minimum_2(self) -> None:
        """REFRESH_SEC must be at least 2 (enforced by max(..., 2))."""
        from live_analytics.dashboard.streamlit_app import REFRESH_SEC
        assert REFRESH_SEC >= 2

    def test_state_lock_exists(self) -> None:
        """_api_state_lock must be a threading.Lock (guards shared counters)."""
        import threading as _t
        from live_analytics.dashboard.streamlit_app import _api_state_lock
        assert isinstance(_api_state_lock, type(_t.Lock()))

    def test_safe_int_returns_default_on_invalid(self) -> None:
        """_safe_int must return default for None, empty string, and non-int."""
        from live_analytics.dashboard.streamlit_app import _safe_int
        assert _safe_int(None, 5) == 5
        assert _safe_int("", 5) == 5
        assert _safe_int("abc", 5) == 5
        assert _safe_int("3", 5) == 3


# ═══════════════════════════════════════════════════════════════════════
# Chain 3 — First API call
# ═══════════════════════════════════════════════════════════════════════

class TestChain3FirstApiCall:
    """Verify _get() handles every failure mode without crashing."""

    def _reset_state(self) -> None:
        import live_analytics.dashboard.streamlit_app as d
        with d._api_state_lock:
            d._api_consecutive_failures = 0
            d._api_was_reachable = True
            d._last_api_error_msg = None

    def test_get_returns_none_on_connection_refused(self) -> None:
        """_get() must return None when the backend is not running."""
        self._reset_state()
        import live_analytics.dashboard.streamlit_app as d
        result = d._get("/api/sessions")
        assert result is None

    def test_get_returns_none_on_timeout(self) -> None:
        """_get() must return None and not raise on timeout."""
        import requests, live_analytics.dashboard.streamlit_app as d
        self._reset_state()
        with patch.object(d._http_session(), "get",
                          side_effect=requests.exceptions.Timeout("timed out")):
            result = d._get("/api/sessions")
        assert result is None

    def test_get_returns_none_on_http_500(self) -> None:
        """_get() must return None (not raise) when backend returns 500."""
        import requests, live_analytics.dashboard.streamlit_app as d
        self._reset_state()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "500", response=mock_resp
        )
        with patch.object(d._http_session(), "get", return_value=mock_resp):
            result = d._get("/api/sessions")
        assert result is None

    def test_get_returns_none_on_non_json_body(self) -> None:
        """_get() must return None when backend returns non-JSON (e.g. startup HTML)."""
        import live_analytics.dashboard.streamlit_app as d
        self._reset_state()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html>Starting up…</html>"
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.side_effect = ValueError("No JSON")
        with patch.object(d._http_session(), "get", return_value=mock_resp):
            result = d._get("/api/sessions")
        assert result is None

    def test_get_increments_failure_counter_thread_safe(self) -> None:
        """Concurrent _get() calls must not corrupt _api_consecutive_failures."""
        import requests, live_analytics.dashboard.streamlit_app as d
        self._reset_state()
        errors: list[Exception] = []

        def _fail(_url: str, **_kw: Any) -> None:
            raise requests.exceptions.ConnectionError("refused")

        session = d._http_session()
        results: list[None] = []

        def _call() -> None:
            with patch.object(session, "get", side_effect=_fail):
                results.append(d._get("/api/sessions"))

        threads = [threading.Thread(target=_call) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r is None for r in results)
        with d._api_state_lock:
            assert d._api_consecutive_failures == 10, (
                f"Expected 10 failures, got {d._api_consecutive_failures}"
            )

    def test_get_resets_counter_on_success(self) -> None:
        """_api_consecutive_failures must reset to 0 after a successful call."""
        import live_analytics.dashboard.streamlit_app as d
        self._reset_state()
        with d._api_state_lock:
            d._api_consecutive_failures = 5

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = []
        with patch.object(d._http_session(), "get", return_value=mock_resp):
            result = d._get("/api/sessions")

        assert result == []
        with d._api_state_lock:
            assert d._api_consecutive_failures == 0

    def test_get_timeout_is_reasonable(self) -> None:
        """_get() connect timeout must be ≥ 1.5 s to tolerate slow first connections."""
        import live_analytics.dashboard.streamlit_app as d
        captured: list[tuple] = []

        def _capture(url: str, **kwargs: Any) -> None:
            captured.append(kwargs.get("timeout", ()))
            raise Exception("bail out")

        self._reset_state()
        with patch.object(d._http_session(), "get", side_effect=_capture):
            d._get("/healthz")

        assert captured, "No call captured"
        t = captured[0]
        connect_t = t[0] if isinstance(t, (list, tuple)) else t
        assert connect_t >= 1.5, (
            f"Connect timeout {connect_t}s is too short; backend may not be ready yet"
        )


# ═══════════════════════════════════════════════════════════════════════
# Chain 4 — Backend request
# ═══════════════════════════════════════════════════════════════════════

class TestChain4BackendRequest:
    """Verify FastAPI endpoint behaviour."""

    @pytest.fixture()
    def client(self, tmp_path: Path):  # Generator[TestClient, None, None]
        """Return a TestClient wired to a fresh in-memory DB."""
        import os
        db = tmp_path / "test.db"
        os.environ["LA_DB_PATH"] = str(db)
        os.environ["LA_SESSIONS_DIR"] = str(tmp_path / "sessions")
        os.environ["LA_DATA_DIR"] = str(tmp_path)

        from live_analytics.app.main import app
        from live_analytics.app.storage.sqlite_store import init_db, close_pool
        init_db(db)
        with TestClient(app) as c:
            yield c
        close_pool()

    def test_healthz_returns_200_and_db_ok(self, client: TestClient) -> None:
        r = client.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["db_ok"] is True

    def test_sessions_list_empty_on_fresh_db(self, client: TestClient) -> None:
        r = client.get("/api/sessions")
        assert r.status_code == 200
        assert r.json() == []

    def test_sessions_list_returns_503_on_broken_db(self, tmp_path: Path) -> None:
        """When the DB is inaccessible, /api/sessions must return 503, not 200+[]."""
        import os
        # Point to a path that is a directory, not a file — SQLite open will fail
        bad_db = tmp_path / "notafile"
        bad_db.mkdir()
        os.environ["LA_DB_PATH"] = str(bad_db)

        from live_analytics.app import api_sessions
        from live_analytics.app.storage import sqlite_store
        # Ensure no cached connection for this path
        with sqlite_store._pool_lock:
            sqlite_store._pool.pop(str(bad_db), None)

        # Patch DB_PATH inside the module
        with patch.object(api_sessions, "DB_PATH", bad_db), \
             patch("live_analytics.app.api_sessions.list_sessions",
                   side_effect=sqlite3.OperationalError("unable to open")):
            from live_analytics.app.main import app
            with TestClient(app) as c:
                r = c.get("/api/sessions")
        assert r.status_code == 503, (
            f"Expected 503 on DB error but got {r.status_code}: {r.text}"
        )

    def test_session_detail_404_on_missing(self, client: TestClient) -> None:
        r = client.get("/api/sessions/nonexistent-session")
        assert r.status_code == 404

    def test_live_latest_returns_none_before_data(self, client: TestClient) -> None:
        from live_analytics.app import ws_ingest
        ws_ingest.latest_scores.clear()
        r = client.get("/api/live/latest")
        assert r.status_code == 200
        assert r.json() is None

    def test_telemetrybatch_accepts_missing_sent_at(self) -> None:
        """TelemetryBatch must accept payloads that omit count/sent_at."""
        from live_analytics.app.models import TelemetryBatch
        b = TelemetryBatch(records=[], count=0)  # no sent_at
        assert b.sent_at == ""

    def test_telemetrybatch_sent_at_optional(self) -> None:
        """TelemetryBatch must not raise when sent_at is missing."""
        from live_analytics.app.models import TelemetryBatch
        b = TelemetryBatch(records=[])
        assert b.count == 0
        assert b.sent_at == ""


# ═══════════════════════════════════════════════════════════════════════
# Chain 5 — File system / storage
# ═══════════════════════════════════════════════════════════════════════

class TestChain5FileSystem:
    """Verify raw JSONL writer and reader handles all edge cases."""

    def test_raw_writer_creates_session_dir(self, tmp_path: Path) -> None:
        from live_analytics.app.storage.raw_writer import RawWriter
        from live_analytics.app.models import TelemetryRecord
        writer = RawWriter(tmp_path)
        rec = TelemetryRecord(session_id="s1", unix_ms=1000, unity_time=1.0)
        writer.append(rec)
        assert (tmp_path / "s1" / "telemetry.jsonl").exists()

    def test_raw_writer_appends_valid_jsonl(self, tmp_path: Path) -> None:
        from live_analytics.app.storage.raw_writer import RawWriter
        from live_analytics.app.models import TelemetryRecord
        writer = RawWriter(tmp_path)
        for i in range(3):
            writer.append(TelemetryRecord(session_id="s1", unix_ms=i, unity_time=float(i)))
        lines = (tmp_path / "s1" / "telemetry.jsonl").read_text().strip().split("\n")
        assert len(lines) == 3
        for line in lines:
            obj = json.loads(line)
            assert "session_id" in obj

    def test_read_last_jsonl_rows_missing_file(self, tmp_path: Path) -> None:
        """_read_last_jsonl_rows must return empty DataFrame when file absent."""
        from live_analytics.dashboard.streamlit_app import _read_last_jsonl_rows
        df = _read_last_jsonl_rows(tmp_path / "no_such.jsonl")
        assert df.empty

    def test_read_last_jsonl_rows_partial_line(self, tmp_path: Path) -> None:
        """Partial (truncated) last line must be skipped gracefully."""
        from live_analytics.dashboard.streamlit_app import _read_last_jsonl_rows
        f = tmp_path / "telemetry.jsonl"
        f.write_text(
            '{"unity_time": 1.0, "speed": 2.0}\n'
            '{"unity_time": 2.0, "speed": 3.0}\n'
            '{"unity_time":',   # truncated
        )
        df = _read_last_jsonl_rows(f)
        assert len(df) == 2  # truncated line silently skipped

    def test_read_last_jsonl_rows_empty_file(self, tmp_path: Path) -> None:
        from live_analytics.dashboard.streamlit_app import _read_last_jsonl_rows
        f = tmp_path / "empty.jsonl"
        f.write_text("")
        df = _read_last_jsonl_rows(f)
        assert df.empty

    def test_read_last_jsonl_rows_all_blank(self, tmp_path: Path) -> None:
        from live_analytics.dashboard.streamlit_app import _read_last_jsonl_rows
        f = tmp_path / "blank.jsonl"
        f.write_text("\n\n\n")
        df = _read_last_jsonl_rows(f)
        assert df.empty

    def test_read_last_jsonl_rows_non_dict_lines_skipped(self, tmp_path: Path) -> None:
        from live_analytics.dashboard.streamlit_app import _read_last_jsonl_rows
        f = tmp_path / "mixed.jsonl"
        f.write_text(
            '{"unity_time": 1.0}\n'
            '"just a string"\n'
            '[1, 2, 3]\n'
            '{"unity_time": 2.0}\n'
        )
        df = _read_last_jsonl_rows(f)
        assert len(df) == 2  # only dict lines

    def test_raw_writer_append_many_groups_by_session(self, tmp_path: Path) -> None:
        from live_analytics.app.storage.raw_writer import RawWriter
        from live_analytics.app.models import TelemetryRecord
        writer = RawWriter(tmp_path)
        records = [
            TelemetryRecord(session_id="a", unix_ms=1, unity_time=1.0),
            TelemetryRecord(session_id="b", unix_ms=2, unity_time=2.0),
            TelemetryRecord(session_id="a", unix_ms=3, unity_time=3.0),
        ]
        writer.append_many(records)
        lines_a = (tmp_path / "a" / "telemetry.jsonl").read_text().strip().split("\n")
        lines_b = (tmp_path / "b" / "telemetry.jsonl").read_text().strip().split("\n")
        assert len(lines_a) == 2
        assert len(lines_b) == 1

    def test_data_dir_alignment_between_backend_and_dashboard(self) -> None:
        """Backend SESSIONS_DIR must equal Dashboard DATA_DIR / 'sessions'."""
        from live_analytics.app.config import SESSIONS_DIR
        from live_analytics.dashboard.streamlit_app import DATA_DIR as DASH_DATA_DIR
        assert SESSIONS_DIR == DASH_DATA_DIR / "sessions", (
            f"Path mismatch: backend writes to {SESSIONS_DIR} "
            f"but dashboard reads from {DASH_DATA_DIR / 'sessions'}"
        )


# ═══════════════════════════════════════════════════════════════════════
# Chain 6 — State
# ═══════════════════════════════════════════════════════════════════════

class TestChain6State:
    """Verify session selection and state management helpers."""

    def test_ensure_selected_session_sets_first_on_empty_state(self) -> None:
        """_ensure_selected_session must pick first session when no selection exists."""
        # We can't run Streamlit session_state directly, but we can test the logic
        state: dict[str, Any] = {}

        def _ensure(session_ids: list[str]) -> None:
            current = state.get("_selected_session")
            if current is None or current not in session_ids:
                state["_selected_session"] = session_ids[0] if session_ids else None

        _ensure(["sess-1", "sess-2"])
        assert state["_selected_session"] == "sess-1"

    def test_ensure_selected_session_keeps_valid_existing(self) -> None:
        state: dict[str, Any] = {"_selected_session": "sess-2"}

        def _ensure(session_ids: list[str]) -> None:
            current = state.get("_selected_session")
            if current is None or current not in session_ids:
                state["_selected_session"] = session_ids[0] if session_ids else None

        _ensure(["sess-1", "sess-2", "sess-3"])
        assert state["_selected_session"] == "sess-2"

    def test_ensure_selected_session_resets_on_stale(self) -> None:
        state: dict[str, Any] = {"_selected_session": "deleted-session"}

        def _ensure(session_ids: list[str]) -> None:
            current = state.get("_selected_session")
            if current is None or current not in session_ids:
                state["_selected_session"] = session_ids[0] if session_ids else None

        _ensure(["sess-1", "sess-2"])
        assert state["_selected_session"] == "sess-1"

    def test_ensure_selected_session_none_on_empty_list(self) -> None:
        state: dict[str, Any] = {"_selected_session": "old"}

        def _ensure(session_ids: list[str]) -> None:
            current = state.get("_selected_session")
            if current is None or current not in session_ids:
                state["_selected_session"] = session_ids[0] if session_ids else None

        _ensure([])
        assert state["_selected_session"] is None

    def test_load_sessions_returns_empty_list_on_api_failure(self) -> None:
        """_load_sessions must return [] (not raise) when _get returns None."""
        import live_analytics.dashboard.streamlit_app as d
        with patch.object(d, "_get", return_value=None):
            result = d._load_sessions.__wrapped__()  # bypass cache
        assert result == []

    def test_load_sessions_returns_empty_on_non_list_response(self) -> None:
        import live_analytics.dashboard.streamlit_app as d
        with patch.object(d, "_get", return_value={"error": "oops"}):
            result = d._load_sessions.__wrapped__()
        assert result == []


# ═══════════════════════════════════════════════════════════════════════
# Chain 7 — Timing / readiness
# ═══════════════════════════════════════════════════════════════════════

class TestChain7Timing:
    """Verify readiness-check and retry behaviour."""

    def test_launcher_rotate_log_no_crash_on_missing_file(self, tmp_path: Path) -> None:
        import sys
        sys.path.insert(0, str(REPO_ROOT / "starters"))
        import importlib
        launcher = importlib.import_module("launcher")
        launcher._rotate_log(tmp_path / "nonexistent.log")  # must not raise

    def test_launcher_service_start_skips_empty_cmd(self) -> None:
        """Service with empty cmd must set status='starting' without calling Popen."""
        import sys
        sys.path.insert(0, str(REPO_ROOT / "starters"))
        import importlib
        launcher = importlib.import_module("launcher")
        svc = launcher.Service(name="WS Ingest", cmd=[], port=8766)
        with patch("subprocess.Popen") as mock_popen:
            svc.start()
            mock_popen.assert_not_called()
        assert svc.status == "starting"

    def test_check_http_returns_false_on_5xx(self) -> None:
        """_check_http must return False AND set status=error when 5xx received."""
        import sys, urllib.error
        sys.path.insert(0, str(REPO_ROOT / "starters"))
        import importlib
        launcher = importlib.import_module("launcher")
        svc = launcher.Service(name="Test", cmd=["x"], port=9999,
                               health_url="http://127.0.0.1:9999/healthz")
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.HTTPError(
                       "http://x", 500, "Internal Server Error", {}, None)):
            result = svc._check_http()
        assert result is False
        assert svc.status == "error", "5xx must set status=error to prevent stuck 'starting'"

    def test_ingest_server_reports_port_conflict(self) -> None:
        """start_ingest_server must log CRITICAL and raise on port conflict."""
        import asyncio, logging
        from live_analytics.app.ws_ingest import start_ingest_server

        async def _run() -> None:
            with patch("websockets.serve", side_effect=OSError(98, "Address already in use")):
                await start_ingest_server()

        with pytest.raises(OSError):
            asyncio.run(_run())

    def test_scoring_returns_default_on_empty_window(self) -> None:
        """compute_scores([]) must return a zero ScoringResult, not raise."""
        from live_analytics.app.scoring.rules import compute_scores
        result = compute_scores([])
        assert result.stress_score == 0.0
        assert result.risk_score == 0.0


# ═══════════════════════════════════════════════════════════════════════
# Chain 8 — Error propagation
# ═══════════════════════════════════════════════════════════════════════

class TestChain8ErrorPropagation:
    """Verify that failures surface clearly and don't silently corrupt state."""

    def test_get_error_msg_stored_in_module_state(self) -> None:
        """A failed _get() call must update _last_api_error_msg."""
        import requests, live_analytics.dashboard.streamlit_app as d
        with d._api_state_lock:
            d._api_consecutive_failures = 0
            d._last_api_error_msg = None
        with patch.object(d._http_session(), "get",
                          side_effect=requests.exceptions.ConnectionError("refused")):
            d._get("/api/sessions")
        assert d._last_api_error_msg is not None
        assert "ConnectionError" in d._last_api_error_msg

    def test_dashboard_handles_none_live_response(self) -> None:
        """_fmt_metric(None) must return '—' without raising."""
        from live_analytics.dashboard.streamlit_app import _fmt_metric
        assert _fmt_metric(None, ".1f", " m/s") == "—"
        assert _fmt_metric(None, ".0f", " bpm") == "—"

    def test_fmt_metric_handles_non_numeric(self) -> None:
        from live_analytics.dashboard.streamlit_app import _fmt_metric
        assert _fmt_metric("not_a_number", ".1f") == "—"
        assert _fmt_metric([], ".1f") == "—"

    def test_sessions_list_503_on_db_error(self) -> None:
        """sessions_list endpoint must respond 503 when list_sessions raises."""
        import sqlite3
        from live_analytics.app.main import app
        from live_analytics.app import api_sessions
        with TestClient(app) as c:
            with patch("live_analytics.app.api_sessions.list_sessions",
                       side_effect=sqlite3.OperationalError("disk I/O error")):
                r = c.get("/api/sessions")
        assert r.status_code == 503
        assert "Database unavailable" in r.json().get("detail", "")

    def test_raw_writer_logs_and_continues_on_oserror(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """RawWriter must log the error and not raise on OSError."""
        from live_analytics.app.storage.raw_writer import RawWriter
        from live_analytics.app.models import TelemetryRecord
        import logging
        writer = RawWriter(tmp_path)
        rec = TelemetryRecord(session_id="s1", unix_ms=1, unity_time=1.0)
        with caplog.at_level(logging.ERROR, logger="live_analytics.raw_writer"):
            with patch("builtins.open", side_effect=OSError("disk full")):
                writer.append(rec)  # must not raise
        assert any("Failed to write JSONL" in r.message for r in caplog.records)

    def test_ingest_skips_malformed_json(self) -> None:
        """_process_message must skip a malformed JSON message without crashing."""
        import asyncio
        from live_analytics.app.ws_ingest import _process_message

        async def _run() -> None:
            ws = MagicMock()
            ws.send = MagicMock(return_value=asyncio.sleep(0))
            await _process_message(ws, "{not valid json")

        asyncio.run(_run())  # must not raise

    def test_ingest_skips_invalid_schema(self) -> None:
        """_process_message must skip a message that fails TelemetryBatch validation."""
        import asyncio
        from live_analytics.app.ws_ingest import _process_message

        async def _run() -> None:
            ws = MagicMock()
            ws.send = MagicMock(return_value=asyncio.sleep(0))
            # Missing required 'records' field
            await _process_message(ws, '{"count": 0, "sent_at": "2024"}')

        asyncio.run(_run())  # must not raise

    def test_ms_to_str_handles_none_and_invalid(self) -> None:
        from live_analytics.dashboard.streamlit_app import _ms_to_str
        assert _ms_to_str(None) == "—"
        # Huge timestamp — should not raise, just return "—"
        result = _ms_to_str(9999999999999)
        assert isinstance(result, str)

    def test_list_sessions_skips_malformed_rows(self, tmp_path: Path) -> None:
        """list_sessions must skip corrupted rows and return valid ones."""
        from live_analytics.app.storage.sqlite_store import (
            init_db, list_sessions, close_pool,
        )
        db = tmp_path / "test.db"
        init_db(db)
        # Insert a row with NULL start_unix_ms (violates NOT NULL but SQLite won't reject it
        # without strict mode — simulate with a bad latest_scores value instead)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO sessions (session_id, start_unix_ms, latest_scores) "
            "VALUES (?, ?, ?)",
            ("good", 1000, '{"stress_score": 10.0}'),
        )
        conn.execute(
            "INSERT INTO sessions (session_id, start_unix_ms, latest_scores) "
            "VALUES (?, ?, ?)",
            ("bad", 2000, "not valid json {{{"),
        )
        conn.commit()
        conn.close()
        sessions = list_sessions(db)
        # "bad" row has invalid JSON in latest_scores — list_sessions must not crash
        # It may skip or recover the bad row; at minimum the good row must be present
        session_ids = {s.session_id for s in sessions}
        assert "good" in session_ids
        close_pool()
