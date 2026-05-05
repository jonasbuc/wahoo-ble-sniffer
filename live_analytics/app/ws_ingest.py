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
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

import websockets
from websockets import ConnectionClosed, ServerConnection

from live_analytics.app.config import DB_PATH, PARTICIPANTS_DIR, WS_INGEST_HOST, WS_INGEST_PORT
from live_analytics.app.models import (
    LiveFeedback,
    ScoringResult,
    TelemetryBatch,
    TelemetryRecord,
)
from live_analytics.app.scoring.rules import compute_scores
from live_analytics.app.storage.raw_writer import RawWriter
from live_analytics.app.storage import web_api_client
from live_analytics.app.storage.participant_logs import append_session_event as _append_session_event
from live_analytics.app.storage.sqlite_store import (
    end_session,
    increment_record_count,
    set_session_participant,
    update_latest_scores,
    upsert_session,
)
from live_analytics.app.utils.time_utils import fmt_iso as _fmt_iso

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
    # Sessions seen on *this* connection — used to write session_end on disconnect.
    # We track ALL session_ids mentioned in any message so that both new sessions
    # AND reconnections to existing session_ids get a session_end written.
    connection_sessions: set[str] = set()
    try:
        async for message in ws:
            await _process_message(ws, message, connection_sessions)
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
    finally:
        await _on_disconnect(connection_sessions)


async def _on_disconnect(session_ids: set[str]) -> None:
    """Write session_end for all sessions that belonged to the disconnected client.

    Also updates the SQLite ``end_unix_ms`` column so the HTTP API and
    dashboard can tell when sessions ended.
    """
    if not session_ids:
        return
    # Capture both UTC and local time once for all sessions in this disconnect.
    _now = datetime.now().astimezone()
    ended_at = _now.astimezone(timezone.utc).isoformat()
    local_time = _now.strftime("%Y-%m-%d %H:%M:%S %Z")
    end_unix_ms = int(_now.timestamp() * 1000)

    for sid in session_ids:
        # Guard: if no records were ever received for this session there is
        # nothing meaningful to record as a session_end — and there is no
        # point trying to resolve a participant for it either.
        last_rec = latest_records.get(sid)
        if last_rec is None:
            logger.debug(
                "_on_disconnect: no latest record for session %r "
                "(evicted before disconnect?) — session_end not written",
                sid,
            )
            continue

        pid = web_api_client._participant_cache.get(sid)
        if not pid:
            # Participant not yet resolved — try one last lookup before giving up.
            pid = await web_api_client.resolve_participant(sid)

        if not pid:
            logger.warning(
                "_on_disconnect: no participant linked to session %r "
                "— session_end not written (was a participant registered before the session started?)",
                sid,
            )
            # Still update SQLite end_unix_ms so the session isn't left open.
            try:
                end_session(DB_PATH, sid, end_unix_ms)
            except Exception:
                logger.exception(
                    "_on_disconnect: could not set end_unix_ms for session %r in SQLite",
                    sid,
                )
            continue

        # ── Update SQLite end_unix_ms ─────────────────────────────────
        try:
            end_session(DB_PATH, sid, end_unix_ms)
        except Exception:
            logger.exception(
                "_on_disconnect: could not set end_unix_ms for session %r in SQLite",
                sid,
            )

        # ── Write session_end to participant JSONL ────────────────────
        _append_session_event(PARTICIPANTS_DIR, pid, {
            "event": "session_end",
            "session_id": sid,
            "participant_id": pid,
            "ended_at": ended_at,
            "local_time": local_time,
            "record_count": _record_counts.get(sid, 0),
        })
        logger.info(
            "session_end written — session=%r participant=%r records=%d "
            "ended_at=%s SQLite.end_unix_ms updated",
            sid, pid, _record_counts.get(sid, 0), local_time,
        )


async def _process_message(ws: ServerConnection, raw: str, connection_sessions: set[str] | None = None) -> None:
    """Parse, validate, store, score, and optionally send feedback.

    *connection_sessions* is mutated in-place to record every session_id that
    appears in this message.  Callers use this set to write session_end events
    on disconnect.  The parameter is optional so unit tests that call
    _process_message directly don't need to pass it.
    """
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
        # Track every session_id seen on this connection regardless of whether
        # it is new or a continuation — covers reconnect scenarios.
        if connection_sessions is not None:
            connection_sessions.add(sid)
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


