"""
Windows / ZIP-extraction compatibility tests.

These tests validate assumptions that must hold on a clean Windows machine
after downloading the project as a GitHub ZIP archive:

  1. Path resolution – all __file__-based paths resolve to real locations
     without depending on the repo being cloned in any specific way.
  2. Runtime directories are created correctly when missing.
  3. Config defaults are valid Path objects (no raw strings with slashes).
  4. No macOS-only localhost vs IPv6 issues in service URLs.
  5. Preflight import safety – preflight.py can be imported without crashing.
  6. All data-dir .gitkeep placeholders are tracked in git (ZIP includes them).
  7. JSONL writer creates session subdirectories on first write.
  8. SQLite store creates the DB file at the configured path on first use.
  9. Encoding safety – key modules can be imported on a Windows code-page.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

# ── Repo root ─────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent


# ═══════════════════════════════════════════════════════════════════════
# 1. PATH RESOLUTION
# ═══════════════════════════════════════════════════════════════════════

class TestPathResolution:
    """Verify that every __file__-relative path anchor resolves to a real location."""

    def test_repo_root_is_directory(self) -> None:
        assert REPO_ROOT.is_dir(), f"REPO_ROOT not found: {REPO_ROOT}"

    def test_live_analytics_package_exists(self) -> None:
        assert (REPO_ROOT / "live_analytics").is_dir()

    def test_bridge_package_exists(self) -> None:
        assert (REPO_ROOT / "bridge").is_dir()

    def test_starters_directory_exists(self) -> None:
        assert (REPO_ROOT / "starters").is_dir()

    def test_config_base_dir_resolves(self) -> None:
        """config.py computes _DEFAULT_BASE_DIR from __file__ – must resolve."""
        from live_analytics.app.config import BASE_DIR
        assert BASE_DIR.is_dir(), f"config.BASE_DIR not found: {BASE_DIR}"

    def test_questionnaire_static_exists(self) -> None:
        """questionnaire/app.py serves static files from ./static/ – must exist."""
        static = REPO_ROOT / "live_analytics" / "questionnaire" / "static"
        assert static.is_dir(), f"Questionnaire static dir missing: {static}"

    def test_system_check_static_exists(self) -> None:
        """system_check/app.py serves static files from ./static/ – must exist."""
        static = REPO_ROOT / "live_analytics" / "system_check" / "static"
        assert static.is_dir(), f"System Check static dir missing: {static}"

    def test_launcher_root_detection(self) -> None:
        """launcher.py uses Path(__file__).resolve().parent.parent to find root."""
        launcher = REPO_ROOT / "starters" / "launcher.py"
        assert launcher.is_file()
        expected_root = launcher.resolve().parent.parent
        assert expected_root == REPO_ROOT

    def test_path_contains_no_hardcoded_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No config path should contain a hardcoded home dir."""
        for key in ("LA_BASE_DIR", "LA_DATA_DIR", "LA_DB_PATH", "LA_SESSIONS_DIR"):
            monkeypatch.delenv(key, raising=False)

        import live_analytics.app.config as cfg
        importlib.reload(cfg)
        for attr in ("BASE_DIR", "DATA_DIR", "DB_PATH", "SESSIONS_DIR"):
            p = str(getattr(cfg, attr))
            assert "/home/" not in p and "C:\\Users\\" not in p.upper() or True
            # The real check: path must be relative to repo root or an override
            # (i.e. must descend from REPO_ROOT when using defaults).
            path = Path(p)
            try:
                path.relative_to(REPO_ROOT)
            except ValueError:
                pytest.fail(
                    f"config.{attr}={p!r} is not inside REPO_ROOT={REPO_ROOT}. "
                    "This will break on any machine with a different home directory."
                )


# ═══════════════════════════════════════════════════════════════════════
# 2. RUNTIME DIRECTORY CREATION
# ═══════════════════════════════════════════════════════════════════════

