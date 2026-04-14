"""
System health-check functions.

Each check returns a dict with at least:
  { "ok": bool, "label": str, "detail": str, "severity": str }
plus optional extra keys.

**Severity levels** (tri-state):
  - ``"ok"``    – everything works (green)
  - ``"warn"``  – not online / not connected *yet* (yellow)
  - ``"error"`` – real failure: corruption, crash, timeout (red)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import struct
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("system_check.checks")


# ═══════════════════════════════════════════════════════════════════════
#  1. Meta Quest 3 headset (via ADB)
# ═══════════════════════════════════════════════════════════════════════

def check_quest_headset() -> dict[str, Any]:
    """Check if a Meta Quest headset is connected via ADB.

    Runs ``adb devices`` and looks for at least one device line that
    shows 'device' status (not 'unauthorized' or 'offline').
    """
    label = "Meta Quest 3 Headset"

    # Check if adb is available
    adb = shutil.which("adb")
    if not adb:
        return {"ok": False, "severity": "warn", "label": label,
                "detail": "ADB ikke fundet i PATH. Installér Android SDK Platform Tools.",
                "hint": "brew install --cask android-platform-tools"}

    try:
        result = subprocess.run(
            [adb, "devices"],
            capture_output=True, text=True, timeout=5,
        )
        lines = result.stdout.strip().split("\n")
        # Skip header line "List of devices attached"
        devices = []
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                serial, status = parts[0], parts[1]
                devices.append({"serial": serial, "status": status})

        connected = [d for d in devices if d["status"] == "device"]
        quest_devices = [d for d in connected
                         if "quest" in d["serial"].lower() or "oculus" in d["serial"].lower()
                         or True]  # any ADB device is accepted

        if connected:
            serial = connected[0]["serial"]
            # Try to get model name
            model = _adb_get_model(adb, serial)
            return {"ok": True, "severity": "ok", "label": label,
                    "detail": f"Forbundet: {model or serial}",
                    "serial": serial, "model": model, "devices": devices}

        if devices:
            # Devices present but not authorized
            unauthorized = [d for d in devices if d["status"] != "device"]
            return {"ok": False, "severity": "error", "label": label,
                    "detail": f"Headset fundet men status: {unauthorized[0]['status']}. Godkend forbindelsen på headsettet.",
                    "devices": devices}

        return {"ok": False, "severity": "warn", "label": label,
                "detail": "Ingen headset fundet. Tænd Quest 3 og tilslut USB.",
                "devices": []}

    except subprocess.TimeoutExpired:
        return {"ok": False, "severity": "error", "label": label, "detail": "ADB timeout – prøv igen"}
    except Exception as e:
        return {"ok": False, "severity": "error", "label": label, "detail": f"ADB fejl: {e}"}


def _adb_get_model(adb: str, serial: str) -> str | None:
    """Try to read the device model via adb shell."""
    try:
        result = subprocess.run(
            [adb, "-s", serial, "shell", "getprop", "ro.product.model"],
            capture_output=True, text=True, timeout=3,
        )
        model = result.stdout.strip()
        return model if model else None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════
#  2. Database access
# ═══════════════════════════════════════════════════════════════════════

def check_database(db_path: Path, db_name: str = "Database") -> dict[str, Any]:
    """Check if a SQLite database file exists and is readable."""
    label = f"Database: {db_name}"

    if not db_path.exists():
        return {"ok": False, "severity": "warn", "label": label,
                "detail": f"Fil ikke fundet: {db_path}",
                "path": str(db_path)}

    try:
        conn = sqlite3.connect(str(db_path), timeout=2)
        conn.execute("PRAGMA integrity_check")
        # Count tables
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t[0] for t in tables]
        conn.close()

        size_kb = db_path.stat().st_size / 1024
        return {"ok": True, "severity": "ok", "label": label,
                "detail": f"OK – {len(table_names)} tabeller, {size_kb:.1f} KB",
                "path": str(db_path),
                "tables": table_names,
                "size_kb": round(size_kb, 1)}

    except sqlite3.Error as e:
        return {"ok": False, "severity": "error", "label": label,
                "detail": f"SQLite fejl: {e}",
                "path": str(db_path)}
    except Exception as e:
        return {"ok": False, "severity": "error", "label": label,
                "detail": f"Fejl: {e}",
                "path": str(db_path)}


# ═══════════════════════════════════════════════════════════════════════
#  3. Heart-rate monitor / Bridge connection
# ═══════════════════════════════════════════════════════════════════════

def check_bridge_connection(ws_url: str = "ws://localhost:8765") -> dict[str, Any]:
    """Check if the bike bridge WebSocket server is running and responsive.

    Attempts a quick HTTP-upgrade probe. Does NOT require a full WS handshake —
    we just check if the port is accepting connections.
    """
    label = "Pulsmåler / Bridge"

    import socket
    import urllib.parse

    parsed = urllib.parse.urlparse(ws_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 8765

    try:
        sock = socket.create_connection((host, port), timeout=2)
        sock.close()

        # Port is open – now try a real WebSocket connect to get handshake
        detail = f"Bridge kører på {ws_url}"
        protocol = None
        try:
            protocol = _ws_probe(ws_url)
            if protocol:
                detail = f"Bridge forbundet – protokol: {protocol}"
        except Exception:
            detail += " (handshake fejlede, men port er åben)"

        return {"ok": True, "severity": "ok", "label": label, "detail": detail,
                "url": ws_url, "protocol": protocol}

    except (socket.timeout, ConnectionRefusedError, OSError):
        return {"ok": False, "severity": "warn", "label": label,
                "detail": f"Ingen forbindelse til bridge på {ws_url}. Start bike_bridge.py først.",
                "url": ws_url}
    except Exception as e:
        return {"ok": False, "severity": "error", "label": label,
                "detail": f"Fejl: {e}", "url": ws_url}


def _ws_probe(ws_url: str, timeout: float = 2.0) -> str | None:
    """Quick synchronous WebSocket connect to read the bridge handshake protocol."""
    try:
        import asyncio
        import websockets

        async def _probe():
            async with websockets.connect(ws_url, open_timeout=timeout, close_timeout=1) as ws:
                msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                if isinstance(msg, str):
                    data = json.loads(msg)
                    return data.get("protocol")
            return None

        # Run in a new event loop (safe from any existing loop)
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_probe())
        finally:
            loop.close()
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════
#  4. VRS Session log files
# ═══════════════════════════════════════════════════════════════════════

def check_vrsf_logs(
    log_base: Path,
    expected_files: list[str] | None = None,
) -> dict[str, Any]:
    """Check for VRS session log files after a simulation.

    Looks in ``log_base`` for ``session_*`` directories containing the
    expected .vrsf files (headpose, bike, hr, events) and a manifest.json.
    """
    label = "VRS Logfiler"

    if expected_files is None:
        expected_files = ["headpose.vrsf", "bike.vrsf", "hr.vrsf", "events.vrsf", "manifest.json"]

    if not log_base.exists():
        return {"ok": False, "severity": "warn", "label": label,
                "detail": f"Logmappe ikke fundet: {log_base}",
                "path": str(log_base), "sessions": []}

    # Find session directories
    session_dirs = sorted(
        [d for d in log_base.iterdir() if d.is_dir() and d.name.startswith("session_")],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )

    if not session_dirs:
        return {"ok": False, "severity": "warn", "label": label,
                "detail": "Ingen session-mapper fundet. Kør en simulering først.",
                "path": str(log_base), "sessions": []}

    sessions = []
    for sd in session_dirs[:10]:  # max 10 newest
        files_present = [f.name for f in sd.iterdir() if f.is_file()]
        missing = [f for f in expected_files if f not in files_present]
        has_end = "manifest_end.json" in files_present

        # Total size
        total_bytes = sum(f.stat().st_size for f in sd.iterdir() if f.is_file())

        # Read manifest for session info
        session_info: dict[str, Any] = {"dir": sd.name, "path": str(sd)}
        manifest_path = sd / "manifest.json"
        if manifest_path.exists():
            try:
                with open(manifest_path) as f:
                    manifest = json.loads(f.read())
                session_info["session_id"] = manifest.get("session_id")
                session_info["started_unix_ms"] = manifest.get("started_unix_ms")
            except Exception:
                pass

        session_info["files_present"] = files_present
        session_info["missing_files"] = missing
        session_info["complete"] = len(missing) == 0
        session_info["finished"] = has_end
        session_info["total_kb"] = round(total_bytes / 1024, 1)
        sessions.append(session_info)

    # Latest session determines overall status
    latest = sessions[0]
    if latest["complete"] and latest["finished"]:
        detail = f"✓ Seneste session komplet: {latest['dir']} ({latest['total_kb']} KB)"
        ok = True
        severity = "ok"
    elif latest["complete"]:
        detail = f"Session kører: {latest['dir']} ({len(latest['files_present'])} filer)"
        ok = True
        severity = "ok"
    else:
        detail = f"Seneste session ufuldstændig: mangler {', '.join(latest['missing_files'])}"
        ok = False
        severity = "error"

    return {"ok": ok, "severity": severity, "label": label, "detail": detail,
            "path": str(log_base), "sessions": sessions,
            "total_sessions": len(session_dirs)}


# ═══════════════════════════════════════════════════════════════════════
#  4b. Verify a specific session by ID
# ═══════════════════════════════════════════════════════════════════════

def check_session_by_id(
    session_id: str,
    log_base: Path,
    expected_files: list[str] | None = None,
) -> dict[str, Any]:
    """Verify that a **specific** session has the correct log files.

    The *session_id* is matched against:
      1. Directory name  (``session_{session_id}``)
      2. ``manifest.json`` → ``session_id`` field  (numeric or string)
      3. ``manifest.json`` → ``display_id`` field
      4. ``sessions_history.ndjson`` → ``display_id`` or ``session_id``

    Returns a detailed status dict for that single session.
    """
    label = f"Session: {session_id}"

    if expected_files is None:
        expected_files = [
            "headpose.vrsf", "bike.vrsf", "hr.vrsf",
            "events.vrsf", "manifest.json",
        ]

    if not log_base.exists():
        return {
            "ok": False, "label": label,
            "detail": f"Logmappe ikke fundet: {log_base}",
            "session_id": session_id, "found": False,
        }

    # ── 1. Try direct directory match ─────────────────────────────────
    direct = log_base / f"session_{session_id}"
    if direct.is_dir():
        return _verify_session_dir(direct, session_id, expected_files, label)

    # ── 2. Scan manifest.json in every session_* dir ──────────────────
    session_dirs = [
        d for d in log_base.iterdir()
        if d.is_dir() and d.name.startswith("session_")
    ]
    for sd in session_dirs:
        manifest_path = sd / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            with open(manifest_path) as f:
                manifest = json.loads(f.read())
            m_sid = manifest.get("session_id")
            m_did = manifest.get("display_id")
            # Match against session_id (could be numeric string) or display_id
            if str(m_sid) == session_id or m_did == session_id:
                return _verify_session_dir(sd, session_id, expected_files, label)
        except Exception:
            continue

    # ── 3. Check sessions_history.ndjson ──────────────────────────────
    history_path = log_base / "sessions_history.ndjson"
    history_match: dict[str, Any] | None = None
    if history_path.exists():
        try:
            with open(history_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    e_sid = entry.get("session_id")
                    e_did = entry.get("display_id")
                    if str(e_sid) == session_id or e_did == session_id:
                        history_match = entry
                        # Try the dir recorded in history
                        hist_dir_name = entry.get("dir")
                        if hist_dir_name:
                            candidate = Path(hist_dir_name)
                            if not candidate.is_absolute():
                                candidate = log_base / candidate
                            if candidate.is_dir():
                                return _verify_session_dir(
                                    candidate, session_id, expected_files, label,
                                    history_entry=history_match,
                                )
        except Exception:
            pass

    # ── Not found ─────────────────────────────────────────────────────
    detail = f"Session '{session_id}' ikke fundet."
    if history_match:
        detail += f" Fundet i historik men mappe mangler: {history_match.get('dir', '?')}"
    return {
        "ok": False, "label": label, "detail": detail,
        "session_id": session_id, "found": False,
        "history_entry": history_match,
    }


def _verify_session_dir(
    session_dir: Path,
    session_id: str,
    expected_files: list[str],
    label: str,
    history_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check that a found session directory contains all expected files."""
    files_present = [f.name for f in session_dir.iterdir() if f.is_file()]
    missing = [f for f in expected_files if f not in files_present]
    has_end = "manifest_end.json" in files_present
    total_bytes = sum(f.stat().st_size for f in session_dir.iterdir() if f.is_file())

    # Read manifest for extra info
    manifest_info: dict[str, Any] = {}
    manifest_path = session_dir / "manifest.json"
    if manifest_path.exists():
        try:
            with open(manifest_path) as f:
                manifest_info = json.loads(f.read())
        except Exception:
            pass

    # Check that each .vrsf file is non-empty
    empty_files = [
        f for f in expected_files
        if f.endswith(".vrsf") and f in files_present
        and (session_dir / f).stat().st_size == 0
    ]

    complete = len(missing) == 0 and len(empty_files) == 0
    ok = complete

    if complete and has_end:
        detail = f"✓ Session '{session_id}' komplet og afsluttet ({len(files_present)} filer, {round(total_bytes/1024, 1)} KB)"
    elif complete:
        detail = f"Session '{session_id}' har alle filer men er ikke afsluttet (mangler manifest_end.json)"
    elif missing:
        detail = f"Session '{session_id}' mangler: {', '.join(missing)}"
        if empty_files:
            detail += f" · Tomme filer: {', '.join(empty_files)}"
    else:
        detail = f"Session '{session_id}' har tomme filer: {', '.join(empty_files)}"

    return {
        "ok": ok,
        "label": label,
        "detail": detail,
        "session_id": session_id,
        "found": True,
        "dir": session_dir.name,
        "path": str(session_dir),
        "files_present": files_present,
        "missing_files": missing,
        "empty_files": empty_files,
        "complete": complete,
        "finished": has_end,
        "total_kb": round(total_bytes / 1024, 1),
        "manifest": manifest_info,
        "history_entry": history_entry,
    }


