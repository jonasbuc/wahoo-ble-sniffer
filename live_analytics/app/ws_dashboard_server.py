"""
ws_dashboard_server.py — standalone analytics-ws pod
=====================================================
Runs as a separate Kubernetes pod (port 8768, separate from analytics-api's
:8080 and analytics-ingest's :8766).

Provides:
  GET  /healthz          Kubernetes liveness / readiness probe
  WS   /ws/dashboard     Live session state pushed to browser clients

Architecture
------------
This module has **no shared in-process state** with ws_ingest.  It reads the
``live_state`` SQLite table that ws-ingest writes after every scored batch.
SQLite WAL mode allows this pod to read concurrently without blocking the
writer, even when both pods mount the same PVC.

In single-process local dev mode this module is NOT used — ``ws_dashboard.py``
is mounted on the analytics-api FastAPI app instead, which also runs the ingest
loop in-process.

Environment variables
---------------------
  LA_DB_PATH          Path to the shared SQLite database  (required)
  WS_DASHBOARD_HOST   Host/interface to bind on           (default: 0.0.0.0)
  WS_DASHBOARD_PORT   Port for this server                (default: 8768)
  POLL_INTERVAL       Seconds between DB reads per client (default: 0.5)
  LA_LOG_LEVEL        Logging level                       (default: INFO)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from live_analytics.app.config import DB_PATH, LOG_LEVEL
from live_analytics.app.storage.sqlite_store import get_live_latest

logger = logging.getLogger("live_analytics.ws_dashboard_server")

_HOST = os.environ.get("WS_DASHBOARD_HOST", "0.0.0.0")
_PORT = int(os.environ.get("WS_DASHBOARD_PORT", "8768"))
_POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "0.5"))

# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="CarVR analytics-ws",
    description="Dashboard WebSocket broadcast pod — reads live_state from SQLite",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
)


@app.get("/healthz")
async def healthz() -> dict:
    """Kubernetes liveness / readiness probe.

    Returns 200 as long as the event loop is running and the DB path is
    configured.  Does not perform a live DB query so the probe never blocks
    even when the DB file is momentarily locked.
    """
    return {
        "status": "ok",
        "service": "analytics-ws",
        "db": str(DB_PATH),
        "poll_interval_s": _POLL_INTERVAL,
    }


# ── WebSocket endpoint ────────────────────────────────────────────────────────

async def _poll_and_send(ws: WebSocket, addr: str) -> None:
    """Background task: poll live_state and push JSON to one dashboard client.

    Each connected client owns its own polling loop so the server scales to
    multiple simultaneous dashboard windows without broadcasting overhead.
    When the WebSocket closes the task exits naturally.
    """
    while True:
        try:
            row = await asyncio.to_thread(get_live_latest, DB_PATH)
            if row is not None:
                await ws.send_text(json.dumps(row))
        except Exception:
            logger.debug(
                "_poll_and_send: error for %s — stopping poll loop", addr,
                exc_info=True,
            )
            return
        await asyncio.sleep(_POLL_INTERVAL)


@app.websocket("/ws/dashboard")
async def ws_dashboard(ws: WebSocket) -> None:
    """WebSocket endpoint for Streamlit / browser dashboard clients.

    Pushes live session state as JSON every ``POLL_INTERVAL`` seconds.
    The payload is identical to the ``live_state`` table row:
      { session_id, unix_ms, heart_rate, speed, scores_json, updated_at_ms }
    """
    client = ws.client
    addr = f"{client.host}:{client.port}" if client else "<unknown>"
    await ws.accept()
    logger.info("Dashboard WS client connected from %s", addr)

    poll_task = asyncio.create_task(_poll_and_send(ws, addr))
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                code = msg.get("code", 1000)
                reason = msg.get("reason", "")
                raise WebSocketDisconnect(code=code, reason=reason)
    except WebSocketDisconnect as exc:
        logger.info(
            "Dashboard WS client disconnected from %s (code=%s reason=%r)",
            addr, exc.code, exc.reason,
        )
    except Exception:
        logger.exception(
            "Unexpected error on dashboard WS connection from %s — closing", addr,
        )
    finally:
        poll_task.cancel()
        logger.debug("Dashboard poll task cancelled for %s", addr)


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main() -> None:
    """CLI entry point — ``python -m live_analytics.app.ws_dashboard_server``."""
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    )
    logger.info("── analytics-ws startup ────────────────────────────")
    logger.info("  WS dashboard = ws://%s:%d/ws/dashboard", _HOST, _PORT)
    logger.info("  healthz      = http://%s:%d/healthz", _HOST, _PORT)
    logger.info("  DB_PATH      = %s", DB_PATH)
    logger.info("  poll_interval= %.1f s", _POLL_INTERVAL)
    uvicorn.run(app, host=_HOST, port=_PORT, log_level=LOG_LEVEL.lower())


if __name__ == "__main__":
    main()
