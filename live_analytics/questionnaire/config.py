"""
Questionnaire service – configuration via environment variables.
"""

from __future__ import annotations

import os
from pathlib import Path

from live_analytics.app.env_utils import int_env


_DEFAULT_BASE = Path(__file__).resolve().parent

# Path defaults are chained so QS_BASE_DIR alone relocates data/db paths.
BASE_DIR = Path(os.getenv("QS_BASE_DIR", str(_DEFAULT_BASE)))
DATA_DIR = Path(os.getenv("QS_DATA_DIR", str(BASE_DIR / "data")))
DB_PATH = Path(os.getenv("QS_DB_PATH", str(DATA_DIR / "questionnaire.db")))

# Shared participants log directory — same location as the analytics service.
# Set LA_PARTICIPANTS_DIR to override (e.g. when running from a custom data dir).
_LA_DEFAULT_BASE = _DEFAULT_BASE.parent  # live_analytics/
PARTICIPANTS_DIR = Path(os.getenv("LA_PARTICIPANTS_DIR", str(_LA_DEFAULT_BASE / "data" / "participants")))

HOST: str = os.getenv("QS_HOST", "0.0.0.0")
PORT: int = int_env("QS_PORT", 8090)

LOG_LEVEL: str = os.getenv("QS_LOG_LEVEL", "INFO")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PARTICIPANTS_DIR.mkdir(parents=True, exist_ok=True)
    # Ensure the parent of the DB file exists even when QS_DB_PATH points to a
    # custom location whose directory tree has not been created yet.  Without
    # this, sqlite3.connect() raises OperationalError: unable to open database.
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