# ═══════════════════════════════════════════════════════════════════════
#  5. Analytics service
# ═══════════════════════════════════════════════════════════════════════

def check_service_http(url: str, name: str = "Service") -> dict[str, Any]:
    """Check if an HTTP service is responding (GET /api/healthz or /)."""
    label = f"Service: {name}"
    import urllib.request

    for path in ["/api/healthz", "/api/sessions", "/"]:
        try:
            req = urllib.request.Request(url + path, method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                status = resp.status
                body = resp.read().decode("utf-8", errors="ignore")[:200]
                return {"ok": True, "severity": "ok", "label": label,
                        "detail": f"OK – HTTP {status} på {url}{path}",
                        "url": url, "status": status}
        except Exception:
            continue

    return {"ok": False, "severity": "warn", "label": label,
            "detail": f"Ingen svar fra {url}. Start serveren først.",
            "url": url}


# ═══════════════════════════════════════════════════════════════════════
#  Run all checks
# ═══════════════════════════════════════════════════════════════════════

def run_all_checks(
    analytics_db: Path | None = None,
    questionnaire_db: Path | None = None,
    bridge_ws_url: str = "ws://localhost:8765",
    analytics_api_url: str = "http://localhost:8080",
    questionnaire_api_url: str = "http://localhost:8090",
    vrs_log_base: Path | None = None,
    expected_vrsf: list[str] | None = None,
) -> dict[str, Any]:
    """Run every health check and return a combined result dict."""
    from live_analytics.system_check import (
        ANALYTICS_DB, QUESTIONNAIRE_DB, BRIDGE_WS_URL,
        ANALYTICS_API_URL, QUESTIONNAIRE_API_URL, VRS_LOG_BASE,
        EXPECTED_VRSF_FILES,
    )

    t0 = time.time()
    results: dict[str, Any] = {}

    # 1. Quest headset
    results["quest_headset"] = check_quest_headset()

    # 2. Databases
    results["analytics_db"] = check_database(
        analytics_db or ANALYTICS_DB, "Live Analytics")
    results["questionnaire_db"] = check_database(
        questionnaire_db or QUESTIONNAIRE_DB, "Spørgeskema")

    # 3. Bridge / heart rate
    results["bridge_connection"] = check_bridge_connection(bridge_ws_url or BRIDGE_WS_URL)

    # 4. Services
    results["analytics_api"] = check_service_http(
        analytics_api_url or ANALYTICS_API_URL, "Analytics API")
    results["questionnaire_api"] = check_service_http(
        questionnaire_api_url or QUESTIONNAIRE_API_URL, "Spørgeskema API")

    # 5. VRS logs
    results["vrsf_logs"] = check_vrsf_logs(
        vrs_log_base or VRS_LOG_BASE,
        expected_vrsf or EXPECTED_VRSF_FILES)

    elapsed = time.time() - t0
    checks_only = {k: v for k, v in results.items() if isinstance(v, dict) and "ok" in v}
    passed  = sum(1 for r in checks_only.values() if r.get("ok"))
    warned  = sum(1 for r in checks_only.values() if not r.get("ok") and r.get("severity") == "warn")
    failed  = sum(1 for r in checks_only.values() if not r.get("ok") and r.get("severity") == "error")
    all_ok  = all(r["ok"] for r in checks_only.values())
    results["_summary"] = {
        "all_ok": all_ok,
        "passed": passed,
        "warned": warned,
        "failed": failed,
        "total": len(checks_only),
        "elapsed_s": round(elapsed, 3),
        "timestamp": time.time(),
    }

    return results
