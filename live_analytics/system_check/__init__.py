"""
System Check GUI – configuration via environment variables.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(os.getenv("SC_BASE_DIR", Path(__file__).resolve().parent))
DATA_DIR = Path(os.getenv("SC_DATA_DIR", BASE_DIR / "data"))

HOST: str = os.getenv("SC_HOST", "0.0.0.0")
PORT: int = int(os.getenv("SC_PORT", "8095"))

LOG_LEVEL: str = os.getenv("SC_LOG_LEVEL", "INFO")

# ── Paths to check ───────────────────────────────────────────────────
# VRS log base path (Unity writes sessions here)
VRS_LOG_BASE: Path = Path(os.getenv("SC_VRS_LOG_BASE", Path(__file__).resolve().parent.parent.parent / "Logs"))

# Analytics DB
ANALYTICS_DB: Path = Path(os.getenv("SC_ANALYTICS_DB",
    Path(__file__).resolve().parent.parent / "app" / "data" / "live_analytics.db"))

# Questionnaire DB
QUESTIONNAIRE_DB: Path = Path(os.getenv("SC_QUESTIONNAIRE_DB",
    Path(__file__).resolve().parent.parent / "questionnaire" / "data" / "questionnaire.db"))

# Bridge WebSocket
BRIDGE_WS_URL: str = os.getenv("SC_BRIDGE_WS_URL", "ws://localhost:8765")

# Analytics API
ANALYTICS_API_URL: str = os.getenv("SC_ANALYTICS_API_URL", "http://localhost:8080")

# Questionnaire API
QUESTIONNAIRE_API_URL: str = os.getenv("SC_QUESTIONNAIRE_API_URL", "http://localhost:8090")

# Expected VRSF files in a session directory
EXPECTED_VRSF_FILES: list[str] = ["headpose.vrsf", "bike.vrsf", "hr.vrsf", "events.vrsf", "manifest.json"]


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
