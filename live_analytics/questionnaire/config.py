"""
Questionnaire service – configuration via environment variables.
"""

from __future__ import annotations

import os
from pathlib import Path

from live_analytics.app.env_utils import int_env


_DEFAULT_BASE = str(Path(__file__).resolve().parent)
_DEFAULT_DATA = str(Path(_DEFAULT_BASE) / "data")
_DEFAULT_DB   = str(Path(_DEFAULT_DATA) / "questionnaire.db")

BASE_DIR = Path(os.getenv("QS_BASE_DIR", _DEFAULT_BASE))
DATA_DIR = Path(os.getenv("QS_DATA_DIR", _DEFAULT_DATA))
DB_PATH  = Path(os.getenv("QS_DB_PATH",  _DEFAULT_DB))

HOST: str = os.getenv("QS_HOST", "0.0.0.0")
PORT: int = int_env("QS_PORT", 8090)

LOG_LEVEL: str = os.getenv("QS_LOG_LEVEL", "INFO")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

