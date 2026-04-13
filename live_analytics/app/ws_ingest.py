"""
WebSocket ingest endpoint – receives telemetry from Unity clients.

Runs on a dedicated port (default 8765) via a standalone websockets server
that is started alongside the FastAPI HTTP server.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import Any

import websockets
from websockets.server import WebSocketServerProtocol

from live_analytics.app.config import DB_PATH, WS_INGEST_HOST, WS_INGEST_PORT
from live_analytics.app.models import (
    LiveFeedback,
    ScoringResult,
    TelemetryBatch,
    TelemetryRecord,
)
from live_analytics.app.scoring.rules import compute_scores
from live_analytics.app.storage.raw_writer import RawWriter
from live_analytics.app.storage.sqlite_store import (
    increment_record_count,
    update_latest_scores,
    upsert_session,
)

logger = logging.getLogger("live_analytics.ws_ingest")

# ── Shared state (module-level) ──────────────────────────────────────
# Sliding window per session for scoring
_windows: dict[str, deque[TelemetryRecord]] = {}
_WINDOW_MAX = 600  # ≈30 s at 20 Hz

# Latest scores per session – read by the dashboard WS endpoint
latest_scores: dict[str, ScoringResult] = {}
latest_records: dict[str, TelemetryRecord] = {}

# Set of dashboard WebSocket connections to broadcast to
dashboard_subscribers: set[Any] = set()

_raw_writer: RawWriter | None = None


def set_raw_writer(writer: RawWriter) -> None:
    global _raw_writer
    _raw_writer = writer


async def _handle_connection(ws: WebSocketServerProtocol) -> None:
    peer = ws.remote_address
    logger.info("Unity client connected from %s", peer)
    try:
        async for message in ws:
            await _process_message(ws, message)
    except websockets.exceptions.ConnectionClosed:
        logger.info("Unity client disconnected: %s", peer)
    except Exception:
        logger.exception("Error in ingest connection from %s", peer)


async def _process_message(ws: WebSocketServerProtocol, raw: str) -> None:
    """Parse, validate, store, score, and optionally send feedback."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Malformed JSON from Unity – skipping.")
        return

    try:
        batch = TelemetryBatch(**data)
    except Exception:
        logger.warning("Payload validation failed – skipping batch.")
        return

    for rec in batch.records:
        _ingest_record(rec)

    # Send feedback to Unity (latest scores)
    session_id = batch.records[0].session_id if batch.records else None
    if session_id and session_id in latest_scores:
        scores = latest_scores[session_id]
        feedback = LiveFeedback(
            stress_score=scores.stress_score,
            risk_score=scores.risk_score,
        )
        try:
            await ws.send(feedback.model_dump_json())
        except Exception:
            pass

    # Broadcast to dashboard subscribers
    await _broadcast_dashboard(session_id)


def _ingest_record(rec: TelemetryRecord) -> None:
    """Process a single telemetry record: store, window, score."""
    sid = rec.session_id

    # Ensure session exists in DB
    if sid not in _windows:
        upsert_session(DB_PATH, sid, rec.unix_ms, rec.scenario_id)
        _windows[sid] = deque(maxlen=_WINDOW_MAX)

    # Persist raw
    if _raw_writer:
        _raw_writer.append(rec)

    # Add to sliding window
    _windows[sid].append(rec)
    increment_record_count(DB_PATH, sid, 1)

    # Re-score
    window = list(_windows[sid])
    scores = compute_scores(window)
    latest_scores[sid] = scores
    latest_records[sid] = rec

    # Persist scores snapshot periodically (every 20 records)
    if len(_windows[sid]) % 20 == 0:
        update_latest_scores(DB_PATH, sid, scores)


async def _broadcast_dashboard(session_id: str | None) -> None:
    """Push latest state to all subscribed dashboard WS clients."""
    if not session_id or not dashboard_subscribers:
        return
    scores = latest_scores.get(session_id)
    rec = latest_records.get(session_id)
    if not scores or not rec:
        return
    payload = json.dumps({
        "session_id": session_id,
        "unix_ms": rec.unix_ms,
        "speed": rec.speed,
        "heart_rate": rec.heart_rate,
        "scores": scores.model_dump(),
    })
    dead: list[Any] = []
    for sub in dashboard_subscribers:
        try:
            await sub.send(payload)
        except Exception:
            dead.append(sub)
    for d in dead:
        dashboard_subscribers.discard(d)


async def start_ingest_server() -> None:
    """Start the standalone websockets ingest server."""
    logger.info("Starting ingest WS on %s:%d", WS_INGEST_HOST, WS_INGEST_PORT)
    async with websockets.serve(_handle_connection, WS_INGEST_HOST, WS_INGEST_PORT):
        await asyncio.Future()  # run forever
