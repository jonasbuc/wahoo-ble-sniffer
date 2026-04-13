"""
Live Analytics – FastAPI application entry point.

Starts both:
  • the FastAPI HTTP/WS server on LA_HTTP_PORT (default 8080)
  • the standalone websockets ingest server on LA_WS_INGEST_PORT (default 8765)
"""

from __future__ import annotations

import asyncio
import logging

import uvicorn
from fastapi import FastAPI

from live_analytics.app.api_sessions import router as sessions_router
from live_analytics.app.config import (
    DB_PATH,
    HTTP_HOST,
    HTTP_PORT,
    LOG_LEVEL,
    SESSIONS_DIR,
    ensure_dirs,
)
from live_analytics.app.storage.raw_writer import RawWriter
from live_analytics.app.storage.sqlite_store import init_db
from live_analytics.app.ws_dashboard import dashboard_ws
from live_analytics.app.ws_ingest import set_raw_writer, start_ingest_server

# ── Logging setup ─────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
)
logger = logging.getLogger("live_analytics")

# ── FastAPI app ───────────────────────────────────────────────────────
app = FastAPI(title="Live Analytics", version="0.1.0")
app.include_router(sessions_router)
app.add_api_websocket_route("/ws/dashboard", dashboard_ws)


@app.on_event("startup")
async def _startup() -> None:
    ensure_dirs()
    init_db(DB_PATH)
    set_raw_writer(RawWriter(SESSIONS_DIR))
    logger.info("FastAPI HTTP server ready on %s:%d", HTTP_HOST, HTTP_PORT)

    # Start the ingest WS server as a background task
    asyncio.create_task(start_ingest_server())


def main() -> None:
    """CLI entry point – ``python -m live_analytics.app.main``."""
    ensure_dirs()
    init_db(DB_PATH)
    set_raw_writer(RawWriter(SESSIONS_DIR))
    uvicorn.run(
        "live_analytics.app.main:app",
        host=HTTP_HOST,
        port=HTTP_PORT,
        log_level=LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()
