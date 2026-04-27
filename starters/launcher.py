#!/usr/bin/env python3
"""
Bike VR - Master Launcher
=================================================================

Starts **all** services in the correct order, then opens a live
status dashboard in the terminal that turns green as each service
comes online.

Services started:
  1. Analytics API        (FastAPI, port 8080 + WS ingest 8766)
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


# ── Log rotation ─────────────────────────────────────────────────────

_LOG_MAX_BYTES = 2 * 1024 * 1024  # 2 MB per log file
_LOG_BACKUPS = 3                   # keep .log.1 / .log.2 / .log.3


def _rotate_log(log_path: Path) -> None:
    """Rotate *log_path* before each new service run.

    The current log becomes .log.1; up to _LOG_BACKUPS numbered backups
    are kept (.log.1 is most recent). If the oldest backup would exceed
    _LOG_BACKUPS it is deleted.  Called unconditionally so every run
    starts with a fresh, empty log file.
    """
    if not log_path.exists():
        return
    # Shift existing backups: .log.3 deleted, .log.2 → .log.3, …
    for i in range(_LOG_BACKUPS, 0, -1):
        src = log_path.with_suffix(f"{log_path.suffix}.{i}")
        dst = log_path.with_suffix(f"{log_path.suffix}.{i + 1}")
        if src.exists():
            if i == _LOG_BACKUPS:
                src.unlink()  # drop the oldest backup
            else:
                src.rename(dst)
    # Current log → .log.1
    log_path.rename(log_path.with_suffix(f"{log_path.suffix}.1"))


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
        self.log_file: Path | None = None

    def start(self) -> None:
        # Services with no cmd are passive health-check entries (e.g. WS ingest
        # port that is managed by the Analytics API process).  Nothing to launch.
        if not self.cmd:
            self.status = "starting"
            return
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        log_dir = ROOT / "logs"
        log_dir.mkdir(exist_ok=True)
        safe_name = self.name.lower().replace(" ", "_")
        self.log_file = log_dir / f"{safe_name}.log"
        _rotate_log(self.log_file)
        try:
            log_fh = self.log_file.open("w", encoding="utf-8", errors="replace")
            self.process = subprocess.Popen(
                self.cmd,
                cwd=self.cwd,
                env=env,
                stdout=log_fh,
                stderr=log_fh,
            )
            # Close our handle — the child process inherits it and keeps the file
            # open.  Without this close() on the parent side the file handle leaks
            # (especially visible on Windows where it prevents log rotation).
            log_fh.close()
            self.status = "starting"
        except Exception as e:
            self.status = "error"
            print(
                f"\n  {_RED}✗{_RESET}  Failed to start '{self.name}': "
                f"{type(e).__name__}: {e}\n"
                f"     Command: {' '.join(self.cmd)}\n"
                f"     CWD:     {self.cwd}\n",
                flush=True,
            )

    def check_health(self) -> bool:
        """Return True if the service is responding."""
        if self.process and self.process.poll() is not None:
            self.status = "error"
            if self.log_file and self.log_file.exists():
                # Read only the last 8 KB to avoid blocking on multi-MB logs
                try:
                    with self.log_file.open("r", encoding="utf-8", errors="replace") as _lf:
                        _lf.seek(0, 2)                      # seek to end
                        size = _lf.tell()
                        _lf.seek(max(0, size - 8192))       # last 8 KB
                        tail_text = _lf.read()
                    lines = tail_text.splitlines()
                    tail = lines[-10:]
                except OSError:
                    tail = []
                if tail:
                    print(
                        f"\n  {_RED}✗{_RESET}  '{self.name}' crashed "
                        f"(exit {self.process.poll()}). "
                        f"Log: {self.log_file}\n"
                        + "\n".join(f"     {l}" for l in tail) + "\n",
                        flush=True,
                    )
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
        import urllib.error
        try:
            req = urllib.request.Request(self.health_url, method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status >= 500:
                    # Service is up but returning a server error – mark as error
                    # so we don't stay stuck in "starting" forever
                    self.status = "error"
                    return False
                return resp.status < 400
        except urllib.error.HTTPError as exc:
            if exc.code >= 500:
                self.status = "error"
            return False
        except (urllib.error.URLError, OSError):
            # Connection refused or network error – expected while service is starting up.
            return False
        except Exception as exc:
            import logging as _logging
            _logging.getLogger("launcher").warning(
                "Unexpected error checking health of '%s' at %s: %s: %s",
                self.name, self.health_url, type(exc).__name__, exc,
            )
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

    # 1b. WS Ingest port – separate TCP health check so a port-conflict on 8766
    #     is visible in the launcher status table rather than being invisible until
    #     Unity tries to connect.
    services.append(Service(
        name="WS Ingest (8766)",
        cmd=[],          # not a separate process – shares the Analytics API process
        port=8766,
        health_tcp=True,
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
    init_result = subprocess.run(
        [PYTHON, str(ROOT / "live_analytics" / "scripts" / "init_db.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if init_result.returncode != 0:
        print(
            f"\n  {_RED}✗{_RESET}  Database initialisation failed (exit {init_result.returncode})\n"
            + (f"     stdout: {init_result.stdout.strip()}\n" if init_result.stdout.strip() else "")
            + (f"     stderr: {init_result.stderr.strip()}\n" if init_result.stderr.strip() else "")
            + f"     Fix the error above and re-run the launcher.\n",
            flush=True,
        )
        sys.exit(1)
    print(f"  {_GREEN}{_CHECK}{_RESET} Databases ready")
    print()

    # Start all services
    # The Streamlit dashboard starts its first API call immediately on launch.
    # To avoid a confusing "● Unreachable" flash before the backend is ready,
    # start the backend services first and give them up to 15 s to come up
    # before releasing the dashboard and bridge.
    _BACKEND_NAMES = {"Analytics API", "WS Ingest (8766)", "Questionnaire API", "System Check GUI"}
    backend_svcs = [s for s in services if s.name in _BACKEND_NAMES]
    deferred_svcs = [s for s in services if s.name not in _BACKEND_NAMES]

    for svc in backend_svcs:
        svc.start()

    # Wait up to 15 s for the Analytics API (primary dependency of the dashboard)
    _api_svc = next((s for s in backend_svcs if s.name == "Analytics API"), None)
    if _api_svc and deferred_svcs:
        print(f"  {_DIM}Waiting for Analytics API to be ready before starting dashboard{_DOTS}{_RESET}")
        t_wait = time.time()
        while time.time() - t_wait < 15:
            if _api_svc.check_health():
                _api_svc.status = "ok"
                break
            if _api_svc.status == "error":
                break
            time.sleep(0.5)
        if _api_svc.status == "ok":
            print(f"  {_GREEN}{_CHECK}{_RESET} Analytics API ready – starting dashboard{_DOTS}")
        else:
            print(
                f"  {_YELLOW}{_WARN}{_RESET}  Analytics API not yet ready "
                f"(status={_api_svc.status}) – starting dashboard anyway"
            )

    for svc in deferred_svcs:
        svc.start()
    print()

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
