"""
Live Analytics – FastAPI application entry point.

Starts both:
  • the FastAPI HTTP/WS server on LA_HTTP_PORT (default 8080)
  • the standalone websockets ingest server on LA_WS_INGEST_PORT (default 8766)
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi import Request

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
from live_analytics.app.ws_ingest import set_raw_writer, start_ingest_server, _evict_stale_sessions

# ── Logging setup ─────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
)
logger = logging.getLogger("live_analytics")


# ── Lifespan ──────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """Startup / shutdown logic for the FastAPI application."""
    logger.info("── Startup ────────────────────────────────────────")
    logger.info("  DB_PATH     = %s", DB_PATH)
    logger.info("  SESSIONS_DIR= %s", SESSIONS_DIR)
    logger.info("  HTTP        = %s:%d", HTTP_HOST, HTTP_PORT)
    logger.info("  LOG_LEVEL   = %s", LOG_LEVEL)

    try:
        ensure_dirs()
        logger.info("  Data directories OK")
    except Exception as exc:
        logger.critical(
            "Startup failed: could not create data directories: %s – "
            "check that the process has write permission to %s",
            exc, SESSIONS_DIR,
        )
        raise

    try:
        init_db(DB_PATH)
    except Exception as exc:
        logger.critical(
            "Startup failed: could not initialise SQLite database at '%s': %s",
            DB_PATH, exc,
        )
        raise

    try:
        set_raw_writer(RawWriter(SESSIONS_DIR))
    except Exception as exc:
        logger.critical(
            "Startup failed: could not create RawWriter for sessions dir '%s': %s",
            SESSIONS_DIR, exc,
        )
        raise

    logger.info("  Storage initialised")

    # Start the ingest WS server as a background task
    task = asyncio.create_task(start_ingest_server())
    task.add_done_callback(_ingest_task_done)

    # Periodically evict in-memory state for sessions idle > 4 h
    evict_task = asyncio.create_task(_evict_stale_sessions())
    evict_task.add_done_callback(_evict_task_done)

    logger.info("── Startup complete ───────────────────────────────")
    yield
    logger.info("── Shutdown ───────────────────────────────────────")


def _evict_task_done(task: asyncio.Task) -> None:
    """Log if the session-eviction background task exits unexpectedly."""
    if task.cancelled():
        logger.warning("Session-eviction task was cancelled – stale in-memory state will no longer be pruned.")
    elif (exc := task.exception()) is not None:
        logger.error(
            "Session-eviction task crashed: %s: %s",
            type(exc).__name__, exc,
            exc_info=exc,
        )


def _ingest_task_done(task: asyncio.Task) -> None:
    """Log if the ingest server exits or crashes unexpectedly."""
    if task.cancelled():
        logger.warning(
            "Ingest WS server task was cancelled – Unity clients will no longer be able to connect."
        )
    elif (exc := task.exception()) is not None:
        logger.critical(
            "Ingest WS server crashed and will NOT restart: %s: %s  "
            "(Unity telemetry ingest is now offline – restart the service to recover)",
            type(exc).__name__, exc,
            exc_info=exc,
        )
    else:
        logger.info("Ingest WS server exited cleanly.")


# ── FastAPI app ───────────────────────────────────────────────────────
app = FastAPI(title="Live Analytics", version="0.1.0", lifespan=lifespan)
app.include_router(sessions_router)
app.add_api_websocket_route("/ws/dashboard", dashboard_ws)


@app.middleware("http")
async def _log_unhandled_request_exceptions(request: Request, call_next):
    """Middleware that logs any unhandled exception during HTTP request
    processing with request context so first-request crashes are actionable.

    This is intentionally generous with captured context (method, url, a
    short preview of the body) because on a clean Windows install the
    first failing request often happens before normal diagnostics are
    configured. The middleware keeps the original exception semantics by
    re-raising after logging.
    """
    try:
        return await call_next(request)
    except Exception as exc:  # capture anything unexpected
        try:
            # Read a tiny preview of the body without consuming too much
            # memory. Request.stream() can only be read once so use .body()
            # which FastAPI/Starlette buffers for us in most deployment
            # setups. Guard with try/except in case the body is large or
            # not available on the first request path.
            body = await request.body()
            body_preview = body[:2048].decode('utf-8', errors='replace')
        except Exception:
            body_preview = '<unavailable>'
        logger.exception(
            "Unhandled exception while serving HTTP %s %s – body_preview=%r",
            request.method, request.url, body_preview,
            exc_info=exc,
        )
        raise


def main() -> None:
    """CLI entry point – ``python -m live_analytics.app.main``."""
    # Note: ensure_dirs/init_db/set_raw_writer are handled by the lifespan
    # context manager when uvicorn imports the app object.
    uvicorn.run(
        "live_analytics.app.main:app",
        host=HTTP_HOST,
        port=HTTP_PORT,
        log_level=LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()
