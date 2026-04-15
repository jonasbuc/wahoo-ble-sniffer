#!/usr/bin/env python3
"""
System Check – CLI runner.

Runs every health check from the terminal and prints a coloured summary.
No server required – just:

    python -m live_analytics.system_check.run_checks

Options:
    --json   Output raw JSON instead of the pretty table
    --check  NAME   Run only one check (quest, analytics-db, questionnaire-db,
                     bridge, analytics-api, questionnaire-api, vrsf-logs)
    --session ID     Verify a specific session by ID
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

# ── Force UTF-8 on Windows ───────────────────────────────────────────
if sys.platform == "win32":
    os.system("")  # enable VT100 ANSI on Windows 10+
    if sys.stdout.encoding and sys.stdout.encoding.lower().replace("-", "") != "utf8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

from live_analytics.system_check import (
    ANALYTICS_API_URL,
    ANALYTICS_DB,
    BRIDGE_WS_URL,
    EXPECTED_VRSF_FILES,
    QUESTIONNAIRE_API_URL,
    QUESTIONNAIRE_DB,
    VRS_LOG_BASE,
)
from live_analytics.system_check.checks import (
    check_bridge_connection,
    check_database,
    check_quest_headset,
    check_service_http,
    check_session_by_id,
    check_vrsf_logs,
    run_all_checks,
)


# ── ANSI colours ──────────────────────────────────────────────────────
_RESET = "\033[0m"
_BOLD = "\033[1m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_DIM = "\033[2m"

_ICON = {
    "ok": f"{_GREEN}*{_RESET}",
    "warn": f"{_YELLOW}!{_RESET}",
    "error": f"{_RED}x{_RESET}",
}


def _severity_colour(sev: str) -> str:
    return {"ok": _GREEN, "warn": _YELLOW, "error": _RED}.get(sev, "")


def _print_result(result: dict[str, Any]) -> None:
    """Pretty-print a single check result."""
    sev = result.get("severity", "error" if not result.get("ok") else "ok")
    icon = _ICON.get(sev, "?")
    colour = _severity_colour(sev)
    label = result.get("label", "?")
    detail = result.get("detail", "")
    print(f"  {icon}  {colour}{_BOLD}{label}{_RESET}")
    print(f"     {_DIM}{detail}{_RESET}")


def _print_summary(summary: dict[str, Any]) -> None:
    """Print the bottom summary bar."""
    passed = summary.get("passed", 0)
    warned = summary.get("warned", 0)
    failed = summary.get("failed", 0)
    total = summary.get("total", 0)
    elapsed = summary.get("elapsed_s", 0)

    parts = []
    if passed:
        parts.append(f"{_GREEN}{passed} ok{_RESET}")
    if warned:
        parts.append(f"{_YELLOW}{warned} warn{_RESET}")
    if failed:
        parts.append(f"{_RED}{failed} error{_RESET}")

    bar = " | ".join(parts)
    print()
    print(f"  {_BOLD}Result:{_RESET}  {bar}  ({total} checks, {elapsed:.2f}s)")

    if summary.get("all_ok"):
        print(f"\n  {_GREEN}{_BOLD}All clear!{_RESET}")
    elif failed:
        print(f"\n  {_RED}{_BOLD}There are errors that need fixing.{_RESET}")
    else:
        print(f"\n  {_YELLOW}{_BOLD}Warnings - but no critical errors.{_RESET}")


# ── Single-check dispatch ────────────────────────────────────────────
_SINGLE_CHECKS: dict[str, Any] = {
    "quest": lambda: check_quest_headset(),
    "analytics-db": lambda: check_database(ANALYTICS_DB, "Live Analytics"),
    "questionnaire-db": lambda: check_database(QUESTIONNAIRE_DB, "Spørgeskema"),
    "bridge": lambda: check_bridge_connection(BRIDGE_WS_URL),
    "analytics-api": lambda: check_service_http(ANALYTICS_API_URL, "Analytics API"),
    "questionnaire-api": lambda: check_service_http(QUESTIONNAIRE_API_URL, "Spørgeskema API"),
    "vrsf-logs": lambda: check_vrsf_logs(VRS_LOG_BASE, EXPECTED_VRSF_FILES),
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="System Check – kør sundhedstjek fra terminalen",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Udskriv resultater som JSON",
    )
    parser.add_argument(
        "--check", metavar="NAME",
        choices=list(_SINGLE_CHECKS),
        help=f"Kør kun ét check: {', '.join(_SINGLE_CHECKS)}",
    )
    parser.add_argument(
        "--session", metavar="ID",
        help="Verificér en specifik session efter ID",
    )
    args = parser.parse_args()

    # ── Single session lookup ─────────────────────────────────────────
    if args.session:
        result = check_session_by_id(args.session, VRS_LOG_BASE, EXPECTED_VRSF_FILES)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print()
            _print_result(result)
            print()
        sys.exit(0 if result.get("ok") else 1)

    # ── Single check ──────────────────────────────────────────────────
    if args.check:
        result = _SINGLE_CHECKS[args.check]()
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print()
            _print_result(result)
            print()
        sys.exit(0 if result.get("ok") else 1)

    # ── Run all checks ────────────────────────────────────────────────
    print()
    print(f"  {_BOLD}======================================{_RESET}")
    print(f"  {_BOLD}         System Check{_RESET}")
    print(f"  {_BOLD}======================================{_RESET}")
    print()

    results = run_all_checks()

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        summary = results.get("_summary", {})
        sys.exit(0 if summary.get("all_ok") else 1)

    # Pretty-print each check
    for key, value in results.items():
        if key == "_summary" or not isinstance(value, dict) or "ok" not in value:
            continue
        _print_result(value)
        print()

    # Summary bar
    summary = results.get("_summary", {})
    if summary:
        _print_summary(summary)

    print()
    sys.exit(0 if summary.get("all_ok") else 1)


if __name__ == "__main__":
    main()
