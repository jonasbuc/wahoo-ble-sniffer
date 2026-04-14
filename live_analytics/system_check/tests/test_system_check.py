"""
Tests for the System Check module.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from live_analytics.system_check.checks import (
    check_bridge_connection,
    check_database,
    check_quest_headset,
    check_service_http,
    check_session_by_id,
    check_vrsf_logs,
    run_all_checks,
)


# ═══════════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture()
def sample_db(tmp_path: Path) -> Path:
    """Create a minimal SQLite database for testing."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE telemetry (id INTEGER PRIMARY KEY, ts REAL, hr INTEGER)")
    conn.execute("INSERT INTO telemetry VALUES (1, 1000.0, 72)")
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    return db


@pytest.fixture()
def session_dirs(tmp_path: Path) -> Path:
    """Create a Logs/ directory with two session folders.
    session_001 is complete (newest), session_002 is incomplete (older).
    """
    logs = tmp_path / "Logs"
    logs.mkdir()

    # Incomplete session (older) — created first
    s2 = logs / "session_002"
    s2.mkdir()
    for fname in ["headpose.vrsf", "bike.vrsf", "events.vrsf"]:
        (s2 / fname).write_bytes(b"\x00" * 32)
    (s2 / "manifest.json").write_text(json.dumps({"session_id": "002", "started_unix_ms": int(time.time() * 1000)}))

    # Small delay to ensure different mtime
    import os
    past = time.time() - 100
    for f in s2.iterdir():
        os.utime(f, (past, past))
    os.utime(s2, (past, past))

    # Complete session (newer) — created second
    s1 = logs / "session_001"
    s1.mkdir()
    for fname in ["headpose.vrsf", "bike.vrsf", "hr.vrsf", "events.vrsf"]:
        (s1 / fname).write_bytes(b"\x00" * 64)
    manifest = {"session_id": "001", "started_unix_ms": int(time.time() * 1000), "files": {}}
    (s1 / "manifest.json").write_text(json.dumps(manifest))
    (s1 / "manifest_end.json").write_text(json.dumps({"ended": True}))

    return logs


# ═══════════════════════════════════════════════════════════════════════
#  check_database
# ═══════════════════════════════════════════════════════════════════════

class TestCheckDatabase:
    def test_valid_db(self, sample_db: Path) -> None:
        result = check_database(sample_db, "Test DB")
        assert result["ok"] is True
        assert "Test DB" in result["label"]
        assert len(result["tables"]) == 2
        assert result["size_kb"] > 0

    def test_missing_db(self, tmp_path: Path) -> None:
        result = check_database(tmp_path / "does_not_exist.db", "Missing")
        assert result["ok"] is False
        assert "ikke fundet" in result["detail"]

    def test_corrupt_db(self, tmp_path: Path) -> None:
        bad_db = tmp_path / "corrupt.db"
        bad_db.write_bytes(b"not a database file at all!")
        result = check_database(bad_db, "Corrupt")
        assert result["ok"] is False
        assert "fejl" in result["detail"].lower() or "sqlite" in result["detail"].lower()


# ═══════════════════════════════════════════════════════════════════════
#  check_quest_headset
# ═══════════════════════════════════════════════════════════════════════

class TestCheckQuestHeadset:
    def test_no_adb_in_path(self) -> None:
        with patch("shutil.which", return_value=None):
            result = check_quest_headset()
        assert result["ok"] is False
        assert "ADB" in result["detail"]

    def test_device_connected(self) -> None:
        fake_output = "List of devices attached\n1WMHH1234567\tdevice\n\n"
        model_output = "Quest 3"

        def fake_run(cmd, **kwargs):
            m = MagicMock()
            if "devices" in cmd:
                m.stdout = fake_output
            else:
                m.stdout = model_output
            return m

        with patch("shutil.which", return_value="/usr/bin/adb"), \
             patch("subprocess.run", side_effect=fake_run):
            result = check_quest_headset()
        assert result["ok"] is True
        assert result["model"] == "Quest 3"
        assert result["serial"] == "1WMHH1234567"

    def test_no_devices(self) -> None:
        fake_output = "List of devices attached\n\n"

        def fake_run(cmd, **kwargs):
            m = MagicMock()
            m.stdout = fake_output
            return m

        with patch("shutil.which", return_value="/usr/bin/adb"), \
             patch("subprocess.run", side_effect=fake_run):
            result = check_quest_headset()
        assert result["ok"] is False
        assert "Ingen headset" in result["detail"]

    def test_unauthorized_device(self) -> None:
        fake_output = "List of devices attached\nABC123\tunauthorized\n\n"

        def fake_run(cmd, **kwargs):
            m = MagicMock()
            m.stdout = fake_output
            return m

        with patch("shutil.which", return_value="/usr/bin/adb"), \
             patch("subprocess.run", side_effect=fake_run):
            result = check_quest_headset()
        assert result["ok"] is False
        assert "unauthorized" in result["detail"]

    def test_adb_timeout(self) -> None:
        import subprocess
        with patch("shutil.which", return_value="/usr/bin/adb"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("adb", 5)):
            result = check_quest_headset()
        assert result["ok"] is False
        assert "timeout" in result["detail"].lower()


