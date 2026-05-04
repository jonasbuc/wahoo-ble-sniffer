"""
Live Analytics – configuration via environment variables with sensible defaults.

All settings can be overridden at runtime via environment variables prefixed
with ``LA_``.  No config file is required — the defaults are designed to work
out of the box on a single developer machine.

Typical overrides when running multiple instances or with a custom data directory:
    LA_DB_PATH=/data/myrun.db
    LA_SESSIONS_DIR=/data/sessions
    LA_HTTP_PORT=8081
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from live_analytics.app.env_utils import float_env, int_env

_log = logging.getLogger("live_analytics.config")


# ── Paths ─────────────────────────────────────────────────────────────
_DEFAULT_BASE_DIR = Path(__file__).resolve().parent.parent

# Path defaults are intentionally *chained*:
#   BASE_DIR -> DATA_DIR -> DB_PATH / SESSIONS_DIR
# so overriding LA_BASE_DIR alone relocates all runtime files unless a more
# specific LA_* override is provided.
BASE_DIR = Path(os.getenv("LA_BASE_DIR", str(_DEFAULT_BASE_DIR)))
DATA_DIR = Path(os.getenv("LA_DATA_DIR", str(BASE_DIR / "data")))
DB_PATH = Path(os.getenv("LA_DB_PATH", str(DATA_DIR / "live_analytics.db")))
SESSIONS_DIR = Path(os.getenv("LA_SESSIONS_DIR", str(DATA_DIR / "sessions")))
PARTICIPANTS_DIR = Path(os.getenv("LA_PARTICIPANTS_DIR", str(DATA_DIR / "participants")))

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
    """Create the data and sessions directories if they do not already exist.

    Safe to call multiple times (uses ``exist_ok=True``).  Called during
    server startup and by backfill scripts.  Must NOT be called in dry-run
    contexts where filesystem side effects are undesirable.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    PARTICIPANTS_DIR.mkdir(parents=True, exist_ok=True)
    # Support custom LA_DB_PATH outside DATA_DIR.
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

