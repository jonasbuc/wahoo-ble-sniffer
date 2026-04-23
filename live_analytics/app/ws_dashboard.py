"""
WebSocket endpoint for dashboard clients.

Dashboard clients connect to ``/ws/dashboard`` on the FastAPI HTTP server
and receive live score updates whenever the ingest pipeline processes new
telemetry.
"""

from __future__ import annotations

import logging

from fastapi import WebSocket, WebSocketDisconnect

from live_analytics.app.ws_ingest import dashboard_subscribers

logger = logging.getLogger("live_analytics.ws_dashboard")


async def dashboard_ws(ws: WebSocket) -> None:
    """FastAPI WebSocket handler mounted at ``/ws/dashboard``."""
    client = ws.client
    addr = f"{client.host}:{client.port}" if client else "<unknown>"
    await ws.accept()
    logger.info("Dashboard WebSocket client connected from %s", addr)
    dashboard_subscribers.add(ws)
    try:
        # Keep the connection alive – the send loop is driven by the ingest side
        while True:
            # Just wait for close / ping
            await ws.receive_text()
    except WebSocketDisconnect as exc:
        logger.info(
            "Dashboard WebSocket client disconnected from %s (code=%s reason=%r)",
            addr, exc.code, exc.reason,
        )
    except Exception:
        logger.exception(
            "Unexpected error on dashboard WebSocket connection from %s – closing",
            addr,
        )
    finally:
        dashboard_subscribers.discard(ws)
        logger.debug("Dashboard subscriber removed: %s (active=%d)", addr, len(dashboard_subscribers))
