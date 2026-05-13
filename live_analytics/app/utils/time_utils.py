"""
Shared timestamp formatting utilities.

All human-visible timestamps in the system — Streamlit dashboard, JSONL logs,
API responses, terminal output — must go through these functions so the format
is consistent everywhere:

    2026-05-04 14:22:18 CEST

The canonical format is:  ``YYYY-MM-DD HH:MM:SS <timezone-abbreviation>``

Design notes
------------
* **Danish time (Europe/Copenhagen) is always used for display** regardless of
  what the OS timezone is set to.  This ensures CET/CEST labels are always
  correct and the clock always matches a Danish wall clock.
* The timezone abbreviation (CEST / CET) is appended so the reader knows
  exactly which zone is shown without having to guess.
* UTC ISO-8601 strings (``created_at`` / ``updated_at`` from the DB) are parsed
  and converted to Danish time before display.
* unix_ms timestamps from the telemetry stream are converted explicitly to
  Europe/Copenhagen — NOT via the OS local timezone.
* DB storage and wire formats always use Danish time via ``now_cph_iso()``
  so raw DB values are directly readable as Danish wall-clock times.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ── Danish timezone (exported so callers don't need to import zoneinfo) ───────
#: ``ZoneInfo("Europe/Copenhagen")`` — use this everywhere a Danish wall-clock
#: time is needed instead of relying on the OS local timezone.
TZ = ZoneInfo("Europe/Copenhagen")

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
    """Return the current Danish time as a formatted string.

    Example: ``"2026-05-04 14:22:18 CEST"``
    """
    return datetime.now(TZ).strftime(_FMT)


def fmt_dt(dt: datetime) -> str:
    """Format a :class:`datetime` object to the canonical Danish-time string.

    If *dt* is timezone-aware it is converted to Europe/Copenhagen first.
    If it is naive it is assumed to be UTC (DB convention).

    Example::

        fmt_dt(datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc))
        # → "2026-05-04 14:00:00 CEST"
    """
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TZ).strftime(_FMT)
    except Exception:
        return _MISSING


def fmt_unix_ms(unix_ms: int | float | None) -> str:
    """Convert a Unix-millisecond timestamp to a human-readable Danish-time string.

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
        return datetime.fromtimestamp(ms / 1000, tz=TZ).strftime(_FMT)
    except Exception:
        return _MISSING


def fmt_iso(iso_str: str | None) -> str:
    """Parse a UTC ISO-8601 string and return a human-readable Danish-time string.

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
        return dt.astimezone(TZ).strftime(_FMT)
    except Exception:
        return _MISSING


def now_utc_iso() -> str:
    """Return the current UTC time as ISO-8601.

    .. deprecated::
        Prefer :func:`now_cph_iso` for new code so DB timestamps are in Danish
        time.  This function is kept for compatibility with any callers that
        explicitly need a UTC offset string.

    Example: ``"2026-05-04T12:22:18.123456+00:00"``
    """
    return datetime.now(timezone.utc).isoformat()


def now_cph_iso() -> str:
    """Return the current Danish time (Europe/Copenhagen) as ISO-8601.

    All DB fields (``created_at``, ``started_at``, ``ended_at``, etc.) use
    this function so the raw value stored in SQLite is immediately readable
    as a Danish wall-clock time.

    Example (summer / CEST):  ``"2026-05-04T14:22:18.123456+02:00"``
    Example (winter / CET):   ``"2026-01-10T09:15:00.000000+01:00"``
    """
    return datetime.now(TZ).isoformat()


def unix_ms_to_cph_iso(unix_ms: int | float) -> str:
    """Convert a Unix-millisecond timestamp to a Danish-time ISO-8601 string.

    Used when a telemetry record's ``unix_ms`` must be stored as a
    human-readable timestamp in DB columns.

    Example: ``unix_ms_to_cph_iso(1_746_360_138_960)``
             → ``"2025-05-04T14:02:18.960000+02:00"``
    """
    return datetime.fromtimestamp(unix_ms / 1000, tz=TZ).isoformat()
