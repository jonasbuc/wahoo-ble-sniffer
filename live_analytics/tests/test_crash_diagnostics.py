"""
Tests that verify crash diagnostics are not silent.

Every major failure path must log useful information rather than
silently swallowing exceptions.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
LA_APP = REPO / "live_analytics" / "app"
LA_DASH = REPO / "live_analytics" / "dashboard"


def _read(relpath: str) -> str:
    return (REPO / relpath).read_text()


class TestNoSilentExceptPass:
    """No 'except ...: pass' anywhere in production code."""

    _PROD_FILES = [
        "live_analytics/app/ws_ingest.py",
        "live_analytics/app/api_sessions.py",
        "live_analytics/app/main.py",
        "live_analytics/app/config.py",
        "live_analytics/app/storage/sqlite_store.py",
        "live_analytics/app/storage/raw_writer.py",
        "live_analytics/dashboard/streamlit_app.py",
    ]

    def test_no_bare_except_pass(self):
        """Search for 'except ...: pass' with no logging."""
        import re
        for relpath in self._PROD_FILES:
            code = _read(relpath)
            # Find except blocks that contain only 'pass' (single-line or next-line)
            matches = re.findall(
                r"except\s+\w[\w\s,|]*:\s*\n\s*pass\s*$",
                code,
                re.MULTILINE,
            )
            # close_pool is OK (teardown utility)
            filtered = [m for m in matches if "close" not in m]
            assert not filtered, (
                f"Silent 'except: pass' in {relpath}:\n" +
                "\n".join(filtered)
            )


class TestValidationLogsIncludeContext:
    """Validation/parse failure logs must include what went wrong."""

    def test_batch_validation_logs_exc_type(self):
        code = _read("live_analytics/app/ws_ingest.py")
        assert "type(exc).__name__" in code or "exc_info" in code, (
            "Payload validation failure must log exception type"
        )

    def test_live_latest_failure_not_debug(self):
        code = _read("live_analytics/app/api_sessions.py")
        # Should be warning level, not debug
        assert 'logger.debug("live_latest' not in code, (
            "live_latest failure should log at WARNING, not DEBUG"
        )

    def test_sort_dedup_failure_logged(self):
        code = _read("live_analytics/dashboard/streamlit_app.py")
        assert "Failed to sort/dedup" in code, (
            "sort/dedup failure must log a message, not silently pass"
        )


class TestStartupDiagnostics:
    """Startup must produce useful config diagnostics."""

    def test_main_logs_config_at_startup(self):
        code = _read("live_analytics/app/main.py")
        assert "DB_PATH" in code and "SESSIONS_DIR" in code
        assert "Startup" in code

    def test_main_logs_shutdown(self):
        code = _read("live_analytics/app/main.py")
        assert "Shutdown" in code

    def test_dashboard_logs_startup_config(self):
        code = _read("live_analytics/dashboard/streamlit_app.py")
        assert "Dashboard startup" in code
        assert "API_BASE" in code
        assert "REFRESH_SEC" in code
        assert "DATA_DIR" in code


class TestConfigLogsInvalidValues:
    """Config helpers must log when they fall back from invalid values."""

    def test_int_env_logs_warning(self):
        code = _read("live_analytics/app/config.py")
        assert "Invalid int" in code

    def test_float_env_logs_warning(self):
        code = _read("live_analytics/app/config.py")
        assert "Invalid float" in code