# ═══════════════════════════════════════════════════════════════════════
#  check_bridge_connection
# ═══════════════════════════════════════════════════════════════════════

class TestCheckBridgeConnection:
    def test_connection_refused(self) -> None:
        result = check_bridge_connection("ws://127.0.0.1:19999")
        assert result["ok"] is False
        assert "Ingen forbindelse" in result["detail"]

    def test_port_open_mock(self) -> None:
        mock_sock = MagicMock()
        with patch("socket.create_connection", return_value=mock_sock), \
             patch("live_analytics.system_check.checks._ws_probe", return_value=None):
            result = check_bridge_connection("ws://localhost:8765")
        assert result["ok"] is True
        assert "Bridge" in result["detail"]

    def test_port_open_with_protocol(self) -> None:
        mock_sock = MagicMock()
        with patch("socket.create_connection", return_value=mock_sock), \
             patch("live_analytics.system_check.checks._ws_probe", return_value="binary"):
            result = check_bridge_connection("ws://localhost:8765")
        assert result["ok"] is True
        assert result["protocol"] == "binary"


# ═══════════════════════════════════════════════════════════════════════
#  check_vrsf_logs
# ═══════════════════════════════════════════════════════════════════════

class TestCheckVrsfLogs:
    def test_complete_session(self, session_dirs: Path) -> None:
        expected = ["headpose.vrsf", "bike.vrsf", "hr.vrsf", "events.vrsf", "manifest.json"]
        result = check_vrsf_logs(session_dirs, expected)
        assert result["ok"] is True
        assert result["total_sessions"] == 2
        assert len(result["sessions"]) == 2

    def test_no_log_dir(self, tmp_path: Path) -> None:
        result = check_vrsf_logs(tmp_path / "nonexistent")
        assert result["ok"] is False
        assert "ikke fundet" in result["detail"]

    def test_empty_log_dir(self, tmp_path: Path) -> None:
        logs = tmp_path / "Logs"
        logs.mkdir()
        result = check_vrsf_logs(logs)
        assert result["ok"] is False
        assert "Ingen session" in result["detail"]

    def test_incomplete_latest(self, tmp_path: Path) -> None:
        logs = tmp_path / "Logs"
        logs.mkdir()
        s = logs / "session_999"
        s.mkdir()
        (s / "headpose.vrsf").write_bytes(b"\x00" * 10)
        (s / "manifest.json").write_text('{"session_id": "999"}')

        expected = ["headpose.vrsf", "bike.vrsf", "hr.vrsf", "events.vrsf", "manifest.json"]
        result = check_vrsf_logs(logs, expected)
        assert result["ok"] is False
        assert "ufuldstændig" in result["detail"].lower()

    def test_session_metadata(self, session_dirs: Path) -> None:
        result = check_vrsf_logs(session_dirs)
        sessions = result["sessions"]
        assert any(s.get("session_id") == "001" for s in sessions)
        assert any(s.get("session_id") == "002" for s in sessions)


# ═══════════════════════════════════════════════════════════════════════
#  check_service_http
# ═══════════════════════════════════════════════════════════════════════

class TestCheckServiceHttp:
    def test_service_unreachable(self) -> None:
        result = check_service_http("http://127.0.0.1:19999", "Fake")
        assert result["ok"] is False
        assert "Ingen svar" in result["detail"]

    def test_service_reachable_mock(self) -> None:
        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.read.return_value = b'{"status":"ok"}'
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = check_service_http("http://localhost:8080", "Analytics")
        assert result["ok"] is True
        assert result["status"] == 200


# ═══════════════════════════════════════════════════════════════════════
#  run_all_checks
# ═══════════════════════════════════════════════════════════════════════

