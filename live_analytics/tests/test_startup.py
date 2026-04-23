"""
test_startup.py – preflight / bootstrap validation tests
=========================================================

Verifies that every required package is importable, that all internal
packages import cleanly, that init_db is idempotent, and that the
system_check config module has no duplicate names.
"""
from __future__ import annotations

import importlib
import subprocess
import sys
import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# ─────────────────────────────────────────────────────────────────────
# 1. Required external packages
# ─────────────────────────────────────────────────────────────────────

REQUIRED_PACKAGES = [
    "fastapi",
    "uvicorn",
    "websockets",
    "bleak",
    "aiofiles",
    "streamlit",
    "pandas",
    "pyarrow",
    "sqlalchemy",
    "httpx",
    "pydantic",
    "starlette",
]


@pytest.mark.parametrize("pkg", REQUIRED_PACKAGES)
def test_required_package_importable(pkg: str) -> None:
    """Each required package must be importable."""
    mod = importlib.import_module(pkg)
    assert mod is not None


# ─────────────────────────────────────────────────────────────────────
# 2. All required packages are declared in requirements.txt
# ─────────────────────────────────────────────────────────────────────

def test_requirements_txt_declares_streamlit() -> None:
    """streamlit must be declared in requirements.txt (not just pyproject.toml)."""
    req_txt = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "streamlit" in req_txt, (
        "streamlit is missing from requirements.txt — add 'streamlit>=1.33' "
        "so `pip install -r requirements.txt` produces a working environment"
    )


@pytest.mark.parametrize("pkg", ["aiofiles", "sqlalchemy", "fastapi", "uvicorn", "bleak", "websockets"])
def test_requirements_txt_declares_package(pkg: str) -> None:
    """Key packages must be declared in requirements.txt."""
    req_txt = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert pkg in req_txt, f"'{pkg}' missing from requirements.txt"


# ─────────────────────────────────────────────────────────────────────
# 3. Internal package imports
# ─────────────────────────────────────────────────────────────────────

INTERNAL_MODULES = [
    "live_analytics.app.config",
    "live_analytics.app.main",
    "live_analytics.questionnaire.app",
    "live_analytics.system_check.app",
    "bridge.bike_bridge",
]


@pytest.mark.parametrize("mod", INTERNAL_MODULES)
def test_internal_module_importable(mod: str) -> None:
    """Each internal module must import without error."""
    m = importlib.import_module(mod)
    assert m is not None


# ─────────────────────────────────────────────────────────────────────
# 4. init_db.py idempotency
# ─────────────────────────────────────────────────────────────────────

def test_init_db_idempotent(tmp_path: Path) -> None:
    """Running init_db.py twice must succeed without errors."""
    import os
    env = {**os.environ, "ANALYTICS_DB": str(tmp_path / "test.db")}
    script = REPO_ROOT / "live_analytics" / "scripts" / "init_db.py"
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, str(script)],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"init_db.py failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ─────────────────────────────────────────────────────────────────────
# 5. preflight.py exits 0 in current env
# ─────────────────────────────────────────────────────────────────────

def test_preflight_exits_zero() -> None:
    """preflight.py must exit 0 when the environment is correctly set up."""
    script = REPO_ROOT / "starters" / "preflight.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"preflight.py exited {result.returncode}:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ─────────────────────────────────────────────────────────────────────
# 6. system_check/__init__.py has no duplicate top-level names
# ─────────────────────────────────────────────────────────────────────

def test_system_check_no_duplicate_names() -> None:
    """system_check/__init__.py must not define any name more than once."""
    src = (
        REPO_ROOT / "live_analytics" / "system_check" / "__init__.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    assigned: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign,)):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned.append(target.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                assigned.append(node.target.id)
        elif isinstance(node, ast.FunctionDef):
            assigned.append(node.name)
    duplicates = {name for name in assigned if assigned.count(name) > 1}
    assert not duplicates, f"Duplicate definitions in system_check/__init__.py: {duplicates}"


# ─────────────────────────────────────────────────────────────────────
# 7. Log rotation helper
# ─────────────────────────────────────────────────────────────────────

def test_rotate_log_creates_backup(tmp_path: Path) -> None:
    """_rotate_log must rename the current log to .log.1 on next run."""
    sys.path.insert(0, str(REPO_ROOT / "starters"))
    import importlib as _il
    launcher = _il.import_module("launcher")
    log = tmp_path / "analytics_api.log"
    log.write_text("old run output\n")

    launcher._rotate_log(log)

    assert not log.exists(), "current log should have been rotated away"
    assert (tmp_path / "analytics_api.log.1").exists(), ".log.1 backup must exist"


def test_rotate_log_max_backups(tmp_path: Path) -> None:
    """_rotate_log must not keep more than _LOG_BACKUPS backups."""
    sys.path.insert(0, str(REPO_ROOT / "starters"))
    import importlib as _il
    launcher = _il.import_module("launcher")
    log = tmp_path / "svc.log"

    # Simulate _LOG_BACKUPS + 1 prior runs
    for run in range(launcher._LOG_BACKUPS + 1):
        log.write_text(f"run {run}\n")
        launcher._rotate_log(log)

    backups = sorted(tmp_path.glob("svc.log.*"))
    assert len(backups) <= launcher._LOG_BACKUPS, (
        f"Too many backups: {[b.name for b in backups]}"
    )


def test_rotate_log_noop_when_absent(tmp_path: Path) -> None:
    """_rotate_log must not raise when the log file does not exist yet."""
    sys.path.insert(0, str(REPO_ROOT / "starters"))
    import importlib as _il
    launcher = _il.import_module("launcher")
    launcher._rotate_log(tmp_path / "nonexistent.log")  # must not raise
