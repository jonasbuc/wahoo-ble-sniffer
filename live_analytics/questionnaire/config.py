"""
Questionnaire service – configuration via environment variables.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(os.getenv("QS_BASE_DIR", Path(__file__).resolve().parent))
DATA_DIR = Path(os.getenv("QS_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.getenv("QS_DB_PATH", DATA_DIR / "questionnaire.db"))

HOST: str = os.getenv("QS_HOST", "0.0.0.0")
PORT: int = int(os.getenv("QS_PORT", "8090"))

LOG_LEVEL: str = os.getenv("QS_LOG_LEVEL", "INFO")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
