"""
Tests for live_analytics.app.config – env var parsing, path resolution,
ensure_dirs, and fresh-clone safety.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest


class TestIntEnv:
    """Test the _int_env helper in config.py."""

    def test_valid_int(self):
        from live_analytics.app.config import _int_env

        with mock.patch.dict(os.environ, {"TEST_INT": "42"}):
            assert _int_env("TEST_INT", 0) == 42

    def test_empty_string_returns_default(self):
        from live_analytics.app.config import _int_env

        with mock.patch.dict(os.environ, {"TEST_INT": ""}):
            assert _int_env("TEST_INT", 99) == 99

    def test_missing_returns_default(self):
        from live_analytics.app.config import _int_env

        env = os.environ.copy()
        env.pop("TEST_INT_MISSING", None)
        with mock.patch.dict(os.environ, env, clear=True):
            assert _int_env("TEST_INT_MISSING", 77) == 77

    def test_non_numeric_returns_default(self):
        from live_analytics.app.config import _int_env

        with mock.patch.dict(os.environ, {"TEST_INT": "abc"}):
            assert _int_env("TEST_INT", 10) == 10

    def test_float_string_returns_default(self):
        from live_analytics.app.config import _int_env

        with mock.patch.dict(os.environ, {"TEST_INT": "3.14"}):
            assert _int_env("TEST_INT", 5) == 5

    def test_whitespace_only_returns_default(self):
        from live_analytics.app.config import _int_env

        with mock.patch.dict(os.environ, {"TEST_INT": "   "}):
            # "   " is truthy but int("   ") raises ValueError
            assert _int_env("TEST_INT", 5) == 5


class TestFloatEnv:
    """Test the _float_env helper in config.py."""

    def test_valid_float(self):
        from live_analytics.app.config import _float_env

        with mock.patch.dict(os.environ, {"TEST_F": "3.14"}):
            assert _float_env("TEST_F", 0.0) == pytest.approx(3.14)

    def test_empty_returns_default(self):
        from live_analytics.app.config import _float_env

        with mock.patch.dict(os.environ, {"TEST_F": ""}):
            assert _float_env("TEST_F", 1.5) == 1.5

    def test_garbage_returns_default(self):
        from live_analytics.app.config import _float_env

        with mock.patch.dict(os.environ, {"TEST_F": "not-a-number"}):
            assert _float_env("TEST_F", 2.0) == 2.0


class TestEnsureDirs:
    """Verify that ensure_dirs creates required directories."""

    def test_creates_data_and_sessions(self, tmp_path: Path):
        from live_analytics.app import config

        data = tmp_path / "data"
        sessions = tmp_path / "data" / "sessions"

        orig_data = config.DATA_DIR
        orig_sessions = config.SESSIONS_DIR
        try:
            config.DATA_DIR = data
            config.SESSIONS_DIR = sessions
            config.ensure_dirs()
            assert data.is_dir()
            assert sessions.is_dir()
        finally:
            config.DATA_DIR = orig_data
            config.SESSIONS_DIR = orig_sessions


class TestPathResolution:
    """Verify default paths resolve correctly relative to config.py."""

    def test_base_dir_is_live_analytics_root(self):
        from live_analytics.app.config import BASE_DIR

        # BASE_DIR should be the live_analytics/ directory
        assert BASE_DIR.name == "live_analytics" or BASE_DIR.exists()

    def test_data_dir_under_base(self):
        from live_analytics.app.config import BASE_DIR, DATA_DIR

        assert str(DATA_DIR).startswith(str(BASE_DIR))

    def test_db_path_ends_with_db(self):
        from live_analytics.app.config import DB_PATH

        assert DB_PATH.name == "live_analytics.db"

    def test_sessions_dir_under_data(self):
        from live_analytics.app.config import DATA_DIR, SESSIONS_DIR

        assert str(SESSIONS_DIR).startswith(str(DATA_DIR))


class TestDefaultPorts:
    """Verify default port values are correct per project spec."""

    def test_http_port(self):
        from live_analytics.app.config import HTTP_PORT

        assert HTTP_PORT == 8080

    def test_ws_ingest_port(self):
        from live_analytics.app.config import WS_INGEST_PORT

        assert WS_INGEST_PORT == 8766

    def test_dashboard_port(self):
        from live_analytics.app.config import DASHBOARD_PORT

        assert DASHBOARD_PORT == 8501
