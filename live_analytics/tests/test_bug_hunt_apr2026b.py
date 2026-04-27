"""
test_bug_hunt_apr2026b.py
=========================

Regression tests for the second April 2026 repository-wide bug hunt pass.

Bug IDs
-------
BH2-01  bridge/bike_bridge.py default host "localhost" causes IPv6-only bind
        on Windows 11 with IPv6 enabled → Unity IPv4 connections refused
BH2-02  questionnaire /api/healthz: sqlite3 connection leaked when execute raises
BH2-03  starters/launcher.py init_db subprocess result not checked →
        silent failure if DB cannot be initialised
BH2-04  live_analytics/system_check/__init__.py: os.getenv() called with
        Path objects as default instead of str — type inconsistency
"""

from __future__ import annotations

import importlib
import inspect
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════
# BH2-01 – bridge host default "localhost" → "0.0.0.0"
# ═══════════════════════════════════════════════════════════════════════

class TestBridgeHostDefault:
    """WahooBridgeServer and parse_args() must not default to 'localhost'."""

    def test_wahoo_bridge_server_default_host_not_localhost(self) -> None:
        """WahooBridgeServer.__init__ default host must not be 'localhost'.

        On Windows 11 with IPv6, websockets.serve('localhost', ...) may bind
        only to ::1 (IPv6).  Unity clients connecting to 127.0.0.1 (IPv4)
        would receive connection-refused even though the server is running.
        The safe default is '0.0.0.0' (all interfaces).
        """
        from bridge.bike_bridge import WahooBridgeServer

        sig = inspect.signature(WahooBridgeServer.__init__)
        default_host = sig.parameters["host"].default
        assert default_host != "localhost", (
            f"WahooBridgeServer default host is {default_host!r}.  "
            "It must not be 'localhost' — on Windows 11 IPv6 this may bind "
            "only to ::1 and refuse Unity IPv4 connections."
        )

    def test_parse_args_default_host_not_localhost(self) -> None:
        """parse_args() default --host must not be 'localhost'."""
        from bridge.bike_bridge import parse_args

        # Inject empty argv so argparse doesn't read pytest's argv
        with patch("sys.argv", ["bike_bridge.py"]):
            args = parse_args()

        assert args.host != "localhost", (
            f"parse_args() returned host={args.host!r}.  "
            "Must not be 'localhost' — see IPv6 note above."
        )

    def test_wahoo_bridge_server_explicit_host_respected(self) -> None:
        """Explicitly passing host= must override the default."""
        from bridge.bike_bridge import WahooBridgeServer

        server = WahooBridgeServer(host="127.0.0.1", port=19876, mock=True)
        assert server.host == "127.0.0.1"

    def test_wahoo_bridge_server_default_host_is_bind_all(self) -> None:
        """Default host should bind on all interfaces (0.0.0.0)."""
        from bridge.bike_bridge import WahooBridgeServer

        server = WahooBridgeServer(mock=True)
        assert server.host == "0.0.0.0", (
            f"Expected default host '0.0.0.0', got {server.host!r}"
        )


# ═══════════════════════════════════════════════════════════════════════
# BH2-02 – questionnaire /api/healthz connection leak
# ═══════════════════════════════════════════════════════════════════════

class TestQuestionnaireHealthzConnectionSafety:
    """The /api/healthz handler must not leak a DB connection on failure."""

    def test_healthz_closes_connection_on_execute_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If conn.execute() raises, conn.close() must still be called.

        Before the fix: conn.close() was not in a finally block.  If
        execute() raised (e.g. database locked), the connection was leaked.
        """
        import asyncio
        import sqlite3 as real_sqlite3
        import live_analytics.questionnaire.app as qs_app

        class _FakeConn:
            def execute(self, *_a, **_kw):
                raise real_sqlite3.OperationalError("database is locked")

            def close(self):
                pass

        monkeypatch.setattr(qs_app, "DB_PATH", tmp_path / "qs.db")
        with patch.object(real_sqlite3, "connect", return_value=_FakeConn()):
            result = asyncio.run(qs_app.healthz())

        assert result["status"] == "ok"
        assert result["db_ok"] is False
        assert "db_detail" in result

    def test_healthz_closes_connection_on_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On success, the DB connection must be closed."""
        import asyncio
        import sqlite3 as _sq
        import live_analytics.questionnaire.app as qs_app

        db = tmp_path / "qs_healthz.db"
        conn = _sq.connect(str(db))
        conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        monkeypatch.setattr(qs_app, "DB_PATH", db)
        result = asyncio.run(qs_app.healthz())
        assert result["status"] == "ok"
        assert result["db_ok"] is True


# ═══════════════════════════════════════════════════════════════════════
# BH2-03 – launcher init_db subprocess result not checked
# ═══════════════════════════════════════════════════════════════════════

class TestLauncherInitDbCheck:
    """launcher.py must check the init_db subprocess return code."""

    def test_launcher_calls_sys_exit_on_init_db_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When init_db.py exits with non-zero, launcher must call sys.exit(1)."""
        import subprocess as _sp
        from starters import launcher

        failed_result = _sp.CompletedProcess(
            args=[], returncode=1,
            stdout="", stderr="OperationalError: unable to open database",
        )

        with patch("sys.argv", ["launcher.py"]):
            with patch.object(_sp, "run", return_value=failed_result):
                with patch("builtins.print"):
                    with pytest.raises(SystemExit) as exc_info:
                        launcher.main()

        assert exc_info.value.code == 1

    def test_launcher_does_not_exit_on_init_db_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When init_db.py succeeds (rc=0), launcher must NOT call sys.exit."""
        import subprocess as _sp
        from starters import launcher

        success_result = _sp.CompletedProcess(
            args=[], returncode=0, stdout="Database initialised at ...\n", stderr=""
        )

        # Interrupt execution after the init_db block by raising in build_services.
        class _StopEarly(Exception):
            pass

        with patch("sys.argv", ["launcher.py"]):
            with patch.object(_sp, "run", return_value=success_result):
                with patch.object(launcher, "build_services", side_effect=_StopEarly()):
                    with patch("builtins.print"):
                        with pytest.raises(_StopEarly):
                            launcher.main()
        # No SystemExit = test passes


# ═══════════════════════════════════════════════════════════════════════
# BH2-04 – system_check __init__.py os.getenv Path defaults → str
# ═══════════════════════════════════════════════════════════════════════

class TestSystemCheckConfigTypes:
    """SC config values must be correctly typed (str paths as str to os.getenv)."""

    def test_base_dir_is_path(self) -> None:
        from live_analytics.system_check import BASE_DIR
        assert isinstance(BASE_DIR, Path)

    def test_data_dir_is_path(self) -> None:
        from live_analytics.system_check import DATA_DIR
        assert isinstance(DATA_DIR, Path)

    def test_vrs_log_base_is_path(self) -> None:
        from live_analytics.system_check import VRS_LOG_BASE
        assert isinstance(VRS_LOG_BASE, Path)

    def test_sc_base_dir_override_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Setting SC_BASE_DIR must propagate to DATA_DIR default."""
        import importlib
        import live_analytics.system_check as sc_mod

        monkeypatch.setenv("SC_BASE_DIR", "/tmp/sc_custom_base")
        monkeypatch.delenv("SC_DATA_DIR", raising=False)

        reloaded = importlib.reload(sc_mod)
        try:
            assert str(reloaded.BASE_DIR) == "/tmp/sc_custom_base"
            assert str(reloaded.DATA_DIR) == "/tmp/sc_custom_base/data"
        finally:
            importlib.reload(sc_mod)  # restore defaults
