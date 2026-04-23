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
        # Keep the connection alive – the send loop is driven by the ingest side.
        # We don't expect clients to send us anything; absorb any incoming messages
        # (text or binary) without crashing.  Binary frames from browsers (e.g.
        # WebSocket ping payloads from some clients) would raise if we called
        # receive_text() — use receive() instead and discard the payload.
        while True:
            msg = await ws.receive()
            # A disconnect message has type "websocket.disconnect"; re-raise
            # so the except WebSocketDisconnect handler fires cleanly.
            if msg.get("type") == "websocket.disconnect":
                code = msg.get("code", 1000)
                reason = msg.get("reason", "")
                from starlette.websockets import WebSocketDisconnect
                raise WebSocketDisconnect(code=code, reason=reason)
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
