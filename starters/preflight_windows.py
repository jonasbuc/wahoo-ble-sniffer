#!/usr/bin/env python3
"""
preflight_windows.py – Windows-deployment readiness check
==========================================================

Supplements the generic preflight.py with Windows-specific and
clean-install-specific checks that are most likely to cause the
"first request → service goes down" failure pattern:

  1. Python 3.11+ on PATH
  2. Virtual environment present and not corrupted
  3. Repo root path: spaces warning, non-ASCII warning
  4. Write permission for all runtime directories
     (DATA_DIR, SESSIONS_DIR, logs/, questionnaire data)
  5. SQLite writable (temp probe write in DATA_DIR)
  6. Port availability: 8080, 8090, 8095, 8501, 8765, 8766
  7. Required packages importable from this interpreter
  8. Windows Defender / antivirus scanning hint (informational)
  9. Console encoding for UTF-8 output (important on cmd.exe)

Run automatically by INSTALL.bat and can be invoked manually:
    .venv\\Scripts\\python.exe starters\\preflight_windows.py

Exits 0 when ready, 1 when one or more critical checks fail.
Warnings (non-fatal) are printed but do not cause a non-zero exit.
"""
from __future__ import annotations

import importlib
import os
import socket
import sqlite3
import sys
import tempfile
from pathlib import Path

# ── Windows console encoding ─────────────────────────────────────────
# Must happen before any print() call to avoid UnicodeEncodeError on
# legacy cmd.exe / PowerShell consoles that default to cp1252 / cp850.
if sys.platform == "win32":
    os.system("")  # enable VT100 ANSI escape processing (Windows 10+)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass  # older Python / non-reconfigurable stream — best effort

# ── Paths ─────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent

# Runtime directories that must be writable before any service starts.
# Keep in sync with live_analytics/app/config.py:ensure_dirs()
DATA_DIR = REPO_ROOT / "live_analytics" / "data"
SESSIONS_DIR = DATA_DIR / "sessions"
LOGS_DIR = REPO_ROOT / "logs"
QUESTIONNAIRE_DATA_DIR = REPO_ROOT / "live_analytics" / "questionnaire" / "data"
SYSTEM_CHECK_DATA_DIR  = REPO_ROOT / "live_analytics" / "system_check"  / "data"

_RUNTIME_DIRS = [
    DATA_DIR,
    SESSIONS_DIR,
    LOGS_DIR,
    QUESTIONNAIRE_DATA_DIR,
    SYSTEM_CHECK_DATA_DIR,
]

# Ports that must be free for all services to bind.
_PORTS: list[tuple[int, str]] = [
    (8080, "Analytics API (HTTP)"),
    (8766, "Analytics API (WS ingest)"),
    (8090, "Questionnaire API"),
    (8095, "System Check GUI"),
    (8501, "Streamlit Dashboard"),
    (8765, "Wahoo BLE Bridge (optional)"),
]

# Packages that must be importable from THIS interpreter.
_PACKAGES: list[tuple[str, str]] = [
    ("fastapi",     "fastapi"),
    ("uvicorn",     "uvicorn[standard]"),
    ("websockets",  "websockets"),
    ("streamlit",   "streamlit"),
    ("pydantic",    "pydantic"),
    ("pandas",      "pandas"),
    ("requests",    "requests"),
    ("starlette",   "starlette"),
]

