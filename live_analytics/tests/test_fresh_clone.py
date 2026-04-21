"""
Tests for launcher scripts and configuration files – verify ports,
paths, and settings are consistent across the project.

Also covers fresh-clone bootstrap guarantees:
  - .gitkeep files ensure data dirs survive a git clone
  - ensure_dirs() creates missing directories idempotently
  - init_db creates the expected tables in an empty SQLite database
  - backfill_from_jsonl inserts sessions from JSONL without duplicates
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestStreamlitConfig:
    """Verify .streamlit/config.toml exists at repo root and has required settings."""

    def test_config_exists_at_repo_root(self):
        cfg = REPO_ROOT / ".streamlit" / "config.toml"
        assert cfg.exists(), f".streamlit/config.toml not found at {cfg}"

    def test_xsrf_protection_disabled(self):
        cfg = REPO_ROOT / ".streamlit" / "config.toml"
        content = cfg.read_text()
        assert "enableXsrfProtection = false" in content, (
            "enableXsrfProtection must be false to allow fragment auto-refresh"
        )

    def test_cors_disabled(self):
        cfg = REPO_ROOT / ".streamlit" / "config.toml"
        content = cfg.read_text()
        assert "enableCORS = false" in content


class TestSimulateRidePort:
    """Verify simulate_ride.py connects to the correct ingest port."""

    def test_uses_port_8766(self):
        script = REPO_ROOT / "live_analytics" / "scripts" / "simulate_ride.py"
        content = script.read_text()
        assert "8766" in content, "simulate_ride.py should connect to port 8766"
        assert "8765" not in content, "simulate_ride.py should NOT reference old port 8765"


class TestRunServerScript:
    """Verify run_server.ps1 references correct ports."""

    def test_banner_shows_8766(self):
        script = REPO_ROOT / "live_analytics" / "scripts" / "run_server.ps1"
        content = script.read_text()
        assert "8766" in content, "run_server.ps1 should show port 8766"
        assert "8765" not in content, "run_server.ps1 should NOT reference old port 8765"


class TestRunDashboardScript:
    """Verify run_dashboard.ps1 sets CWD to repo root for config discovery."""

    def test_cwd_set_to_repo_root(self):
        script = REPO_ROOT / "live_analytics" / "scripts" / "run_dashboard.ps1"
        content = script.read_text()
        assert "RepoRoot" in content, (
            "run_dashboard.ps1 must set CWD to repo root so "
            ".streamlit/config.toml is found by Streamlit"
        )


class TestFreshCloneGitkeep:
    """Verify data directories have .gitkeep so they exist after clone."""

    def test_analytics_data_dir(self):
        p = REPO_ROOT / "live_analytics" / "data" / ".gitkeep"
        assert p.exists(), "live_analytics/data/.gitkeep missing"

    def test_analytics_sessions_dir(self):
        p = REPO_ROOT / "live_analytics" / "data" / "sessions" / ".gitkeep"
        assert p.exists(), "live_analytics/data/sessions/.gitkeep missing"

    def test_system_check_data_dir(self):
        p = REPO_ROOT / "live_analytics" / "system_check" / "data" / ".gitkeep"
        assert p.exists(), "system_check/data/.gitkeep missing"

    def test_questionnaire_data_dir(self):
        p = REPO_ROOT / "live_analytics" / "questionnaire" / "data" / ".gitkeep"
        assert p.exists(), "questionnaire/data/.gitkeep missing"


class TestEnsureDirs:
    """ensure_dirs() must create directories idempotently from a cold state."""

    def test_analytics_ensure_dirs_creates_dirs(self, tmp_path):
        import os
        from unittest.mock import patch
        from live_analytics.app import config as cfg

        data_dir = tmp_path / "data"
        sessions_dir = data_dir / "sessions"
        assert not data_dir.exists()
        assert not sessions_dir.exists()

        with (
            patch.object(cfg, "DATA_DIR", data_dir),
            patch.object(cfg, "SESSIONS_DIR", sessions_dir),
        ):
            cfg.ensure_dirs()

        assert data_dir.is_dir()
        assert sessions_dir.is_dir()

    def test_analytics_ensure_dirs_idempotent(self, tmp_path):
        from unittest.mock import patch
        from live_analytics.app import config as cfg

        data_dir = tmp_path / "data"
        sessions_dir = data_dir / "sessions"
        with (
            patch.object(cfg, "DATA_DIR", data_dir),
            patch.object(cfg, "SESSIONS_DIR", sessions_dir),
        ):
            cfg.ensure_dirs()
            cfg.ensure_dirs()  # second call must not raise

        assert sessions_dir.is_dir()

    def test_questionnaire_ensure_dirs(self, tmp_path):
        from unittest.mock import patch
        from live_analytics.questionnaire import config as qcfg

        data_dir = tmp_path / "qdata"
        with patch.object(qcfg, "DATA_DIR", data_dir):
            qcfg.ensure_dirs()
        assert data_dir.is_dir()

    def test_system_check_ensure_dirs(self, tmp_path):
        import live_analytics.system_check as sccfg
        from unittest.mock import patch

        data_dir = tmp_path / "scdata"
        with patch.object(sccfg, "DATA_DIR", data_dir):
            sccfg.ensure_dirs()
        assert data_dir.is_dir()


class TestInitDb:
    """init_db must create the expected schema in an empty database."""

    def test_creates_sessions_table(self, tmp_path):
        from live_analytics.app.storage.sqlite_store import init_db, close_pool

        db = tmp_path / "test.db"
        init_db(db)
        close_pool()

        conn = sqlite3.connect(str(db))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()

        assert "sessions" in tables
        assert "events" in tables

    def test_idempotent(self, tmp_path):
        from live_analytics.app.storage.sqlite_store import init_db, close_pool

        db = tmp_path / "test2.db"
        init_db(db)
        init_db(db)  # second call must not raise or wipe data
        close_pool()

        conn = sqlite3.connect(str(db))
        count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        conn.close()
        assert count == 0


class TestBackfillFromJsonl:
    """backfill_from_jsonl.py must insert sessions from JSONL without duplicates."""

    def _make_session(self, sessions_dir: Path, session_id: str, n_records: int = 3) -> Path:
        d = sessions_dir / session_id
        d.mkdir(parents=True, exist_ok=True)
        jsonl = d / "telemetry.jsonl"
        with open(jsonl, "w", encoding="utf-8") as f:
            for i in range(n_records):
                rec = {
                    "session_id": session_id,
                    "unix_ms": 1_700_000_000_000 + i * 1000,
                    "unity_time": float(i),
                    "scenario_id": "test_scenario",
                    "speed": 10.0,
                    "steering_angle": 0.0,
                    "brake_front": 0,
                    "brake_rear": 0,
                    "heart_rate": 70.0,
                    "head_pos_x": 0.0, "head_pos_y": 0.0, "head_pos_z": 0.0,
                    "head_rot_x": 0.0, "head_rot_y": 0.0, "head_rot_z": 0.0, "head_rot_w": 1.0,
                    "trigger_id": "",
                    "record_type": "gameplay",
                }
                f.write(json.dumps(rec) + "\n")
        return jsonl

    def test_inserts_new_sessions(self, tmp_path):
        from live_analytics.app.storage.sqlite_store import init_db, list_sessions, close_pool
        from live_analytics.scripts.backfill_from_jsonl import backfill

        sessions_dir = tmp_path / "sessions"
        db = tmp_path / "test.db"
        self._make_session(sessions_dir, "session_001", n_records=5)
        self._make_session(sessions_dir, "session_002", n_records=3)

        init_db(db)
        n = backfill(db, sessions_dir)
        assert n == 2

        rows = list_sessions(db)
        close_pool()
        ids = {r.session_id for r in rows}
        assert ids == {"session_001", "session_002"}

    def test_skips_existing_sessions(self, tmp_path):
        from live_analytics.app.storage.sqlite_store import init_db, upsert_session, list_sessions, close_pool
        from live_analytics.scripts.backfill_from_jsonl import backfill

        sessions_dir = tmp_path / "sessions"
        db = tmp_path / "test.db"
        self._make_session(sessions_dir, "session_001")

        init_db(db)
        upsert_session(db, "session_001", 1_700_000_000_000, "existing")
        n = backfill(db, sessions_dir)
        assert n == 0  # already in DB, must be skipped

        rows = list_sessions(db)
        close_pool()
        assert len(rows) == 1

    def test_dry_run_does_not_write(self, tmp_path):
        from live_analytics.app.storage.sqlite_store import init_db, list_sessions, close_pool
        from live_analytics.scripts.backfill_from_jsonl import backfill

        sessions_dir = tmp_path / "sessions"
        db = tmp_path / "test.db"
        self._make_session(sessions_dir, "session_dry")

        init_db(db)
        n = backfill(db, sessions_dir, dry_run=True)
        assert n == 1  # reports 1 would-be insert

        rows = list_sessions(db)
        close_pool()
        assert len(rows) == 0  # nothing actually written

    def test_handles_empty_sessions_dir(self, tmp_path):
        from live_analytics.app.storage.sqlite_store import init_db, close_pool
        from live_analytics.scripts.backfill_from_jsonl import backfill

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        db = tmp_path / "test.db"
        init_db(db)
        n = backfill(db, sessions_dir)
        close_pool()
        assert n == 0

    def test_handles_missing_sessions_dir(self, tmp_path):
        from live_analytics.app.storage.sqlite_store import init_db, close_pool
        from live_analytics.scripts.backfill_from_jsonl import backfill

        sessions_dir = tmp_path / "does_not_exist"
        db = tmp_path / "test.db"
        init_db(db)
        n = backfill(db, sessions_dir)
        close_pool()
        assert n == 0

    def test_skips_session_with_no_jsonl(self, tmp_path):
        from live_analytics.app.storage.sqlite_store import init_db, list_sessions, close_pool
        from live_analytics.scripts.backfill_from_jsonl import backfill

        sessions_dir = tmp_path / "sessions"
        # Session dir with no telemetry.jsonl file inside
        (sessions_dir / "empty_session").mkdir(parents=True)
        db = tmp_path / "test.db"
        init_db(db)
        n = backfill(db, sessions_dir)
        rows = list_sessions(db)
        close_pool()
        assert n == 0
        assert len(rows) == 0

    def test_record_count_stored(self, tmp_path):
        from live_analytics.app.storage.sqlite_store import init_db, list_sessions, close_pool
        from live_analytics.scripts.backfill_from_jsonl import backfill

        sessions_dir = tmp_path / "sessions"
        db = tmp_path / "test.db"
        self._make_session(sessions_dir, "session_count", n_records=7)

        init_db(db)
        backfill(db, sessions_dir)
        rows = list_sessions(db)
        close_pool()
        assert rows[0].record_count == 7