class TestRunAllChecks:
    def test_all_checks_return_summary(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.close()

        with patch("shutil.which", return_value=None):
            result = run_all_checks(
                analytics_db=db,
                questionnaire_db=db,
                bridge_ws_url="ws://127.0.0.1:19999",
                analytics_api_url="http://127.0.0.1:19999",
                questionnaire_api_url="http://127.0.0.1:19999",
                vrs_log_base=tmp_path / "no_logs",
            )

        assert "_summary" in result
        assert isinstance(result["_summary"]["total"], int)
        assert result["_summary"]["total"] == 7
        assert "quest_headset" in result
        assert "analytics_db" in result
        assert "bridge_connection" in result

    def test_summary_counts(self, sample_db: Path, session_dirs: Path) -> None:
        with patch("shutil.which", return_value=None):
            result = run_all_checks(
                analytics_db=sample_db,
                questionnaire_db=sample_db,
                bridge_ws_url="ws://127.0.0.1:19999",
                analytics_api_url="http://127.0.0.1:19999",
                questionnaire_api_url="http://127.0.0.1:19999",
                vrs_log_base=session_dirs,
            )

        s = result["_summary"]
        assert result["analytics_db"]["ok"] is True
        assert result["questionnaire_db"]["ok"] is True
        assert result["vrsf_logs"]["ok"] is True
        assert s["passed"] >= 3
        assert s["elapsed_s"] >= 0


# ═══════════════════════════════════════════════════════════════════════
#  FastAPI app tests
# ═══════════════════════════════════════════════════════════════════════

class TestAppEndpoints:
    @pytest.fixture(autouse=True)
    def _setup(self):
        from fastapi.testclient import TestClient
        from live_analytics.system_check.app import app
        self.client = TestClient(app)

    def test_healthz(self) -> None:
        resp = self.client.get("/api/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_index_html(self) -> None:
        resp = self.client.get("/")
        assert resp.status_code == 200
        assert "System Check" in resp.text

    def test_check_headset_endpoint(self) -> None:
        resp = self.client.get("/api/check/headset")
        assert resp.status_code == 200
        data = resp.json()
        assert "ok" in data
        assert "label" in data

    def test_check_bridge_endpoint(self) -> None:
        resp = self.client.get("/api/check/bridge")
        assert resp.status_code == 200
        data = resp.json()
        assert "ok" in data

    def test_check_all_endpoint(self) -> None:
        with patch("live_analytics.system_check.checks.check_quest_headset",
                    return_value={"ok": False, "label": "Headset", "detail": "mock"}), \
             patch("live_analytics.system_check.checks.check_bridge_connection",
                    return_value={"ok": False, "label": "Bridge", "detail": "mock"}):
            resp = self.client.get("/api/check/all")
        assert resp.status_code == 200
        data = resp.json()
        assert "_summary" in data

    def test_check_vrsf_logs_endpoint(self) -> None:
        resp = self.client.get("/api/check/vrsf-logs")
        assert resp.status_code == 200
        data = resp.json()
        assert "ok" in data

    def test_check_session_endpoint(self) -> None:
        resp = self.client.get("/api/check/session/NONEXISTENT")
        assert resp.status_code == 200
        data = resp.json()
        assert "ok" in data
        assert data["found"] is False


# ═══════════════════════════════════════════════════════════════════════
#  check_session_by_id
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture()
def rich_session_dirs(tmp_path: Path) -> Path:
    """Logs/ with sessions using display_id naming + sessions_history.ndjson."""
    logs = tmp_path / "Logs"
    logs.mkdir()

    # Session with display_id "SUBJ-001"
    s1 = logs / "session_SUBJ-001"
    s1.mkdir()
    for fname in ["headpose.vrsf", "bike.vrsf", "hr.vrsf", "events.vrsf"]:
        (s1 / fname).write_bytes(b"\x00" * 64)
    manifest1 = {
        "session_id": 1713000000000,
        "display_id": "SUBJ-001",
        "started_unix_ms": 1713000000000,
        "files": ["headpose.vrsf", "bike.vrsf", "hr.vrsf", "events.vrsf"],
    }
    (s1 / "manifest.json").write_text(json.dumps(manifest1))
    (s1 / "manifest_end.json").write_text(json.dumps({"ended": True}))

    # Session with numeric-only id, no display_id
    s2 = logs / "session_1713000060000"
    s2.mkdir()
    for fname in ["headpose.vrsf", "bike.vrsf"]:
        (s2 / fname).write_bytes(b"\x00" * 32)
    manifest2 = {
        "session_id": 1713000060000,
        "started_unix_ms": 1713000060000,
        "files": ["headpose.vrsf", "bike.vrsf"],
    }
    (s2 / "manifest.json").write_text(json.dumps(manifest2))

    # Session with empty .vrsf file
    s3 = logs / "session_SUBJ-002"
    s3.mkdir()
    for fname in ["headpose.vrsf", "bike.vrsf", "hr.vrsf", "events.vrsf"]:
        (s3 / fname).write_bytes(b"\x00" * 64 if fname != "hr.vrsf" else b"")
    manifest3 = {
        "session_id": 1713000120000,
        "display_id": "SUBJ-002",
        "started_unix_ms": 1713000120000,
    }
    (s3 / "manifest.json").write_text(json.dumps(manifest3))

    # sessions_history.ndjson
    history = [
        {"display_id": "SUBJ-001", "session_id": 1713000000000, "subject": "Jonas",
         "started_unix_ms": 1713000000000, "ended_unix_ms": 1713000050000,
         "dir": "session_SUBJ-001"},
        {"display_id": "SUBJ-002", "session_id": 1713000120000, "subject": "Alice",
         "started_unix_ms": 1713000120000, "ended_unix_ms": 0,
         "dir": "session_SUBJ-002"},
    ]
    ndjson = "\n".join(json.dumps(e) for e in history) + "\n"
    (logs / "sessions_history.ndjson").write_text(ndjson)

    return logs


class TestCheckSessionById:
    def test_find_by_dir_name(self, rich_session_dirs: Path) -> None:
        """Lookup by the display_id that's part of the directory name."""
        result = check_session_by_id("SUBJ-001", rich_session_dirs)
        assert result["ok"] is True
        assert result["found"] is True
        assert result["complete"] is True
        assert result["finished"] is True
        assert result["dir"] == "session_SUBJ-001"

    def test_find_by_numeric_session_id(self, rich_session_dirs: Path) -> None:
        """Lookup by the numeric session_id stored in manifest.json."""
        result = check_session_by_id("1713000000000", rich_session_dirs)
        assert result["ok"] is True
        assert result["found"] is True
        assert result["manifest"]["session_id"] == 1713000000000

    def test_find_by_numeric_dir(self, rich_session_dirs: Path) -> None:
        """Lookup for a session whose dir IS the numeric id."""
        result = check_session_by_id("1713000060000", rich_session_dirs)
        assert result["found"] is True
        assert result["ok"] is False  # missing hr.vrsf and events.vrsf
        assert len(result["missing_files"]) > 0

    def test_not_found(self, rich_session_dirs: Path) -> None:
        result = check_session_by_id("NONEXISTENT", rich_session_dirs)
        assert result["ok"] is False
        assert result["found"] is False

    def test_no_log_dir(self, tmp_path: Path) -> None:
        result = check_session_by_id("123", tmp_path / "nope")
        assert result["ok"] is False
        assert result["found"] is False

    def test_empty_vrsf_detected(self, rich_session_dirs: Path) -> None:
        """Session SUBJ-002 has an empty hr.vrsf — should be flagged."""
        result = check_session_by_id("SUBJ-002", rich_session_dirs)
        assert result["found"] is True
        assert result["ok"] is False
        assert "hr.vrsf" in result["empty_files"]

    def test_history_subject(self, rich_session_dirs: Path) -> None:
        """When found via history, subject info should be available."""
        # SUBJ-001 is found directly by dir, but SUBJ-002 also has history
        result = check_session_by_id("SUBJ-001", rich_session_dirs)
        # Direct match – history_entry is None (found by dir)
        assert result["found"] is True

    def test_find_via_manifest_display_id(self, rich_session_dirs: Path) -> None:
        """Rename dir so it doesn't match, forcing manifest scan."""
        # Create a session with a non-matching dir name
        logs = rich_session_dirs
        s = logs / "session_abc123"
        s.mkdir()
        for fname in ["headpose.vrsf", "bike.vrsf", "hr.vrsf", "events.vrsf"]:
            (s / fname).write_bytes(b"\x00" * 64)
        manifest = {
            "session_id": 9999999999999,
            "display_id": "PILOT-X",
            "started_unix_ms": 1713000200000,
        }
        (s / "manifest.json").write_text(json.dumps(manifest))

        # Look up by display_id "PILOT-X" – won't match dir name, must scan manifests
        result = check_session_by_id("PILOT-X", logs)
        assert result["found"] is True
        assert result["dir"] == "session_abc123"

    def test_find_via_history_ndjson(self, tmp_path: Path) -> None:
        """Session dir exists but has no manifest; found via history.ndjson dir field."""
        logs = tmp_path / "Logs"
        logs.mkdir()
        s = logs / "session_HIST-01"
        s.mkdir()
        for fname in ["headpose.vrsf", "bike.vrsf", "hr.vrsf", "events.vrsf", "manifest.json"]:
            (s / fname).write_bytes(b"\x00" * 32)

        history = [
            {"display_id": "HIST-01", "session_id": 5555555555555, "subject": "Bob",
             "started_unix_ms": 5555555555555, "ended_unix_ms": 0,
             "dir": "session_HIST-01"},
        ]
        (logs / "sessions_history.ndjson").write_text(json.dumps(history[0]) + "\n")

        # Look up by numeric id "5555555555555" — not in dir name, not in manifest (binary),
        # but present in history.ndjson
        result = check_session_by_id("5555555555555", logs)
        assert result["found"] is True
        assert result["history_entry"]["subject"] == "Bob"