# ── ANSI helpers ─────────────────────────────────────────────────────
_R      = "\033[0m"
_BOLD   = "\033[1m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_RED    = "\033[31m"
_DIM    = "\033[2m"

_OK   = f"  {_GREEN}+{_R}"
_FAIL = f"  {_RED}x{_R}"
_WARN = f"  {_YELLOW}!{_R}"
_INFO = f"  {_DIM}>{_R}"

errors:   list[str] = []
warnings: list[str] = []


def ok(msg: str) -> None:
    print(f"{_OK}  {msg}", flush=True)


def fail(msg: str, hint: str = "") -> None:
    print(f"{_FAIL}  {_BOLD}{msg}{_R}" + (f"\n       Hint: {hint}" if hint else ""), flush=True)
    errors.append(msg)


def warn(msg: str, hint: str = "") -> None:
    print(f"{_WARN}  {msg}" + (f"\n       Hint: {hint}" if hint else ""), flush=True)
    warnings.append(msg)


def info(msg: str) -> None:
    print(f"{_INFO}  {_DIM}{msg}{_R}", flush=True)


def _sep(title: str) -> None:
    print(f"\n{_BOLD}── {title} ──{_R}", flush=True)


# ─────────────────────────────────────────────────────────────────────
# 1. Python version
# ─────────────────────────────────────────────────────────────────────
_sep("1/9  Python version")
major, minor = sys.version_info[:2]
if (major, minor) >= (3, 11):
    ok(f"Python {major}.{minor}  ({sys.executable})")
else:
    fail(
        f"Python {major}.{minor} is too old — 3.11+ required",
        "Install Python 3.11+ from https://www.python.org/downloads/ "
        "and re-run INSTALL.bat",
    )

# ─────────────────────────────────────────────────────────────────────
# 2. Virtual environment
# ─────────────────────────────────────────────────────────────────────
_sep("2/9  Virtual environment")
_venv_bin = "Scripts" if sys.platform == "win32" else "bin"
_venv_exe = "python.exe" if sys.platform == "win32" else "python"
_venv_python = REPO_ROOT / ".venv" / _venv_bin / _venv_exe

if not _venv_python.exists():
    fail(
        f".venv not found at {_venv_python}",
        "Run INSTALL.bat first to create the virtual environment.",
    )
else:
    ok(f".venv found at {_venv_python.parent.parent}")
    if Path(sys.executable).resolve() != _venv_python.resolve():
        warn(
            "This script is NOT running from the project venv",
            f"For best results run: {_venv_python} starters\\preflight_windows.py",
        )
    else:
        ok("Running inside project venv")

    # Check venv is not corrupted (pip still works)
    import subprocess as _sp
    _pip_check = _sp.run(
        [str(_venv_python), "-m", "pip", "--version"],
        capture_output=True, text=True,
    )
    if _pip_check.returncode != 0:
        fail(
            "venv pip is broken",
            "Delete the .venv folder and re-run INSTALL.bat to recreate it.",
        )
    else:
        ok("venv pip OK")

# ─────────────────────────────────────────────────────────────────────
# 3. Repo root path: spaces / non-ASCII
# ─────────────────────────────────────────────────────────────────────
_sep("3/9  Repo path")
_root_str = str(REPO_ROOT)
ok(f"Repo root: {_root_str}")

if " " in _root_str:
    warn(
        f"Repo root path contains spaces: {_root_str!r}",
        "Most commands handle quoted paths correctly, but some "
        "third-party tools do not.  Consider moving to a path "
        "without spaces (e.g. C:\\VR\\wahoo-ble-sniffer) if you "
        "encounter unexpected errors.",
    )
else:
    ok("No spaces in repo root path")

try:
    _root_str.encode("ascii")
    ok("Repo root path is ASCII-safe")
except UnicodeEncodeError:
    warn(
        f"Repo root path contains non-ASCII characters: {_root_str!r}",
        "This can cause issues with some Python packages and SQLite "
        "on Windows.  Consider moving the project to an ASCII-only path.",
    )

# ─────────────────────────────────────────────────────────────────────
# 4. Runtime directory write permissions
# ─────────────────────────────────────────────────────────────────────
_sep("4/9  Runtime directory write permission")
for _dir in _RUNTIME_DIRS:
    try:
        _dir.mkdir(parents=True, exist_ok=True)
        # Probe write permission by creating and immediately removing a temp file
        _probe = _dir / ".write_probe"
        _probe.write_text("ok", encoding="utf-8")
        _probe.unlink()
        ok(f"Writable: {_dir.relative_to(REPO_ROOT)}")
    except PermissionError as exc:
        fail(
            f"No write permission: {_dir}",
            f"Grant write permission to this folder or run as a user who owns it. ({exc})",
        )
    except Exception as exc:
        fail(
            f"Cannot create/write to {_dir}: {type(exc).__name__}: {exc}",
            "Check that the disk is not full and the path is valid on Windows.",
        )

# ─────────────────────────────────────────────────────────────────────
# 5. SQLite writable
# ─────────────────────────────────────────────────────────────────────
_sep("5/9  SQLite write probe")
_sqlite_probe = DATA_DIR / "_preflight_probe.db"
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(_sqlite_probe))
    _conn.execute("CREATE TABLE IF NOT EXISTS _probe (x INTEGER)")
    _conn.execute("INSERT INTO _probe VALUES (1)")
    _conn.commit()
    _conn.close()
    _sqlite_probe.unlink(missing_ok=True)
    ok(f"SQLite writable in {DATA_DIR.relative_to(REPO_ROOT)}")