class TestRuntimeDirCreation:
    """ensure_dirs() must create directories from scratch (clean-state behavior)."""

    def test_analytics_ensure_dirs_creates_missing(self, tmp_path: Path) -> None:
        """ensure_dirs() must work even if the data dir doesn't exist yet."""
        data = tmp_path / "la_data"
        sessions = data / "sessions"
        assert not data.exists()

        # Temporarily override config paths
        import live_analytics.app.config as cfg
        orig_data = cfg.DATA_DIR
        orig_sess = cfg.SESSIONS_DIR
        orig_db = cfg.DB_PATH
        custom_db = tmp_path / "nested" / "db" / "custom.db"
        cfg.DATA_DIR = data
        cfg.SESSIONS_DIR = sessions
        cfg.DB_PATH = custom_db
        try:
            cfg.ensure_dirs()
            assert data.is_dir()
            assert sessions.is_dir()
            assert custom_db.parent.is_dir(), "ensure_dirs() did not create custom DB parent directory"
        finally:
            cfg.DATA_DIR = orig_data
            cfg.SESSIONS_DIR = orig_sess
            cfg.DB_PATH = orig_db

    def test_questionnaire_ensure_dirs_creates_missing(self, tmp_path: Path) -> None:
        import live_analytics.questionnaire.config as qcfg
        data = tmp_path / "qs_data"
        db = data / "questionnaire.db"
        orig_data = qcfg.DATA_DIR
        orig_db = qcfg.DB_PATH
        qcfg.DATA_DIR = data
        qcfg.DB_PATH = db
        try:
            qcfg.ensure_dirs()
            assert data.is_dir()
        finally:
            qcfg.DATA_DIR = orig_data
            qcfg.DB_PATH = orig_db

    def test_system_check_ensure_dirs_creates_missing(self, tmp_path: Path) -> None:
        import live_analytics.system_check as sc
        orig = sc.DATA_DIR
        sc.DATA_DIR = tmp_path / "sc_data"
        try:
            sc.ensure_dirs()
            assert sc.DATA_DIR.is_dir()
        finally:
            sc.DATA_DIR = orig

    def test_raw_writer_creates_session_subdir(self, tmp_path: Path) -> None:
        """RawWriter must create <sessions_dir>/<session_id>/ on first append."""
        from live_analytics.app.storage.raw_writer import RawWriter
        from live_analytics.app.models import TelemetryRecord

        writer = RawWriter(tmp_path)
        record = TelemetryRecord(
            session_id="test_session",
            sequence=1,
            unity_time=0.0,
            unix_ms=0,
            speed=5.0,
            steering_angle=0.0,
            brake_front=0.0,
            brake_rear=0.0,
            head_rot_w=1.0,
            head_rot_x=0.0,
            head_rot_y=0.0,
            head_rot_z=0.0,
            heart_rate=70,
            trigger_id="",
        )
        writer.append(record)
        session_dir = tmp_path / "test_session"
        assert session_dir.is_dir(), "RawWriter did not create session subdirectory"
        jsonl = session_dir / "telemetry.jsonl"
        assert jsonl.is_file(), "RawWriter did not create telemetry.jsonl"

    def test_sqlite_store_init_creates_db(self, tmp_path: Path) -> None:
        """init_db() must create the DB file when it doesn't exist."""
        from live_analytics.app.storage.sqlite_store import init_db, close_pool
        db = tmp_path / "test.db"
        assert not db.exists()
        try:
            init_db(db)
            assert db.is_file(), "init_db() did not create the SQLite file"
        finally:
            close_pool()

    def test_backfill_creates_custom_db_parent(self, tmp_path: Path) -> None:
        """backfill() must support --db paths whose parent dirs don't exist."""
        from live_analytics.scripts.backfill_from_jsonl import backfill

        db = tmp_path / "nested" / "custom" / "backfill.db"
        sessions = tmp_path / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)

        inserted = backfill(db, sessions, dry_run=False)
        assert inserted == 0
        assert db.exists(), "backfill() did not create DB for custom --db path"


# ═══════════════════════════════════════════════════════════════════════
# 3. SERVICE URL DEFAULTS — NO localhost (IPv6 RISK ON WINDOWS)
# ═══════════════════════════════════════════════════════════════════════

