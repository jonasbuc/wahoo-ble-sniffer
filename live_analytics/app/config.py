"""
Live Analytics – configuration via environment variables with sensible defaults.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from live_analytics.app.env_utils import float_env, int_env

_log = logging.getLogger("live_analytics.config")


# ── Paths ─────────────────────────────────────────────────────────────
_DEFAULT_BASE_DIR = str(Path(__file__).resolve().parent.parent)
_DEFAULT_DATA_DIR = str(Path(_DEFAULT_BASE_DIR) / "data")
_DEFAULT_DB_PATH  = str(Path(_DEFAULT_DATA_DIR) / "live_analytics.db")
_DEFAULT_SESS_DIR = str(Path(_DEFAULT_DATA_DIR) / "sessions")

BASE_DIR    = Path(os.getenv("LA_BASE_DIR",    _DEFAULT_BASE_DIR))
DATA_DIR    = Path(os.getenv("LA_DATA_DIR",    _DEFAULT_DATA_DIR))
DB_PATH     = Path(os.getenv("LA_DB_PATH",     _DEFAULT_DB_PATH))
SESSIONS_DIR = Path(os.getenv("LA_SESSIONS_DIR", _DEFAULT_SESS_DIR))

# ── Network ───────────────────────────────────────────────────────────
HTTP_HOST: str = os.getenv("LA_HTTP_HOST", "0.0.0.0")
HTTP_PORT: int = int_env("LA_HTTP_PORT", 8080)

WS_INGEST_HOST: str = os.getenv("LA_WS_INGEST_HOST", "0.0.0.0")
WS_INGEST_PORT: int = int_env("LA_WS_INGEST_PORT", 8766)

DASHBOARD_PORT: int = int_env("LA_DASHBOARD_PORT", 8501)

# ── Scoring ───────────────────────────────────────────────────────────
SCORING_WINDOW_SEC: float = float_env("LA_SCORING_WINDOW_SEC", 5.0)
HR_BASELINE_BPM: float = float_env("LA_HR_BASELINE_BPM", 70.0)

# ── Logging ───────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LA_LOG_LEVEL", "INFO")


def ensure_dirs() -> None:
    """Create data directories if they don't exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

