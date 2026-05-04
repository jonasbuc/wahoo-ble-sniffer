"""
Tests for live_analytics.app.utils.time_utils.

All assertions use regex / structural checks rather than hardcoded local
timestamps so the tests pass regardless of the machine's timezone.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from live_analytics.app.utils.time_utils import (
    fmt_dt,
    fmt_iso,
    fmt_now,
    fmt_unix_ms,
    now_utc_iso,
)

# ── Format pattern ────────────────────────────────────────────────────
# Matches "2026-05-04 14:22:18 CEST" or "2026-05-04 14:22:18 UTC" etc.
_RE_FMT = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \S+$")

# Valid unix_ms for 2026-05-04 14:22:18 UTC
_VALID_MS = 1_746_360_138_000


def _looks_like_fmt(s: str) -> bool:
    return bool(_RE_FMT.match(s))


# ── fmt_now ───────────────────────────────────────────────────────────

def test_fmt_now_returns_correct_format():
    result = fmt_now()
    assert _looks_like_fmt(result), f"unexpected format: {result!r}"


# ── fmt_dt ────────────────────────────────────────────────────────────

def test_fmt_dt_utc_aware():
    dt = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)
    result = fmt_dt(dt)
    assert _looks_like_fmt(result), f"unexpected format: {result!r}"


def test_fmt_dt_naive_treated_as_local():
    dt = datetime(2026, 5, 4, 14, 22, 18)
    result = fmt_dt(dt)
    assert _looks_like_fmt(result), f"unexpected format: {result!r}"


# ── fmt_unix_ms ───────────────────────────────────────────────────────

def test_fmt_unix_ms_valid():
    result = fmt_unix_ms(_VALID_MS)
    assert _looks_like_fmt(result), f"unexpected format: {result!r}"


def test_fmt_unix_ms_none():
    assert fmt_unix_ms(None) == "—"


def test_fmt_unix_ms_zero():
    assert fmt_unix_ms(0).startswith("—")


def test_fmt_unix_ms_negative():
    assert fmt_unix_ms(-1).startswith("—")


def test_fmt_unix_ms_too_large():
    assert fmt_unix_ms(99_999_999_999_999).startswith("—")


def test_fmt_unix_ms_float_input():
    result = fmt_unix_ms(float(_VALID_MS))
    assert _looks_like_fmt(result), f"unexpected format: {result!r}"


def test_fmt_unix_ms_invalid_invalid_ts_msg():
    result = fmt_unix_ms(12345)
    assert "invalid ts" in result


# ── fmt_iso ───────────────────────────────────────────────────────────

def test_fmt_iso_utc_with_offset():
    result = fmt_iso("2026-05-04T12:19:21.776249+00:00")
    assert _looks_like_fmt(result), f"unexpected format: {result!r}"


def test_fmt_iso_naive_treated_as_utc():
    result = fmt_iso("2026-05-04T12:19:21")
    assert _looks_like_fmt(result), f"unexpected format: {result!r}"


def test_fmt_iso_none():
    assert fmt_iso(None) == "—"


def test_fmt_iso_empty_string():
    assert fmt_iso("") == "—"


def test_fmt_iso_garbage():
    assert fmt_iso("not-a-date") == "—"


# ── now_utc_iso ───────────────────────────────────────────────────────

def test_now_utc_iso_is_valid_iso():
    result = now_utc_iso()
    # Must parse back without errors
    dt = datetime.fromisoformat(result)
    assert dt.tzinfo is not None, "now_utc_iso should return a timezone-aware string"


def test_now_utc_iso_contains_utc_offset():
    result = now_utc_iso()
    # Should contain "+00:00" (UTC offset) at the end
    assert result.endswith("+00:00"), f"unexpected value: {result!r}"