async def _resolve_and_link_participant(sid: str, scenario_id: str, started_at: str) -> None:
    """Fetch the questionnaire participant for *sid* and store it in the analytics DB.

    Called once per new session.  Failures are logged but never propagated so
    the ingest pipeline is not affected.
    Also writes a session-start event to the participant's session.jsonl log.
    """
    pid = await web_api_client.resolve_participant(sid)
    if pid:
        try:
            set_session_participant(DB_PATH, sid, pid)
        except Exception:
            logger.exception(
                "_resolve_and_link_participant: could not store participant %r "
                "for session %r in analytics DB",
                pid, sid,
            )
        # Write session-start event to participant's local log file.
        # Derive local_time from the same instant as started_at (the first
        # telemetry record's timestamp) so both fields always describe the
        # same point in time regardless of how long the HTTP lookup took.
        _append_session_event(PARTICIPANTS_DIR, pid, {
            "event": "session_start",
            "session_id": sid,
            "scenario_id": scenario_id,
            "participant_id": pid,
            "started_at": started_at,
            "local_time": _fmt_iso(started_at),
        })
        logger.info(
            "_resolve_and_link_participant: session_start written for session %r "
            "(participant=%r, scenario=%r)",
            sid, pid, scenario_id,
        )
    else:
        logger.warning(
            "_resolve_and_link_participant: no participant found for session %r "
            "(scenario=%r) — session not linked and session_start not written to JSONL. "
            "Register the participant in the questionnaire before starting a session "
            "so that pulse and session logs are correctly attributed.",
            sid, scenario_id,
        )


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
        # Resolve and cache the participant for this session asynchronously.
        # This links the questionnaire participant_id to all log entries for
        # this session (pulse, scores, etc.) without blocking the ingest pipeline.
        try:
            loop = asyncio.get_running_loop()
            _started_at = datetime.fromtimestamp(
                first_rec.unix_ms / 1000, tz=timezone.utc
            ).isoformat()
            loop.create_task(_resolve_and_link_participant(
                sid, first_rec.scenario_id or "", _started_at
            ))
        except RuntimeError:
            pass  # No running event loop (e.g. synchronous unit tests)

    # Persist raw records – one file open/close for the whole batch
    if _raw_writer:
        _raw_writer.append_many(records)
    elif sid not in _record_counts or _record_counts[sid] == 0:
        # Only emit this warning once per session (when record_count is still 0,
        # i.e. this is the very first batch). On subsequent batches it would spam
        # the log at 20 Hz with the same message.
        logger.debug(
            "raw_writer not initialised – JSONL persistence disabled for session %s "
            "(running in degraded/test mode; scoring and DB record counts still active)",
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

    # Persist heart-rate sample once per batch.
    # Pick the last record in the batch that carries a valid (>0) HR reading.
    # Writing once per batch (rather than once per record) keeps SQLite write
    # amplification low — at 20 Hz / batch_size=10 this is ≈2 writes/sec.
    _hr_rec = next(
        (r for r in reversed(records) if r.heart_rate > 0),
        None,
    )
    if _hr_rec is not None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                web_api_client.send_pulse(sid, _hr_rec.unix_ms, int(_hr_rec.heart_rate))
            )
        except RuntimeError:
            # No running event loop (e.g. during unit tests called synchronously).
            # Fire-and-forget via a new loop so the call is not silently dropped.
            logger.warning(
                "_ingest_session_batch: no running event loop for session %s — "
                "firing send_pulse via asyncio.run() (synchronous context; "
                "this should not happen in production)",
                sid,
            )
            asyncio.run(
                web_api_client.send_pulse(sid, _hr_rec.unix_ms, int(_hr_rec.heart_rate))
            )
    else:
        logger.debug(
            "_ingest_session_batch: batch for session %s had no valid HR reading "
            "(all %d records have heart_rate=0) — send_pulse skipped for this batch",
            sid, len(records),
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
        cutoff_unix_ms = int((time.time() - _SESSION_EVICT_AFTER_SEC) * 1000)

        stale = [
            sid
            for sid, rec in list(latest_records.items())
            if rec.unix_ms < cutoff_unix_ms
        ]
        if not stale:
            continue  # nothing to evict – keep the loop running

        for sid in stale:
            _windows.pop(sid, None)
            # Capture record_count BEFORE popping so session_end gets the real value.
            final_record_count = _record_counts.pop(sid, 0)
            latest_scores.pop(sid, None)
            last_rec = latest_records.pop(sid, None)
            # Read participant BEFORE evicting the cache entry.
            pid = web_api_client._participant_cache.pop(sid, None)

            # ── Update SQLite end_unix_ms ─────────────────────────────
            # Use current wall-clock time as the authoritative end time.
            _evict_now = datetime.now().astimezone()
            _evict_end_unix_ms = int(_evict_now.timestamp() * 1000)
            try:
                end_session(DB_PATH, sid, _evict_end_unix_ms)
            except Exception:
                logger.exception(
                    "_evict_stale_sessions: could not set end_unix_ms for session %r in SQLite",
                    sid,
                )

            # ── Write session-end event to participant JSONL ──────────
            if last_rec is not None and pid:
                _append_session_event(PARTICIPANTS_DIR, pid, {
                    "event": "session_end",
                    "session_id": sid,
                    "participant_id": pid,
                    # ended_at = current wall-clock (the eviction moment, not
                    # the last telemetry time which could be 4 h ago).
                    "ended_at": _evict_now.astimezone(timezone.utc).isoformat(),
                    "local_time": _evict_now.strftime("%Y-%m-%d %H:%M:%S %Z"),
                    # last_record_at = actual last telemetry timestamp so
                    # analysts can see when the rider truly stopped transmitting.
                    "last_record_at": datetime.fromtimestamp(
                        last_rec.unix_ms / 1000, tz=timezone.utc
                    ).isoformat(),
                    "record_count": final_record_count,
                    "reason": "idle_eviction",
                })
            elif last_rec is not None and not pid:
                logger.debug(
                    "Evicting session %r: no participant linked — session_end not written to JSONL",
                    sid,
                )

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