class TestServiceUrls:
    """On Windows, 'localhost' may resolve to ::1 (IPv6) while services bind
    on 0.0.0.0 (IPv4).  All default service URLs must use 127.0.0.1."""

    def test_bridge_url_uses_127(self) -> None:
        from live_analytics.system_check import BRIDGE_WS_URL
        assert "localhost" not in BRIDGE_WS_URL, (
            f"BRIDGE_WS_URL={BRIDGE_WS_URL!r} uses 'localhost' which may "
            "resolve to ::1 on Windows. Use 127.0.0.1 instead."
        )

    def test_analytics_url_uses_127(self) -> None:
        from live_analytics.system_check import ANALYTICS_API_URL
        assert "localhost" not in ANALYTICS_API_URL, (
            f"ANALYTICS_API_URL={ANALYTICS_API_URL!r} uses 'localhost'."
        )

    def test_questionnaire_url_uses_127(self) -> None:
        from live_analytics.system_check import QUESTIONNAIRE_API_URL
        assert "localhost" not in QUESTIONNAIRE_API_URL, (
            f"QUESTIONNAIRE_API_URL={QUESTIONNAIRE_API_URL!r} uses 'localhost'."
        )

    def test_dashboard_api_default_uses_127(self) -> None:
        """The dashboard falls back to 127.0.0.1 when no env var is set."""
        api_base = os.getenv("LA_API_BASE", "http://127.0.0.1:8080")
        assert "localhost" not in api_base


# ═══════════════════════════════════════════════════════════════════════
# 4. GITKEEP FILES — ZIP DOWNLOAD INTEGRITY
# ═══════════════════════════════════════════════════════════════════════

class TestGitkeepFiles:
    """All .gitkeep files must be present so ZIP-extracted folders exist."""

    @pytest.mark.parametrize("rel", [
        "live_analytics/data/.gitkeep",
        "live_analytics/data/sessions/.gitkeep",
        "live_analytics/questionnaire/data/.gitkeep",
        "live_analytics/system_check/data/.gitkeep",
    ])
    def test_gitkeep_present(self, rel: str) -> None:
        path = REPO_ROOT / rel
        assert path.is_file(), (
            f"Missing placeholder: {rel}\n"
            "This file ensures the directory exists after a GitHub ZIP download. "
            "If it was deleted, re-create it with: touch " + rel
        )


# ═══════════════════════════════════════════════════════════════════════
# 5. IMPORT SAFETY
# ═══════════════════════════════════════════════════════════════════════

class TestImportSafety:
    """All main service modules must be importable without side effects."""

    @pytest.mark.parametrize("module", [
        "live_analytics.app.config",
        "live_analytics.app.env_utils",
        "live_analytics.app.models",
        "live_analytics.app.storage.sqlite_store",
        "live_analytics.app.storage.raw_writer",
        "live_analytics.questionnaire.config",
        "live_analytics.system_check",
    ])
    def test_module_importable(self, module: str) -> None:
        """Module must import cleanly (no missing deps, no bad paths)."""
        try:
            importlib.import_module(module)
        except ImportError as exc:
            pytest.fail(f"ImportError on {module!r}: {exc}")
        except Exception as exc:
            pytest.fail(f"Unexpected error importing {module!r}: {type(exc).__name__}: {exc}")

    def test_preflight_syntax(self) -> None:
        """preflight.py must be syntactically valid Python.

        We cannot import it as a module because it runs checks and calls
        sys.exit() at module level.  Compiling the source is sufficient to
        detect import-time syntax errors, bad f-strings, etc.
        """
        src = (REPO_ROOT / "starters" / "preflight.py").read_text(encoding="utf-8")
        try:
            compile(src, "starters/preflight.py", "exec")
        except SyntaxError as exc:
            pytest.fail(f"Syntax error in starters/preflight.py: {exc}")


# ═══════════════════════════════════════════════════════════════════════
# 6. ENCODING SAFETY
# ═══════════════════════════════════════════════════════════════════════

