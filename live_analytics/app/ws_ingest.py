"""
WebSocket ingest endpoint – receives telemetry from Unity clients.

Runs on a dedicated port (default 8766) via a standalone websockets server
that is started alongside the FastAPI HTTP server.

Performance notes
-----------------
- All storage work (JSONL append, record-count increment, score persistence)
  is batched at the *message* level, not the record level.  A single Unity
  message may contain up to ``batch_size`` records (default 10).  Doing one
  DB write per batch instead of one per record reduces SQLite round-trips by
  ~10× at 20 Hz / batch_size=10.
- ``compute_scores`` is called once per batch per session (after the sliding
  window is updated for all records) rather than once per record.
- ``_record_counts`` is a separate counter dict.  We cannot rely on
  ``len(_windows[sid])`` because the deque has ``maxlen``; once it is full
  ``len()`` is always ``_WINDOW_MAX`` and ``len % 20 == 0`` would be True
  on every record, causing a DB write every record instead of every 20.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import Any

import websockets
from websockets import ConnectionClosed, ServerConnection

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

# Separate record counter per session (deque len is capped at _WINDOW_MAX,
# so we cannot use it to detect "every 20 records" reliably).
_record_counts: dict[str, int] = {}

# Latest scores per session – read by the dashboard WS endpoint
latest_scores: dict[str, ScoringResult] = {}
latest_records: dict[str, TelemetryRecord] = {}

# Set of dashboard WebSocket connections to broadcast to
dashboard_subscribers: set[Any] = set()

_raw_writer: RawWriter | None = None

# How often (in records) to persist scores to SQLite
_SCORE_PERSIST_EVERY = 20


def set_raw_writer(writer: RawWriter) -> None:
    global _raw_writer
    _raw_writer = writer


async def _handle_connection(ws: ServerConnection) -> None:
    peer = ws.remote_address
    logger.info("Unity client connected from %s", peer)
    try:
        async for message in ws:
            await _process_message(ws, message)
    except ConnectionClosed as exc:
        logger.info(
            "Unity client disconnected: %s  (code=%s reason=%r)",
            peer, exc.code, exc.reason,
        )
    except Exception:
        logger.exception("Unexpected error in ingest connection from %s – closing", peer)


async def _process_message(ws: ServerConnection, raw: str) -> None:
    """Parse, validate, store, score, and optionally send feedback."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Malformed JSON from Unity – skipping.")
        return

    try:
        batch = TelemetryBatch(**data)
    except Exception as exc:
        logger.warning("Payload validation failed – skipping batch: %s: %s",
                        type(exc).__name__, exc)
        return

    if not batch.records:
        return

    # ── Batch-level storage and scoring ──────────────────────────────

    # Group records by session (almost always one session per batch)
    by_session: dict[str, list[TelemetryRecord]] = {}
    for rec in batch.records:
        by_session.setdefault(rec.session_id, []).append(rec)

    for sid, records in by_session.items():
        try:
            _ingest_session_batch(sid, records)
        except Exception:
            logger.exception(
                "Failed to ingest batch for session %s (%d records) – batch dropped",
                sid, len(records),
            )

    # ── Send feedback to Unity (latest scores for first session) ─────
    session_id = batch.records[0].session_id
    if session_id in latest_scores:
        scores = latest_scores[session_id]
        feedback = LiveFeedback(
            stress_score=scores.stress_score,
            risk_score=scores.risk_score,
        )
        try:
            await ws.send(feedback.model_dump_json())
        except Exception as exc:
            logger.debug("Failed to send feedback to Unity for session %s: %s",
                         session_id, exc)

    # ── Broadcast to dashboard subscribers ──────────────────────────
    await _broadcast_dashboard(session_id)


