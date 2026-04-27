#!/usr/bin/env python3
"""
preflight.py – environment validation for Bike VR / Wahoo BLE Sniffer
======================================================================

Checks that every requirement is met before any service is started.
Run automatically by INSTALL.command / INSTALL.bat after pip install,
and can be invoked manually at any time:

    python starters/preflight.py

Exits 0 on success, 1 on any failure with a clear remediation message.
"""
from __future__ import annotations

import importlib
import sys
import os
from pathlib import Path

# ── Windows console compatibility ─────────────────────────────────────
# Without this, printing UTF-8 characters (✔ ✘ ⚠) from cmd.exe on Windows
# raises UnicodeEncodeError and preflight crashes before any check runs.
# This mirrors the same guard in launcher.py.
if sys.platform == "win32":
    os.system("")  # enables VT100 ANSI escape processing on Windows 10+
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass  # older Python or non-reconfigurable stream — best effort

# ── Paths ─────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent

# ANSI
_R = "\033[0m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_BOLD = "\033[1m"

OK = f"  {_GREEN}✔{_R}"
FAIL = f"  {_RED}✘{_R}"
WARN = f"  {_YELLOW}⚠{_R}"

errors: list[str] = []
warnings: list[str] = []


def _ok(msg: str) -> None:
    print(f"{OK}  {msg}")


def _fail(msg: str, hint: str = "") -> None:
    print(f"{FAIL}  {_BOLD}{msg}{_R}" + (f"\n       → {hint}" if hint else ""))
    errors.append(msg)


def _warn(msg: str, hint: str = "") -> None:
    print(f"{WARN}  {msg}" + (f"\n       → {hint}" if hint else ""))
    warnings.append(msg)


# ─────────────────────────────────────────────────────────────────────
# 1. Python version
# ─────────────────────────────────────────────────────────────────────
print(f"\n{_BOLD}[1/5] Python version{_R}")
major, minor = sys.version_info[:2]
if (major, minor) >= (3, 11):
    _ok(f"Python {major}.{minor} ({sys.executable})")
else:
    _fail(
        f"Python {major}.{minor} is too old — 3.11+ required",
        "Install Python 3.11+ and re-run INSTALL.command / INSTALL.bat",
    )

# ─────────────────────────────────────────────────────────────────────
# 2. Virtual environment
# ─────────────────────────────────────────────────────────────────────
print(f"\n{_BOLD}[2/5] Virtual environment{_R}")
venv_bin = "Scripts" if sys.platform == "win32" else "bin"
venv_exe = "python.exe" if sys.platform == "win32" else "python"
venv_python = REPO_ROOT / ".venv" / venv_bin / venv_exe

if Path(sys.executable).resolve() == venv_python.resolve():
    _ok(f"Running inside project venv ({sys.prefix})")
elif venv_python.exists():
    activate_hint = ".venv\\Scripts\\activate" if sys.platform == "win32" else "source .venv/bin/activate"
    _warn(
        "Not running inside the project venv",
        f"Run: {activate_hint}  (or use {venv_python})",
    )
else:
    _fail(
        "Project venv not found at .venv/",
        "Run INSTALL.command (macOS/Linux) or INSTALL.bat (Windows) first",
    )

# ─────────────────────────────────────────────────────────────────────
# 3. Required packages
# ─────────────────────────────────────────────────────────────────────
print(f"\n{_BOLD}[3/5] Required packages{_R}")

REQUIRED: list[tuple[str, str]] = [
    # (import_name, pip_name)
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn[standard]"),
    ("websockets", "websockets"),
    ("bleak", "bleak"),
    ("aiofiles", "aiofiles"),
    ("streamlit", "streamlit"),
    ("pandas", "pandas"),
    ("pyarrow", "pyarrow"),
    ("sqlalchemy", "sqlalchemy"),
    ("httpx", "httpx"),
    ("pydantic", "pydantic"),
    ("starlette", "starlette"),
]

for import_name, pip_name in REQUIRED:
    try:
        importlib.import_module(import_name)
        _ok(import_name)
    except ImportError:
        _fail(
            f"'{import_name}' not installed",
            f"pip install \"{pip_name}\"  (or re-run INSTALL script)",
        )

# ─────────────────────────────────────────────────────────────────────
# 4. Repo structure
# ─────────────────────────────────────────────────────────────────────
print(f"\n{_BOLD}[4/5] Repo structure{_R}")
EXPECTED_DIRS = [
    "live_analytics/app",
    "live_analytics/questionnaire",
    "live_analytics/system_check",
    "live_analytics/dashboard",
    "bridge",
    "starters",
]
for rel in EXPECTED_DIRS:
    p = REPO_ROOT / rel
    if p.is_dir():
        _ok(rel)
    else:
        _fail(f"Directory missing: {rel}", "Check git clone is complete")

# ─────────────────────────────────────────────────────────────────────
# 5. Internal imports
# ─────────────────────────────────────────────────────────────────────
print(f"\n{_BOLD}[5/5] Internal package imports{_R}")

# Ensure repo root is on sys.path for editable installs
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

INTERNAL: list[str] = [
    "live_analytics.app.config",
    "live_analytics.app.main",
    "live_analytics.questionnaire.app",
    "live_analytics.system_check.app",
    "bridge.bike_bridge",
]
for mod in INTERNAL:
    try:
        importlib.import_module(mod)
        _ok(mod)
    except Exception as exc:
        _fail(f"{mod}: {type(exc).__name__}: {exc}")

# ─────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────
print()
if errors:
    print(f"{_RED}{_BOLD}✘  Preflight FAILED — {len(errors)} error(s):{_R}")
    for e in errors:
        print(f"   • {e}")
    if warnings:
        print(f"\n{_YELLOW}⚠  {len(warnings)} warning(s):{_R}")
        for w in warnings:
            print(f"   • {w}")
    print(
        "\nFix the errors above, then re-run:\n"
        "  python starters/preflight.py\n"
    )
    sys.exit(1)
else:
    if warnings:
        print(f"{_YELLOW}{_BOLD}⚠  Preflight passed with {len(warnings)} warning(s){_R}")
        for w in warnings:
            print(f"   • {w}")
    else:
        print(f"{_GREEN}{_BOLD}✔  All preflight checks passed.{_R}")
    print()
    sys.exit(0)
