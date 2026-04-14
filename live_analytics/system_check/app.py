"""
System Check GUI – FastAPI application.

Hosts:
  • REST API for running system checks
  • Static SPA frontend served from ./static/
  • Runs on SC_PORT (default 8095)
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from live_analytics.system_check import (
    HOST, PORT, LOG_LEVEL, ANALYTICS_DB, QUESTIONNAIRE_DB,
    BRIDGE_WS_URL, ANALYTICS_API_URL, QUESTIONNAIRE_API_URL,
    VRS_LOG_BASE, EXPECTED_VRSF_FILES, ensure_dirs,
)
from live_analytics.system_check.checks import (
    check_quest_headset,
    check_database,
    check_bridge_connection,
    check_vrsf_logs,
    check_service_http,
    run_all_checks,
)

# ── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
)
logger = logging.getLogger("system_check")

# ── Lifespan ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    logger.info("System Check GUI ready on %s:%d", HOST, PORT)
    yield

# ── App ───────────────────────────────────────────────────────────────
app = FastAPI(title="System Check GUI", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATIC_DIR = Path(__file__).resolve().parent / "static"


# ── Serve SPA ─────────────────────────────────────────────────────────

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Individual check endpoints ────────────────────────────────────────

@app.get("/api/check/headset")
async def api_check_headset() -> dict:
    return check_quest_headset()


@app.get("/api/check/analytics-db")
async def api_check_analytics_db() -> dict:
    return check_database(ANALYTICS_DB, "Live Analytics")


@app.get("/api/check/questionnaire-db")
async def api_check_questionnaire_db() -> dict:
    return check_database(QUESTIONNAIRE_DB, "Spørgeskema")


@app.get("/api/check/bridge")
async def api_check_bridge() -> dict:
    return check_bridge_connection(BRIDGE_WS_URL)


@app.get("/api/check/analytics-api")
async def api_check_analytics_api() -> dict:
    return check_service_http(ANALYTICS_API_URL, "Analytics API")


@app.get("/api/check/questionnaire-api")
async def api_check_questionnaire_api() -> dict:
    return check_service_http(QUESTIONNAIRE_API_URL, "Spørgeskema API")


@app.get("/api/check/vrsf-logs")
async def api_check_vrsf_logs() -> dict:
    return check_vrsf_logs(VRS_LOG_BASE, EXPECTED_VRSF_FILES)


# ── Run all checks at once ────────────────────────────────────────────

@app.get("/api/check/all")
async def api_check_all() -> dict:
    return run_all_checks()


# ── Health ────────────────────────────────────────────────────────────

@app.get("/api/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


# ── Entry point ───────────────────────────────────────────────────────

def main() -> None:
    ensure_dirs()
    uvicorn.run(
        "live_analytics.system_check.app:app",
        host=HOST, port=PORT,
        log_level=LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()