def _ingest_session_batch(sid: str, records: list[TelemetryRecord]) -> None:
    """
    Process a batch of records for a single session.

    Storage operations are batched:
    - ``upsert_session``         – only on first encounter
    - ``raw_writer.append_many`` – once per batch (one file open/close)
    - ``increment_record_count`` – once per batch (one DB write)
    - ``compute_scores``         – once per batch (after window is fully updated)
    - ``update_latest_scores``   – every _SCORE_PERSIST_EVERY records
    """
    first_rec = records[0]

    # Initialise session on first encounter
    if sid not in _windows:
        db_ok = True
        try:
            upsert_session(DB_PATH, sid, first_rec.unix_ms, first_rec.scenario_id)
        except Exception:
            logger.exception(
                "DB error: could not upsert session %s in %s – "
                "session will still be scored and raw JSONL written, "
                "but record counts and scores will NOT be persisted to SQLite",
                sid, DB_PATH,
            )
            db_ok = False
        _windows[sid] = deque(maxlen=_WINDOW_MAX)
        _record_counts[sid] = 0
        logger.info(
            "New session started: %s (scenario=%r, db_registered=%s)",
            sid, first_rec.scenario_id, db_ok,
        )

    # Persist raw records – one file open/close for the whole batch
    if _raw_writer:
        _raw_writer.append_many(records)
    else:
        logger.warning(
            "raw_writer is not initialised – telemetry for session %s not persisted to JSONL "
            "(running in degraded mode; DB record count and scoring still active)",
            sid,
        )

    # Update sliding window for all records
    window = _windows[sid]
    for rec in records:
        window.append(rec)

    # Increment DB record count once for the batch
    n = len(records)
    old_count = _record_counts[sid]
    _record_counts[sid] = old_count + n
    try:
        increment_record_count(DB_PATH, sid, n)
    except Exception:
        logger.exception(
            "DB error: could not increment record_count for session %s in %s",
            sid, DB_PATH,
        )

    # Score once on the updated window
    try:
        scores = compute_scores(list(window))
    except Exception:
        logger.exception(
            "Scoring failed for session %s (window size=%d) – keeping previous scores",
            sid, len(window),
        )
        return
    latest_scores[sid] = scores
    latest_records[sid] = records[-1]

    # Persist scores snapshot every _SCORE_PERSIST_EVERY records.
    # Trigger when the counter crosses a multiple of _SCORE_PERSIST_EVERY.
    if old_count // _SCORE_PERSIST_EVERY != _record_counts[sid] // _SCORE_PERSIST_EVERY:
        try:
            update_latest_scores(DB_PATH, sid, scores)
        except Exception:
            logger.exception(
                "DB error: could not persist scores for session %s in %s",
                sid, DB_PATH,
            )


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
    # Snapshot the subscriber set before iterating.
    # Without a snapshot, an `await sub.send()` suspends this coroutine;
    # while it is suspended, `dashboard_ws` may add or remove a subscriber,
    # causing "RuntimeError: Set changed size during iteration" on the next
    # iteration step.
    for sub in list(dashboard_subscribers):
        try:
            await sub.send(payload)
        except Exception:
            dead.append(sub)
    for d in dead:
        dashboard_subscribers.discard(d)
        logger.info(
            "Dashboard subscriber removed after send failure (addr=%s)",
            getattr(d, "remote_address", "<unknown>"),
        )


async def start_ingest_server() -> None:
    """Start the standalone websockets ingest server."""
    logger.info("Starting ingest WS on %s:%d", WS_INGEST_HOST, WS_INGEST_PORT)
    try:
        async with websockets.serve(_handle_connection, WS_INGEST_HOST, WS_INGEST_PORT):
            logger.info(
                "Ingest WS server listening on ws://%s:%d – waiting for Unity connections",
                WS_INGEST_HOST, WS_INGEST_PORT,
            )
            await asyncio.Future()  # run forever
    except OSError as exc:
        logger.critical(
            "Ingest WS server failed to bind on %s:%d – %s: %s  "
            "(Is another process already on that port?)",
            WS_INGEST_HOST, WS_INGEST_PORT, type(exc).__name__, exc,
        )
        raise
    except Exception:
        logger.exception(
            "Ingest WS server crashed unexpectedly (was listening on %s:%d)",
            WS_INGEST_HOST, WS_INGEST_PORT,
        )
        raise
