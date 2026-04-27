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
# All dicts below are written by the ingest coroutine and read by the
# FastAPI HTTP thread (for /api/live/latest) and the dashboard WS handler.
# Because asyncio is single-threaded, updates from within a single coroutine
# are safe without locks.  The only risk is dict-mutation-during-iteration
# (handled in _broadcast_dashboard with a list() snapshot).

# Sliding window of the last _WINDOW_MAX records per session, used for scoring.
_windows: dict[str, deque[TelemetryRecord]] = {}
_WINDOW_MAX = 600  # ≈30 s at 20 Hz

# Separate record counter per session.
# We cannot use len(_windows[sid]) because the deque is capped at _WINDOW_MAX;
# once full, len() is always _WINDOW_MAX and "len % 20 == 0" would be True on
# every record, triggering a SQLite write every record instead of every 20.
_record_counts: dict[str, int] = {}

# Latest scores per session – read by the dashboard WS endpoint and /api/live/latest.
latest_scores: dict[str, ScoringResult] = {}
# Latest record per session – used to populate the live/latest REST response.
latest_records: dict[str, TelemetryRecord] = {}

# Set of active dashboard WebSocket connections to broadcast score updates to.
dashboard_subscribers: set[Any] = set()

# Injected by main.py lifespan; None in unit tests (degraded mode: no JSONL persistence).
_raw_writer: RawWriter | None = None

# How often (in records) to persist scores to SQLite.
# A score snapshot is written whenever the record count crosses a multiple of this value.
# Lower values increase DB write frequency; 20 matches the default Unity batch size.
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
        # websockets ≥ 13.1 deprecated ConnectionClosed.code / .reason in favour
        # of exc.rcvd.code / exc.rcvd.reason (rcvd is None when the connection
        # was lost without a Close frame, e.g. network drop).
        _rcvd = getattr(exc, "rcvd", None)
        _code   = _rcvd.code   if _rcvd is not None else None
        _reason = _rcvd.reason if _rcvd is not None else ""
        logger.info(
            "Unity client disconnected: %s  (code=%s reason=%r)",
            peer, _code, _reason,
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

    Storage operations are batched at the *message* level (not per record):
    - ``upsert_session``         – only on first encounter for a given *sid*
    - ``raw_writer.append_many`` – once per batch (one file open/close)
    - ``increment_record_count`` – once per batch (one SQLite write)
    - ``compute_scores``         – once per batch, after the window is updated
    - ``update_latest_scores``   – every _SCORE_PERSIST_EVERY records

    Degraded mode: if any of the SQLite calls raise, they are caught and logged
    and processing continues.  The session is still scored and the raw JSONL is
    still written.  If ``_raw_writer`` is None (e.g. in unit tests or on a
    failed startup), JSONL persistence is skipped but scoring still runs.
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

    # Always update the latest record pointer so the dashboard shows the
    # actual newest record even when scoring fails.
    latest_records[sid] = records[-1]

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
    """Push latest state to all subscribed dashboard WS clients.

    Dashboard clients are FastAPI/Starlette ``WebSocket`` objects.
    Starlette's ``WebSocket.send(msg)`` expects an ASGI message *dict*
    (``{"type": "websocket.send", "text": ...}``), NOT a plain string.
    The correct method for sending text is ``WebSocket.send_text(str)``.
    """
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
    # Without a snapshot, an `await sub.send_text()` suspends this coroutine;
    # while it is suspended, `dashboard_ws` may add or remove a subscriber,
    # causing "RuntimeError: Set changed size during iteration" on the next
    # iteration step.
    for sub in list(dashboard_subscribers):
        try:
            # FastAPI/Starlette WebSocket: use send_text() not send().
            # send() expects an ASGI dict, not a string – calling send(str)
            # raises TypeError and every subscriber would be silently evicted.
            await sub.send_text(payload)
        except Exception:
            dead.append(sub)
    for d in dead:
        dashboard_subscribers.discard(d)
        logger.info(
            "Dashboard subscriber removed after send failure (addr=%s)",
            getattr(d, "remote_address", getattr(d, "client", "<unknown>")),
        )


# ── Session-state eviction ────────────────────────────────────────────
# How long (seconds) a session must be idle (no new records) before its
# in-memory state is eligible for eviction.  The default matches a
# typical session length plus a generous grace period so that the dashboard
# can still retrieve scores shortly after the rider finishes.
_SESSION_EVICT_AFTER_SEC: float = 4 * 3600      # 4 hours
_SESSION_EVICT_CHECK_INTERVAL_SEC: float = 3600  # check once per hour


async def _evict_stale_sessions() -> None:
    """Periodically remove in-memory state for sessions that have been idle
    for longer than *_SESSION_EVICT_AFTER_SEC*.

    This prevents ``_windows``, ``_record_counts``, ``latest_scores``, and
    ``latest_records`` from growing without bound during very long server
    uptimes that process hundreds of sessions.

    A session is considered idle when its ``latest_records`` entry has a
    ``unix_ms`` older than the eviction threshold.  Active sessions (e.g. a
    rider currently riding) are never evicted because their ``unix_ms``
    timestamp is always recent.

    The eviction loop runs as a background asyncio task started by
    ``main.py`` alongside the ingest server.  It is intentionally
    low-frequency (once per hour) to minimise overhead.
    """
    while True:
        await asyncio.sleep(_SESSION_EVICT_CHECK_INTERVAL_SEC)
        cutoff_ms = (asyncio.get_running_loop().time() - _SESSION_EVICT_AFTER_SEC) * 1000
        # Convert to wall-clock ms.  asyncio loop time is monotonic and may
        # not be Unix epoch; use time.time() instead for the comparison.
        import time as _time
        cutoff_unix_ms = int((_time.time() - _SESSION_EVICT_AFTER_SEC) * 1000)

        stale = [
            sid
            for sid, rec in list(latest_records.items())
            if rec.unix_ms < cutoff_unix_ms
        ]
        if not stale:
            return

        for sid in stale:
            _windows.pop(sid, None)
            _record_counts.pop(sid, None)
            latest_scores.pop(sid, None)
            latest_records.pop(sid, None)

        logger.info(
            "Evicted in-memory state for %d stale session(s) "
            "(idle > %.0f hours): %s",
            len(stale),
            _SESSION_EVICT_AFTER_SEC / 3600,
            stale[:5],  # log first 5 ids to avoid flooding
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
