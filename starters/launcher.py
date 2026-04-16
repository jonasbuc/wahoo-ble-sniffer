#!/usr/bin/env python3
"""
Bike VR - Master Launcher
=================================================================

Starts **all** services in the correct order, then opens a live
status dashboard in the terminal that turns green as each service
comes online.

Services started:
  1. Analytics API        (FastAPI, port 8080 + WS ingest 8765)
  2. Questionnaire API    (FastAPI, port 8090)
  3. System Check GUI     (FastAPI, port 8095)
  4. Streamlit Dashboard  (port 8501)
  5. Wahoo BLE Bridge     (WebSocket, port 8765 - optional with --bridge)

Usage:
    python starters/launcher.py                # start all (no BLE bridge)
    python starters/launcher.py --bridge       # also start the Wahoo bridge
    python starters/launcher.py --bridge --mock  # use mock bridge instead
    python starters/launcher.py --no-dashboard # skip Streamlit
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

# ── Force UTF-8 on Windows so print() never crashes ──────────────────
if sys.platform == "win32":
    os.system("")  # enables VT100 ANSI on Windows 10+
    if sys.stdout.encoding and sys.stdout.encoding.lower().replace("-", "") != "utf8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

# Detect if the terminal can handle unicode box-drawing / emoji
def _supports_unicode() -> bool:
    """Return True if stdout likely supports unicode glyphs."""
    if sys.platform != "win32":
        return True
    # Windows Terminal and modern consoles set WT_SESSION or use utf-8
    enc = (sys.stdout.encoding or "").lower().replace("-", "")
    if enc == "utf8":
        return True
    if os.environ.get("WT_SESSION"):  # Windows Terminal
        return True
    return False

_UNICODE = _supports_unicode()

# ── Resolve paths ─────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = ROOT / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / "python"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

# ── ANSI helpers ──────────────────────────────────────────────────────
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_CYAN = "\033[36m"
_CLEAR_LINE = "\033[2K"
_CURSOR_UP = "\033[A"


# ── Service definitions ──────────────────────────────────────────────

class Service:
    """Describes a service that should be launched."""

    def __init__(
        self,
        name: str,
        cmd: list[str],
        port: int,
        health_url: str | None = None,
        health_tcp: bool = True,
        cwd: str | None = None,
    ):
        self.name = name
        self.cmd = cmd
        self.port = port
        self.health_url = health_url
        self.health_tcp = health_tcp
        self.cwd = cwd or str(ROOT)
        self.process: subprocess.Popen | None = None
        self.status: str = "starting"  # starting | ok | error | skipped

    def start(self) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        try:
            self.process = subprocess.Popen(
                self.cmd,
                cwd=self.cwd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.status = "starting"
        except Exception as e:
            self.status = "error"

    def check_health(self) -> bool:
        """Return True if the service is responding."""
        if self.process and self.process.poll() is not None:
            self.status = "error"
            return False

        if self.health_url:
            return self._check_http()
        if self.health_tcp and self.port:
            return self._check_tcp()
        # No health endpoint — just check process is alive
        if self.process and self.process.poll() is None:
            return True
        return False

    def _check_tcp(self) -> bool:
        try:
            s = socket.create_connection(("127.0.0.1", self.port), timeout=1)
            s.close()
            return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            return False

    def _check_http(self) -> bool:
        import urllib.request
        try:
            req = urllib.request.Request(self.health_url, method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status < 500
        except Exception:
            return False

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


# ── Build service list ───────────────────────────────────────────────

def build_services(args: argparse.Namespace) -> list[Service]:
    services: list[Service] = []

    # 1. Analytics API (FastAPI + WS ingest)
    services.append(Service(
        name="Analytics API",
        cmd=[PYTHON, "-m", "live_analytics.app.main"],
        port=8080,
        health_url="http://127.0.0.1:8080/healthz",
    ))

    # 2. Questionnaire API
    services.append(Service(
        name="Questionnaire API",
        cmd=[PYTHON, "-m", "live_analytics.questionnaire.app"],
        port=8090,
        health_url="http://127.0.0.1:8090/api/healthz",
    ))

    # 3. System Check GUI
    services.append(Service(
        name="System Check GUI",
        cmd=[PYTHON, "-m", "live_analytics.system_check.app"],
        port=8095,
        health_url="http://127.0.0.1:8095/api/healthz",
    ))

    # 4. Streamlit Dashboard
    if not args.no_dashboard:
        dashboard_script = str(ROOT / "live_analytics" / "dashboard" / "streamlit_app.py")
        services.append(Service(
            name="Dashboard",
            cmd=[
                PYTHON, "-m", "streamlit", "run", dashboard_script,
                "--server.port", "8501",
                "--server.headless", "true",
                "--browser.gatherUsageStats", "false",
            ],
            port=8501,
            health_tcp=True,
        ))

    # 5. Wahoo Bridge (optional)
    if args.bridge:
        bridge_script = str(ROOT / "bridge" / (
            "mock_wahoo_bridge.py" if args.mock else "bike_bridge.py"
        ))
        bridge_cmd = [PYTHON, bridge_script]
        if not args.mock:
            bridge_cmd.append("--live")
        services.append(Service(
            name="Wahoo Bridge" + (" (mock)" if args.mock else ""),
            cmd=bridge_cmd,
            port=8765,
            health_tcp=True,
        ))

        # 6. Bridge → Analytics forwarder (replaces Unity's role for testing)
        forwarder_script = str(ROOT / "bridge" / "forward_to_analytics.py")
        services.append(Service(
            name="HR Forwarder",
            cmd=[
                PYTHON, forwarder_script,
                "--bridge-url", "ws://localhost:8765",
                "--ingest-url", "ws://localhost:8766",
            ],
            port=0,          # no dedicated port to health-check
            health_tcp=False,
        ))

    return services


# ── Live status display ──────────────────────────────────────────────

_STATUS_ICON = {
    "starting": f"{_YELLOW}o{_RESET}" if not _UNICODE else f"{_YELLOW}\u25cc{_RESET}",
    "ok":       f"{_GREEN}*{_RESET}" if not _UNICODE else f"{_GREEN}\u25cf{_RESET}",
    "error":    f"{_RED}x{_RESET}" if not _UNICODE else f"{_RED}\u2717{_RESET}",
    "skipped":  f"{_DIM}o{_RESET}" if not _UNICODE else f"{_DIM}\u25cb{_RESET}",
}

_STATUS_LABEL = {
    "starting": f"{_YELLOW}starting ...{_RESET}",
    "ok":       f"{_GREEN}running{_RESET}",
    "error":    f"{_RED}error{_RESET}",
    "skipped":  f"{_DIM}skipped{_RESET}",
}

_CHECK = "*" if not _UNICODE else "\u2713"
_WARN  = "!" if not _UNICODE else "\u26a0"
_CROSS = "x" if not _UNICODE else "\u2717"
_ARROW = "->" if not _UNICODE else "\u2192"
_DOTS  = "..." if not _UNICODE else "\u2026"


def _print_header() -> None:
    print()
    print(f"  {_BOLD}================================================{_RESET}")
    print(f"  {_BOLD}     Bike VR - Master Launcher{_RESET}")
    print(f"  {_BOLD}================================================{_RESET}")
    print()


def _print_status(services: list[Service], elapsed: float) -> int:
    """Print/refresh the status table. Returns number of lines printed."""
    lines = []
    for svc in services:
        icon = _STATUS_ICON.get(svc.status, "?")
        label = _STATUS_LABEL.get(svc.status, svc.status)
        port_str = f"{_DIM}:{svc.port}{_RESET}" if svc.port else f"{_DIM}     {_RESET}"
        lines.append(f"  {icon}  {_BOLD}{svc.name:<25}{_RESET} {port_str:>18}   {label}")

    # Summary line
    ok_count = sum(1 for s in services if s.status == "ok")
    total = len(services)
    if ok_count == total:
        summary = f"  {_GREEN}{_BOLD}{_CHECK} All {total} services running!{_RESET}  ({elapsed:.0f}s)"
    else:
        summary = f"  {_DIM}{ok_count}/{total} klar  ({elapsed:.0f}s){_RESET}"

    lines.append("")
    lines.append(summary)

    for line in lines:
        print(line)

    return len(lines)


def _clear_lines(n: int) -> None:
    """Move cursor up N lines and clear them."""
    for _ in range(n):
        print(f"{_CURSOR_UP}{_CLEAR_LINE}", end="")


# ── Main loop ────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Start all Bike VR services",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Services started:
              - Analytics API       http://127.0.0.1:8080
              - Questionnaire API   http://127.0.0.1:8090
              - System Check GUI    http://127.0.0.1:8095
              - Streamlit Dashboard http://127.0.0.1:8501
              - Wahoo BLE Bridge    ws://127.0.0.1:8765  (with --bridge)
        """),
    )
    parser.add_argument("--bridge", action="store_true", help="Also start Wahoo BLE bridge")
    parser.add_argument("--mock", action="store_true", help="Use mock bridge instead of real BLE")
    parser.add_argument("--no-dashboard", action="store_true", help="Skip Streamlit dashboard")
    args = parser.parse_args()

    services = build_services(args)

    _print_header()

    # Init DB before starting services
    print(f"  {_DIM}Initialising databases {_DOTS}{_RESET}")
    subprocess.run(
        [PYTHON, str(ROOT / "live_analytics" / "scripts" / "init_db.py")],
        cwd=str(ROOT),
        capture_output=True,
    )
    print(f"  {_GREEN}{_CHECK}{_RESET} Databases ready")
    print()

    # Start all services
    for svc in services:
        svc.start()

    # Poll health checks with live status updates
    t0 = time.time()
    printed_lines = 0
    timeout = 45  # max seconds to wait for all services
    all_ok = False

    try:
        while time.time() - t0 < timeout:
            # Check health for each service
            for svc in services:
                if svc.status == "starting":
                    if svc.check_health():
                        svc.status = "ok"

            # Refresh display
            if printed_lines > 0:
                _clear_lines(printed_lines)
            printed_lines = _print_status(services, time.time() - t0)

            # Are all services up?
            if all(s.status == "ok" for s in services):
                all_ok = True
                break

            time.sleep(1.0)

        # Final status
        if not all_ok:
            # Mark any still-starting as error
            for svc in services:
                if svc.status == "starting":
                    svc.status = "error"
            if printed_lines > 0:
                _clear_lines(printed_lines)
            _print_status(services, time.time() - t0)

        print()
        if all_ok:
            print(f"  {_BOLD}URLs:{_RESET}")
            print(f"    System Check  {_ARROW} {_CYAN}http://127.0.0.1:8095{_RESET}")
            print(f"    Dashboard     {_ARROW} {_CYAN}http://127.0.0.1:8501{_RESET}")
            print(f"    Analytics API {_ARROW} {_CYAN}http://127.0.0.1:8080{_RESET}")
            print(f"    Questionnaire {_ARROW} {_CYAN}http://127.0.0.1:8090{_RESET}")
            print()
        print(f"  {_DIM}Press Ctrl+C to stop all services{_RESET}")
        print()

        # Keep running until Ctrl+C
        while True:
            time.sleep(2)
            # Re-check for crashes
            any_down = False
            for svc in services:
                if svc.process and svc.process.poll() is not None and svc.status == "ok":
                    svc.status = "error"
                    any_down = True
            if any_down:
                print(f"\n  {_RED}{_BOLD}{_WARN} A service has stopped!{_RESET}")
                for svc in services:
                    if svc.status == "error":
                        print(f"    {_RED}{_CROSS} {svc.name}{_RESET}")
                print()

    except KeyboardInterrupt:
        pass
    finally:
        print()
        print(f"  {_DIM}Stopping services {_DOTS}{_RESET}")
        for svc in reversed(services):
            if svc.process:
                svc.stop()
        print(f"  {_GREEN}{_CHECK}{_RESET} All services stopped.")
        print()


if __name__ == "__main__":
    main()
