#!/usr/bin/env python3
"""
Build ``system-check`` as a standalone executable using PyInstaller.

Usage:
    python build_exe.py          # builds dist/system-check (or .exe on Windows)
    python build_exe.py --clean  # remove previous build artifacts first

The resulting binary includes all check logic and requires NO Python
installation on the target machine.  Only ``adb`` must be in PATH for
the Quest headset check to work.

Prerequisites:
    pip install pyinstaller
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENTRY_POINT = ROOT / "live_analytics" / "system_check" / "run_checks.py"
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"

EXE_NAME = "system-check"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build system-check executable")
    parser.add_argument("--clean", action="store_true", help="Remove previous build artifacts first")
    args = parser.parse_args()

    if args.clean:
        for d in [DIST_DIR, BUILD_DIR]:
            if d.exists():
                print(f"  Removing {d} ...")
                shutil.rmtree(d)
        spec = ROOT / f"{EXE_NAME}.spec"
        if spec.exists():
            spec.unlink()

    # Verify PyInstaller is available
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("ERROR: PyInstaller not installed.  Run:  pip install pyinstaller")
        sys.exit(1)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", EXE_NAME,
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR),
        # Include the live_analytics.system_check package
        "--hidden-import", "live_analytics.system_check",
        "--hidden-import", "live_analytics.system_check.checks",
        "--hidden-import", "live_analytics.system_check.run_checks",
        # websockets is imported lazily inside _ws_probe(); include it
        "--hidden-import", "websockets",
        # No GUI – pure console app
        "--console",
        # Strip debug symbols for smaller binary
        "--strip",
        str(ENTRY_POINT),
    ]

    print()
    print(f"  Building {EXE_NAME} ...")
    print(f"  Entry point: {ENTRY_POINT}")
    print(f"  Platform:    {platform.system()} {platform.machine()}")
    print()

    result = subprocess.run(cmd, cwd=str(ROOT))

    if result.returncode != 0:
        print(f"\nERROR: Build failed (exit {result.returncode})")
        sys.exit(result.returncode)

    # Show result
    if platform.system() == "Windows":
        exe_path = DIST_DIR / f"{EXE_NAME}.exe"
    else:
        exe_path = DIST_DIR / EXE_NAME

    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print()
        print(f"  Build OK!")
        print(f"  Output: {exe_path}  ({size_mb:.1f} MB)")
        print()
        print(f"  Run with:")
        print(f"    {exe_path}")
        print(f"    {exe_path} --help")
        print(f"    {exe_path} --check quest")
        print(f"    {exe_path} --json")
        print()
    else:
        print(f"\nERROR: Exe not found at {exe_path}")
        sys.exit(1)


if __name__ == "__main__":
    main()
