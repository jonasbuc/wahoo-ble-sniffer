"""
Live Analytics – configuration via environment variables with sensible defaults.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

_log = logging.getLogger("live_analytics.config")


def _int_env(name: str, default: int) -> int:
    """Parse an int env var, returning *default* on missing/empty/invalid."""
    val = os.getenv(name)
    if not val:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        _log.warning("Invalid int for %s=%r, using default %d", name, val, default)
        return default


def _float_env(name: str, default: float) -> float:
    """Parse a float env var, returning *default* on missing/empty/invalid."""
    val = os.getenv(name)
    if not val:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        _log.warning("Invalid float for %s=%r, using default %.2f", name, val, default)
        return default


# ── Paths ─────────────────────────────────────────────────────────────
BASE_DIR = Path(os.getenv("LA_BASE_DIR", Path(__file__).resolve().parent.parent))
DATA_DIR = Path(os.getenv("LA_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.getenv("LA_DB_PATH", DATA_DIR / "live_analytics.db"))
SESSIONS_DIR = Path(os.getenv("LA_SESSIONS_DIR", DATA_DIR / "sessions"))

# ── Network ───────────────────────────────────────────────────────────
HTTP_HOST: str = os.getenv("LA_HTTP_HOST", "0.0.0.0")
HTTP_PORT: int = _int_env("LA_HTTP_PORT", 8080)

WS_INGEST_HOST: str = os.getenv("LA_WS_INGEST_HOST", "0.0.0.0")
WS_INGEST_PORT: int = _int_env("LA_WS_INGEST_PORT", 8766)

DASHBOARD_PORT: int = _int_env("LA_DASHBOARD_PORT", 8501)

# ── Scoring ───────────────────────────────────────────────────────────
SCORING_WINDOW_SEC: float = _float_env("LA_SCORING_WINDOW_SEC", 5.0)
HR_BASELINE_BPM: float = _float_env("LA_HR_BASELINE_BPM", 70.0)

# ── Logging ───────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LA_LOG_LEVEL", "INFO")


def ensure_dirs() -> None:
    """Create data directories if they don't exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
