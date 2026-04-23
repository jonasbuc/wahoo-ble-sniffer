"""
Shared environment-variable helpers used across all service configs.

Keeps the helper definitions in one place so each config module
just imports them rather than re-declaring them.
"""

from __future__ import annotations

import logging
import os

_log = logging.getLogger("live_analytics.env_utils")


def int_env(name: str, default: int) -> int:
    """Parse an int env var, returning *default* on missing/empty/invalid."""
    val = os.getenv(name)
    if not val:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        _log.warning("Invalid int for %s=%r, using default %d", name, val, default)
        return default


def float_env(name: str, default: float) -> float:
    """Parse a float env var, returning *default* on missing/empty/invalid."""
    val = os.getenv(name)
    if not val:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        _log.warning("Invalid float for %s=%r, using default %.2f", name, val, default)
        return default
