"""
Shared timestamp formatting utilities.

All human-visible timestamps in the system — Streamlit dashboard, JSONL logs,
API responses, terminal output — must go through these functions so the format
is consistent everywhere:

    2026-05-04 14:22:18 CEST

The canonical format is:  ``YYYY-MM-DD HH:MM:SS <timezone-abbreviation>``

Design notes
------------
* Local time is always displayed so times match the wall clock the operator is
  looking at.
* The timezone abbreviation (CEST, CET, UTC …) is appended so the reader knows
  exactly which zone is shown without having to guess.
* UTC ISO-8601 strings (``created_at`` / ``updated_at`` from the DB) are parsed
  and converted to local time before display.
* unix_ms timestamps from the telemetry stream are converted via
  ``datetime.fromtimestamp()`` which uses the OS local timezone — correct for
  single-machine deployments.
"""

from __future__ import annotations

from datetime import datetime, timezone

# ── Public format constant ────────────────────────────────────────────
# Change this one string to restyle every timestamp in the whole system.
_FMT = "%Y-%m-%d %H:%M:%S %Z"

# Sentinel for missing / invalid / out-of-range timestamps.
_MISSING = "—"

# Valid unix_ms range: 2000-01-01 → 2100-01-01 (guards against obviously
# wrong values like 0, negative numbers, or accidental seconds-not-ms).
_UNIX_MS_MIN = 946_684_800_000   # 2000-01-01 00:00:00 UTC
_UNIX_MS_MAX = 4_102_444_800_000  # 2100-01-01 00:00:00 UTC


def fmt_now() -> str:
    """Return the current local time as a formatted string.

    Example: ``"2026-05-04 14:22:18 CEST"``
    """
    return datetime.now().astimezone().strftime(_FMT)


def fmt_dt(dt: datetime) -> str:
    """Format a :class:`datetime` object to the canonical local-time string.

    If *dt* is timezone-aware it is converted to local time first.
    If it is naive it is assumed to be local time already.

    Example::

        fmt_dt(datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc))
        # → "2026-05-04 14:00:00 CEST"  (on a CEST machine)
    """
    try:
        return dt.astimezone().strftime(_FMT)
    except Exception:
        return _MISSING


def fmt_unix_ms(unix_ms: int | float | None) -> str:
    """Convert a Unix-millisecond timestamp to a human-readable local-time string.

    Returns ``"—"`` for None, out-of-range, or conversion errors.

    Example::

        fmt_unix_ms(1_746_360_138_960)
        # → "2026-05-04 14:22:18 CEST"
    """
    if unix_ms is None:
        return _MISSING
    try:
        ms = int(unix_ms)
    except (TypeError, ValueError):
        return _MISSING
    if not (_UNIX_MS_MIN <= ms <= _UNIX_MS_MAX):
        return f"—(invalid ts: {ms})"
    try:
        return datetime.fromtimestamp(ms / 1000).astimezone().strftime(_FMT)
    except Exception:
        return _MISSING


def fmt_iso(iso_str: str | None) -> str:
    """Parse a UTC ISO-8601 string and return a human-readable local-time string.

    Handles the format stored in the questionnaire and analytics DBs, e.g.:
    ``"2026-05-04T12:19:21.776249+00:00"``  →  ``"2026-05-04 14:19:21 CEST"``

    Returns ``"—"`` for None, empty strings, or parse errors.
    """
    if not iso_str:
        return _MISSING
    try:
        dt = datetime.fromisoformat(iso_str)
        # If the string carries no timezone info, assume UTC (DB convention).
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime(_FMT)
    except Exception:
        return _MISSING


def now_utc_iso() -> str:
    """Return the current UTC time as ISO-8601 (for DB storage).

    Example: ``"2026-05-04T12:22:18.123456+00:00"``
    """
    return datetime.now(timezone.utc).isoformat()
