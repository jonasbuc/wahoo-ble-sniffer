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
    await ws.accept()
    logger.info("Dashboard client connected.")
    dashboard_subscribers.add(ws)
    try:
        # Keep the connection alive – the send loop is driven by the ingest side
        while True:
            # Just wait for close / ping
            await ws.receive_text()
    except WebSocketDisconnect:
        logger.info("Dashboard client disconnected.")
    finally:
        dashboard_subscribers.discard(ws)