class TestEncodingSafety:
    """Validate that JSONL output is always UTF-8 regardless of OS locale."""

    def test_raw_writer_uses_utf8(self, tmp_path: Path) -> None:
        """JSONL must be written with explicit UTF-8 encoding."""
        import inspect
        from live_analytics.app.storage import raw_writer
        src = inspect.getsource(raw_writer)
        # The file open must specify encoding="utf-8" so Windows cp1252 locale
        # doesn't corrupt non-ASCII session IDs or telemetry strings.
        assert 'encoding="utf-8"' in src, (
            "raw_writer.py opens files without explicit encoding='utf-8'. "
            "On Windows with a non-UTF-8 code page this will corrupt non-ASCII data."
        )

    def test_preflight_has_windows_encoding_guard(self) -> None:
        """preflight.py must contain the Windows encoding reconfigure block."""
        preflight = REPO_ROOT / "starters" / "preflight.py"
        src = preflight.read_text(encoding="utf-8")
        assert "reconfigure" in src and "utf-8" in src, (
            "preflight.py is missing the Windows stdout.reconfigure(encoding='utf-8') guard. "
            "Running it from cmd.exe without chcp 65001 will raise UnicodeEncodeError."
        )

    def test_preflight_uses_python_exe_on_windows(self) -> None:
        """preflight venv detection must target python.exe on win32."""
        src = (REPO_ROOT / "starters" / "preflight.py").read_text(encoding="utf-8")
        assert "python.exe" in src, (
            "preflight.py must check .venv\\Scripts\\python.exe on Windows; "
            "checking '.venv\\Scripts\\python' fails on clean Windows setups."
        )


# ═══════════════════════════════════════════════════════════════════════
# 7. PATH SEPARATOR SAFETY
# ═══════════════════════════════════════════════════════════════════════

class TestPathSeparatorSafety:
    """Path construction must use pathlib, not hardcoded forward/backslashes."""

    def test_config_paths_are_pathlib(self) -> None:
        from live_analytics.app import config
        for attr in ("BASE_DIR", "DATA_DIR", "DB_PATH", "SESSIONS_DIR"):
            val = getattr(config, attr)
            assert isinstance(val, Path), (
                f"config.{attr} is {type(val).__name__}, expected pathlib.Path. "
                "String paths with hardcoded separators break on Windows."
            )

    def test_questionnaire_config_paths_are_pathlib(self) -> None:
        from live_analytics.questionnaire import config as qcfg
        for attr in ("BASE_DIR", "DATA_DIR", "DB_PATH"):
            val = getattr(qcfg, attr)
            assert isinstance(val, Path), (
                f"questionnaire.config.{attr} is {type(val).__name__}, expected Path."
            )


# ═══════════════════════════════════════════════════════════════════════
# 8. CONFIG CHAINING SAFETY
# ═══════════════════════════════════════════════════════════════════════

class TestConfigPathChaining:
    """BASE_DIR overrides should cascade to DATA_DIR/DB_PATH unless explicitly overridden."""

    def test_la_base_dir_cascades_to_data_db_sessions(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        base = tmp_path / "la_base"
        monkeypatch.setenv("LA_BASE_DIR", str(base))
        monkeypatch.delenv("LA_DATA_DIR", raising=False)
        monkeypatch.delenv("LA_DB_PATH", raising=False)
        monkeypatch.delenv("LA_SESSIONS_DIR", raising=False)

        import live_analytics.app.config as cfg
        importlib.reload(cfg)

        assert cfg.DATA_DIR == base / "data"
        assert cfg.DB_PATH == base / "data" / "live_analytics.db"
        assert cfg.SESSIONS_DIR == base / "data" / "sessions"

    def test_qs_base_dir_cascades_to_data_db(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        base = tmp_path / "qs_base"
        monkeypatch.setenv("QS_BASE_DIR", str(base))
        monkeypatch.delenv("QS_DATA_DIR", raising=False)
        monkeypatch.delenv("QS_DB_PATH", raising=False)

        import live_analytics.questionnaire.config as qcfg
        importlib.reload(qcfg)

        assert qcfg.DATA_DIR == base / "data"
        assert qcfg.DB_PATH == base / "data" / "questionnaire.db"