except Exception as exc:
    fail(
        f"SQLite probe failed in {DATA_DIR}: {type(exc).__name__}: {exc}",
        "Ensure the process has read+write access to the data directory.  "
        "On Windows, antivirus software sometimes locks SQLite files — try "
        "adding the project folder to the antivirus exclusion list.",
    )
    # Clean up probe file if it was created before the error
    try:
        _sqlite_probe.unlink(missing_ok=True)
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────
# 6. Port availability
# ─────────────────────────────────────────────────────────────────────
_sep("6/9  Port availability")

def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
        _s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            _s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False

for _port, _label in _PORTS:
    if _port_free(_port):
        ok(f"Port {_port} free  ({_label})")
    else:
        if _port == 8765:
            # BLE bridge port is optional — only a warning
            warn(
                f"Port {_port} in use  ({_label} — optional; only needed with --bridge)",
                "Kill the process using this port or ignore if not using BLE bridge.",
            )
        else:
            fail(
                f"Port {_port} already in use  ({_label})",
                f"Find and stop the process using port {_port}:\n"
                f"       Windows: netstat -ano | findstr :{_port}\n"
                f"       Then:    taskkill /PID <pid> /F",
            )

# ─────────────────────────────────────────────────────────────────────
# 7. Required packages importable
# ─────────────────────────────────────────────────────────────────────
_sep("7/9  Package imports")
# Ensure repo root is on path for local packages
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

for _import_name, _pip_name in _PACKAGES:
    try:
        importlib.import_module(_import_name)
        ok(_import_name)
    except ImportError:
        fail(
            f"'{_import_name}' not importable",
            f"pip install \"{_pip_name}\"  — or re-run INSTALL.bat",
        )
    except Exception as exc:
        fail(
            f"'{_import_name}' import error: {type(exc).__name__}: {exc}",
            "Re-run INSTALL.bat to reinstall dependencies.",
        )

# ─────────────────────────────────────────────────────────────────────
# 8. Windows Defender / antivirus hint
# ─────────────────────────────────────────────────────────────────────
_sep("8/9  Antivirus / Windows Defender hint")
if sys.platform == "win32":
    info(
        "If any check above failed with PermissionError or SQLite lock errors, "
        "Windows Defender or your antivirus may be scanning Python / SQLite files.  "
        "Add the project folder to the antivirus exclusion list for best performance."
    )
    info(
        "Path to add as exclusion: "
        + str(REPO_ROOT)
    )
    ok("Hint printed (check manually if you see unexplained failures)")
else:
    ok("Not running on Windows — antivirus check skipped")

# ─────────────────────────────────────────────────────────────────────
# 9. Console encoding
# ─────────────────────────────────────────────────────────────────────
_sep("9/9  Console encoding")
_enc = getattr(sys.stdout, "encoding", None) or ""
if _enc.lower().replace("-", "") == "utf8":
    ok(f"stdout encoding is UTF-8  ({_enc})")
elif sys.platform != "win32":
    ok(f"stdout encoding: {_enc}  (non-Windows, should be fine)")
else:
    warn(
        f"stdout encoding is {_enc!r}, not UTF-8",
        "Log output may show garbled characters for special symbols.  "
        "Run 'chcp 65001' in cmd.exe, or use Windows Terminal for full UTF-8 support.",
    )

# ─────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────
print(flush=True)
print("─" * 60, flush=True)
if errors:
    print(f"{_RED}{_BOLD}PREFLIGHT FAILED  —  {len(errors)} error(s):{_R}", flush=True)
    for _e in errors:
        print(f"   x  {_e}", flush=True)
    if warnings:
        print(f"\n{_YELLOW}{len(warnings)} warning(s):{_R}", flush=True)
        for _w in warnings:
            print(f"   !  {_w}", flush=True)
    print(
        "\nFix the errors above, then re-run:\n"
        "    .venv\\Scripts\\python.exe starters\\preflight_windows.py\n",
        flush=True,
    )
    sys.exit(1)
else:
    if warnings:
        print(
            f"{_YELLOW}{_BOLD}PREFLIGHT PASSED  with {len(warnings)} warning(s):{_R}",
            flush=True,
        )
        for _w in warnings:
            print(f"   !  {_w}", flush=True)
        print()
    else:
        print(f"{_GREEN}{_BOLD}PREFLIGHT PASSED  —  all 9 checks OK{_R}", flush=True)
    print()
    sys.exit(0)
