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
- ``_ingest_session_batch`` / ``_write_db_batch`` split: all blocking I/O
  (SQLite writes, JSONL file appends) runs in a thread-pool executor via
  ``run_in_executor`` so it never freezes the asyncio event loop.  The fast
  in-memory mutations (_windows, _record_counts, latest_scores, etc.) remain
  on the event-loop thread where they are safe without locks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
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
from live_analytics.app.storage.participant_logs import (
    append_pulse as _append_pulse_to_file,
    append_pulse_session_marker as _append_pulse_marker,
    append_session_event as _append_session_event,
)
from live_analytics.app.pulse_session_logger import get_pulse_logger as _get_pulse_logger
from live_analytics.app.storage.sqlite_store import (
    end_session,
    increment_record_count,
    insert_records,
    set_session_participant,
    update_latest_scores,
    upsert_session,
)
from live_analytics.app.utils.time_utils import fmt_iso as _fmt_iso, TZ as _TZ, unix_ms_to_cph_iso as _unix_ms_to_cph_iso

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
# Latest record per session – used for session selection and eviction tracking.
latest_records: dict[str, TelemetryRecord] = {}
# Latest gameplay (non-hr_only) record per session – used for speed/steering in live metrics.
latest_gameplay_records: dict[str, TelemetryRecord] = {}
# Latest HR value per session – updated from any record type (relay or Unity).
latest_hr: dict[str, float] = {}

# Set of active dashboard WebSocket connections to broadcast score updates to.
dashboard_subscribers: set[Any] = set()

# Injected by main.py lifespan; None in unit tests (degraded mode: no JSONL persistence).
_raw_writer: RawWriter | None = None

# How often (in records) to persist scores to SQLite.
# A score snapshot is written whenever the record count crosses a multiple of this value.
# Lower values increase DB write frequency; 20 matches the default Unity batch size.
_SCORE_PERSIST_EVERY = 20

# ── Per-session participant-resolution lock ───────────────────────────
# Prevents trigger_relink from launching a duplicate _resolve_and_link_participant
# task while the original retry loop is still running.  Without this guard,
# two concurrent tasks that both resolve the same participant would each write
# a session_start event to JSONL and call PulseSessionLogger.start_session()
# — producing duplicate markers in every participant log file.
#
# asyncio is single-threaded: .add() and .discard() inside coroutines on the
# same event-loop thread are inherently race-free without locks.
_resolve_running: set[str] = set()


# ── DB write payload ──────────────────────────────────────────────────
@dataclass
class _DbWritePayload:
    """All parameters needed for the blocking I/O part of one ingest batch.

    Created by ``_ingest_session_batch`` (event-loop thread, fast/no-I/O) and
    passed to ``_write_db_batch`` which runs in a ``ThreadPoolExecutor`` via
    ``run_in_executor``.  This split ensures that SQLite writes and JSONL file
    appends never block the asyncio event loop, keeping the FastAPI HTTP server
    responsive to dashboard requests even under heavy Unity telemetry load.

    Fields must be plain Python values or thread-safe objects — no asyncio
    primitives (Queues, Events, etc.) may be included here.
    """
    sid: str
    records: list[Any]           # list[TelemetryRecord] — all records in batch
    gameplay_to_write: list[Any] # non-hr_only records written to JSONL
    is_new: bool                 # True on first batch for this session
    first_unix_ms: int           # unix_ms of the first record (for upsert_session)
    first_scenario_id: str       # scenario_id of the first record
    n: int                       # number of records in this batch
    old_count: int               # record_count BEFORE this batch
    new_count: int               # record_count AFTER this batch
    hr_records: list[Any]        # records with heart_rate > 0
    cached_pid: str | None       # participant_id from cache, or None if not yet resolved
    created_at: str              # ISO timestamp for pulse log entries
    local_time: str              # human-readable local time for pulse log entries
    scores: Any                  # ScoringResult | None — latest computed scores
    should_persist_scores: bool  # True when new_count crosses a _SCORE_PERSIST_EVERY boundary
    psl: Any                     # PulseSessionLogger | None (module-level singleton)


def _write_db_batch(p: _DbWritePayload) -> None:
    """Execute all blocking I/O for one ingest batch in a thread-pool executor.

    Runs entirely on a worker thread — must NOT read or write any of the
    module-level asyncio state dicts (_windows, _record_counts, latest_scores,
    etc.) which are owned by the event-loop thread.

    Write order:
      1. ``upsert_session``         — only on first batch for *sid*
      2. ``_raw_writer.append_many``— JSONL file append (gameplay records only)
      3. ``insert_records``         — SQLite telemetry_records table
      4. ``increment_record_count`` — SQLite sessions.record_count
      5. pulse file appends         — per-participant JSONL + PulseSessionLogger
      6. ``update_latest_scores``   — SQLite sessions.latest_scores (periodic)
    """
    if p.is_new:
        try:
            upsert_session(DB_PATH, p.sid, p.first_unix_ms, p.first_scenario_id)
        except Exception:
            logger.exception(
                "DB error: could not upsert session %s in %s — "
                "session will still be scored and raw JSONL written, "
                "but record counts and scores will NOT be persisted to SQLite",
                p.sid, DB_PATH,
            )

    if _raw_writer and p.gameplay_to_write:
        _raw_writer.append_many(p.gameplay_to_write)
    elif not p.is_new and not _raw_writer:
        # Only log once — caller already logged on first batch (is_new path)
        pass

    try:
        insert_records(DB_PATH, p.records)
    except Exception:
        logger.exception(
            "DB error: could not insert %d records for session %s into telemetry_records",
            len(p.records), p.sid,
        )

    try:
        increment_record_count(DB_PATH, p.sid, p.n)
    except Exception:
        logger.exception(
            "DB error: could not increment record_count for session %s in %s",
            p.sid, DB_PATH,
        )

    if p.cached_pid and p.hr_records:
        for hr_rec in p.hr_records:
            try:
                _append_pulse_to_file(PARTICIPANTS_DIR, p.cached_pid, {
                    "session_id": p.sid,
                    "unix_ms": hr_rec.unix_ms,
                    "pulse": int(hr_rec.heart_rate),
                    "participant_id": p.cached_pid,
                    "created_at": p.created_at,
                    "local_time": p.local_time,
                })
            except Exception:
                logger.exception(
                    "_write_db_batch: could not write pulse to local log file "
                    "(participant=%r, session=%s, path=%s/pulse.jsonl)",
                    p.cached_pid, p.sid, PARTICIPANTS_DIR / p.cached_pid,
                )
            if p.psl is not None:
                p.psl.write_pulse(
                    p.cached_pid, p.sid, hr_rec.unix_ms, int(hr_rec.heart_rate)
                )

    if p.should_persist_scores and p.scores is not None:
        try:
            update_latest_scores(DB_PATH, p.sid, p.scores)
        except Exception:
            logger.exception(
                "DB error: could not persist scores for session %s in %s",
                p.sid, DB_PATH,
            )


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
    _now = datetime.now(_TZ)
    ended_at = _now.isoformat()
    local_time = _now.strftime("%Y-%m-%d %H:%M:%S %Z")
    end_unix_ms = int(_now.timestamp() * 1000)

    for sid in session_ids:
        try:
            await _process_one_disconnect(sid, ended_at, local_time, end_unix_ms)
        finally:
            # Always pop the window so the background _resolve_and_link_participant
            # task aborts on its next iteration (it checks `if sid not in _windows`).
            # This prevents the task from linking a fresh FIFO participant to a
            # session that has already ended — which would mark that participant
            # as done without them ever having ridden.
            _windows.pop(sid, None)


async def _process_one_disconnect(sid: str, ended_at: str, local_time: str, end_unix_ms: int) -> None:
    """Process session-end for a single session_id on disconnect.

    Extracted from the _on_disconnect loop so the try/finally in _on_disconnect
    can unconditionally pop _windows[sid] regardless of early returns.
    """
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
        # Safety-net: if a participant was already resolved (cached) for
        # this session, make sure they are unlinked so they can be
        # auto-linked to the next session.  This handles the race where
        # _evict_stale_sessions already ran (clearing _participant_cache
        # and calling unlink), so get_cached_participant returns None and
        # this call is a cheap no-op.  It also handles the case where
        # all ingest batches failed (so latest_records was never set) but
        # the resolve task had already linked the participant.
        _safety_pid = web_api_client.get_cached_participant(sid)
        if _safety_pid:
            logger.info(
                "_on_disconnect: safety-net unlink — participant %r was linked "
                "to session %r but no records received; unlinking so they re-enter FIFO pool",
                _safety_pid, sid,
            )
            await web_api_client.clear_participant_session_link(_safety_pid, session_id=sid)
        return

    pid = web_api_client.get_cached_participant(sid)
    if not pid:
        # Participant not yet resolved — try one last lookup before giving up.
        pid = await web_api_client.resolve_participant(sid)

    if not pid:
        logger.warning(
            "_on_disconnect: no participant linked to session %r "
            "— session_end not written. "
            "Register the participant in the questionnaire before putting on "
            "the headset and link the session_id immediately after pressing Play.",
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
        return

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
    # ── Write SESSION_END marker to pulse log ─────────────────────
    # After this marker, no more pulse data will be written for this session.
    # The next participant's session will start with its own SESSION_START marker.
    _append_pulse_marker(
        PARTICIPANTS_DIR,
        pid,
        marker="SESSION_END",
        session_id=sid,
        timestamp=ended_at,
        local_time=local_time,
        extra={"record_count": _record_counts.get(sid, 0)},
    )
    # Close the dedicated PulseSessionLogger file for this participant.
    _psl = _get_pulse_logger()
    if _psl is not None:
        _psl.close_session(
            pid, extra={"record_count": _record_counts.get(sid, 0)}
        )
    logger.info(
        "session_end written — session=%r participant=%r records=%d "
        "ended_at=%s SQLite.end_unix_ms updated",
        sid, pid, _record_counts.get(sid, 0), local_time,
    )
    # ── Mark participant as done ───────────────────────────────────
    # Sets session_id = '__done__' in the questionnaire DB so this
    # participant is permanently excluded from the FIFO pool and
    # cannot be auto-linked to a future Unity session.
    await web_api_client.mark_participant_done(pid, session_id=sid)


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

    # ── Handle explicit session-signal events from Unity ─────────────
    # Unity sends {"event": "start_session"|"end_session", "session_id": "…"}
    # These are treated as hints to the PulseSessionLogger.  The ingest
    # pipeline's own connect/disconnect handling is authoritative, but the
    # explicit signals allow Unity to mark clean starts without waiting for
    # participant resolution.
    _event = data.get("event") if isinstance(data, dict) else None
    if _event in ("start_session", "end_session"):
        _sig_sid = data.get("session_id", "")
        _psl = _get_pulse_logger()
        if _event == "start_session":
            logger.info(
                "_process_message: received start_session signal from Unity "
                "(session_id=%r) — PulseSessionLogger will open file when participant resolves",
                _sig_sid,
            )
            # Note: we do NOT call _psl.start_session() here because the
            # participant_id is not yet known at this point.  The actual
            # start_session() call happens inside _resolve_and_link_participant()
            # once the questionnaire participant is resolved.
        elif _event == "end_session" and _psl is not None:
            # Use the participant cache to resolve pid from session_id.
            _sig_pid = web_api_client.get_cached_participant(_sig_sid)
            if _sig_pid:
                logger.info(
                    "_process_message: received end_session signal from Unity "
                    "(session_id=%r, participant=%r) — closing PulseSessionLogger file",
                    _sig_sid, _sig_pid,
                )
                _psl.close_session(_sig_pid, extra={"trigger": "unity_signal"})
            else:
                logger.debug(
                    "_process_message: received end_session signal for unknown session %r "
                    "— no participant cached yet; PulseSessionLogger close will happen on disconnect",
                    _sig_sid,
                )
        return  # Do not try to parse this as a TelemetryBatch

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
            # Phase 1 — fast, non-blocking: update in-memory state, compute scores,
            # schedule async tasks (participant resolution, API pulse send).
            db_payload = _ingest_session_batch(sid, records)
            # Phase 2 — blocking I/O: SQLite writes + JSONL file appends run in a
            # thread-pool executor so the event loop stays free to handle dashboard
            # HTTP requests while Unity is streaming telemetry at 20 Hz.
            await asyncio.get_running_loop().run_in_executor(
                None, _write_db_batch, db_payload
            )
        except Exception:
            logger.exception(
                "Failed to ingest batch for session %s (%d records) – batch dropped",
                sid, len(records),
            )

    # ── Send feedback to Unity (latest scores for first session) ─────
    session_id = batch.records[0].session_id
    if session_id in latest_scores:
        scores = latest_scores[session_id]
        # Guard against NaN/Inf: Pydantic serialises non-finite floats as JSON
        # `null`, which Unity's JsonUtility cannot parse for a C# `float` field.
        feedback = LiveFeedback(
            stress_score=scores.stress_score if math.isfinite(scores.stress_score) else 0.0,
            risk_score=scores.risk_score if math.isfinite(scores.risk_score) else 0.0,
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

    Called once per new session.  The intended workflow is:
      1. Operator registers the test participant in the questionnaire UI
         (http://localhost:8090) *before* the headset goes on.
      2. Unity starts → session_id is created.
      3. Operator links the new session_id to the pre-registered participant
         via the questionnaire UI or:
           PUT :8090/api/participants/{id}/session  {"session_id": "..."}
         This also notifies the analytics API to clear the participant cache
         so resolution happens immediately.
      4. This coroutine resolves the participant, writes SESSION_START, and
         opens the PulseSessionLogger file.

    Retry schedule (tiered):
      - Attempts 1–12 : every 5 s  (covers ~60 s — normal linking window)
      - Attempts 13–22: every 60 s (covers ~10 min — fallback for late linking)

    Failures are logged but never propagated so the ingest pipeline is not affected.

    Duplicate-task guard
    --------------------
    ``trigger_relink`` may fire an additional task for the same *sid* while the
    original retry loop is still running.  The ``_resolve_running`` set prevents
    both tasks from each writing session_start events or calling
    ``PulseSessionLogger.start_session()`` more than once.
    """
    # ── Duplicate-task guard ──────────────────────────────────────────
    if sid in _resolve_running:
        logger.debug(
            "_resolve_and_link_participant: task already running for session %r — "
            "skipping duplicate (triggered by trigger_relink or rapid reconnect)",
            sid,
        )
        return
    _resolve_running.add(sid)
    try:
        await _do_resolve_and_link_participant(sid, scenario_id, started_at)
    finally:
        _resolve_running.discard(sid)


async def _do_resolve_and_link_participant(sid: str, scenario_id: str, started_at: str) -> None:
    """Inner implementation — do not call directly; use _resolve_and_link_participant."""
    # Tiered delays: fast retries first so a pre-registered participant is
    # linked within seconds; slow retries as a fallback.
    _FAST_RETRIES = 12
    _FAST_DELAY_SEC = 5.0
    _SLOW_RETRIES = 10
    _SLOW_DELAY_SEC = 60.0
    _RESOLVE_MAX_RETRIES = _FAST_RETRIES + _SLOW_RETRIES

    for attempt in range(1, _RESOLVE_MAX_RETRIES + 1):
        # ── Early-exit: session closed or evicted ─────────────────────────
        # _on_disconnect() pops _windows[sid] after processing the session so
        # this check fires immediately on the next retry cycle — preventing
        # the task from linking a fresh participant to an already-ended session.
        # _evict_stale_sessions also pops _windows so the same guard covers
        # eviction-triggered aborts.
        if sid not in _windows:
            logger.debug(
                "_resolve_and_link_participant: session %r is no longer active "
                "(removed from _windows by disconnect/eviction) — aborting after %d attempt(s).",
                sid, attempt,
            )
            return

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
            # Write SESSION_START marker to the participant's pulse log so it is
            # immediately clear which person's pulse data follows and from when.
            _append_pulse_marker(
                PARTICIPANTS_DIR,
                pid,
                marker="SESSION_START",
                session_id=sid,
                timestamp=started_at,
                local_time=_fmt_iso(started_at),
                extra={"scenario_id": scenario_id},
            )
            # Start a dedicated PulseSessionLogger file for this participant/session.
            _psl = _get_pulse_logger()
            if _psl is not None:
                _psl.start_session(
                    pid, sid, extra={"scenario_id": scenario_id}
                )
            logger.info(
                "_resolve_and_link_participant: session_start written for session %r "
                "(participant=%r, scenario=%r, attempt=%d)",
                sid, pid, scenario_id, attempt,
            )
            return

        if attempt < _RESOLVE_MAX_RETRIES:
            delay = _FAST_DELAY_SEC if attempt <= _FAST_RETRIES else _SLOW_DELAY_SEC
            logger.debug(
                "_resolve_and_link_participant: no participant yet for session %r "
                "(attempt %d/%d) — retrying in %.0f s",
                sid, attempt, _RESOLVE_MAX_RETRIES, delay,
            )
            await asyncio.sleep(delay)
        else:
            logger.warning(
                "_resolve_and_link_participant: no participant found for session %r "
                "(scenario=%r) after %d attempts — session not linked and "
                "session_start not written to JSONL. "
                "Make sure the participant is registered in the questionnaire "
                "(http://localhost:8090) *before* the headset goes on, then link "
                "the session_id to the participant immediately after pressing Play.",
                sid, scenario_id, _RESOLVE_MAX_RETRIES,
            )


def _ingest_session_batch(sid: str, records: list[TelemetryRecord]) -> _DbWritePayload:
    """Fast in-memory part of batch ingest — runs on the event-loop thread.

    Updates all module-level state dicts (_windows, _record_counts,
    latest_scores, latest_records, latest_hr, latest_gameplay_records),
    computes scores, and schedules async tasks (participant resolution,
    pulse API send).  Returns a :class:`_DbWritePayload` with everything
    ``_write_db_batch`` needs to perform the blocking I/O in a thread-pool
    executor.

    **No blocking I/O here** — every SQLite write and file append has been
    extracted to ``_write_db_batch`` so this function completes in
    microseconds and never stalls the FastAPI HTTP server.

    Degraded mode: if ``_raw_writer`` is None (e.g. in unit tests or a
    failed startup), JSONL persistence is skipped but scoring still runs.
    """
    first_rec = records[0]
    is_new = sid not in _windows

    # ── Initialise session on first encounter ────────────────────────
    if is_new:
        _windows[sid] = deque(maxlen=_WINDOW_MAX)
        _record_counts[sid] = 0
        logger.info(
            "New session started: %s (scenario=%r)",
            sid, first_rec.scenario_id,
        )
        # Resolve and cache the participant asynchronously — must be done here
        # (on the event-loop thread) because create_task requires the loop.
        try:
            loop = asyncio.get_running_loop()
            _started_at = _unix_ms_to_cph_iso(first_rec.unix_ms)
            loop.create_task(_resolve_and_link_participant(
                sid, first_rec.scenario_id or "", _started_at
            ))
        except RuntimeError:
            pass  # No running event loop (e.g. synchronous unit tests)
    elif _record_counts.get(sid, -1) == 0:
        # First batch after session re-init but not truly new — log raw_writer state once
        if not _raw_writer:
            logger.debug(
                "raw_writer not initialised – JSONL persistence disabled for session %s "
                "(running in degraded/test mode; scoring and DB record counts still active)",
                sid,
            )

    # ── Update sliding window (in-memory, fast) ──────────────────────
    window = _windows[sid]
    for rec in records:
        window.append(rec)

    # ── Update counters and latest pointers (in-memory, fast) ────────
    n = len(records)
    old_count = _record_counts[sid]
    new_count = old_count + n
    _record_counts[sid] = new_count

    latest_records[sid] = records[-1]

    for rec in records:
        if rec.heart_rate > 0:
            latest_hr[sid] = rec.heart_rate

    gameplay_recs = [r for r in records if r.record_type == "gameplay"]
    if gameplay_recs:
        latest_gameplay_records[sid] = gameplay_recs[-1]

    # ── HR records for pulse logging ──────────────────────────────────
    hr_records = [r for r in records if r.heart_rate > 0]
    cached_pid = web_api_client.get_cached_participant(sid) if hr_records else None

    if hr_records:
        # Schedule external API pulse send (non-blocking async task).
        # Only the LAST HR value per batch is sent to external APIs to avoid
        # flooding them at full 20 Hz sample rate.
        last_hr_rec = hr_records[-1]
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                web_api_client.send_pulse(sid, last_hr_rec.unix_ms, int(last_hr_rec.heart_rate))
            )
        except RuntimeError:
            logger.warning(
                "_ingest_session_batch: no running event loop for session %s — "
                "firing send_pulse via asyncio.run() (synchronous context; "
                "this should not happen in production)",
                sid,
            )
            asyncio.run(
                web_api_client.send_pulse(sid, last_hr_rec.unix_ms, int(last_hr_rec.heart_rate))
            )
    else:
        logger.debug(
            "_ingest_session_batch: batch for session %s had no valid HR reading "
            "(all %d records have heart_rate=0) — pulse log and send_pulse skipped for this batch",
            sid, len(records),
        )

    # ── Score (CPU-bound but fast — uses a list snapshot of the window) ──
    scores: Any = None
    try:
        scores = compute_scores(list(window))
    except Exception:
        logger.exception(
            "Scoring failed for session %s (window size=%d) – keeping previous scores",
            sid, len(window),
        )
    if scores is not None:
        latest_scores[sid] = scores

    # ── Determine if scores need DB persistence this batch ────────────
    should_persist_scores = (
        scores is not None
        and old_count // _SCORE_PERSIST_EVERY != new_count // _SCORE_PERSIST_EVERY
    )

    gameplay_to_write = [r for r in records if r.record_type != "hr_only"]

    # Timestamps for pulse log entries — computed once per batch.
    _now = datetime.now(_TZ)

    return _DbWritePayload(
        sid=sid,
        records=records,
        gameplay_to_write=gameplay_to_write,
        is_new=is_new,
        first_unix_ms=first_rec.unix_ms,
        first_scenario_id=first_rec.scenario_id or "",
        n=n,
        old_count=old_count,
        new_count=new_count,
        hr_records=hr_records,
        cached_pid=cached_pid,
        created_at=_now.isoformat(),
        local_time=_now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        scores=scores,
        should_persist_scores=should_persist_scores,
        psl=_get_pulse_logger(),
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
    gameplay_rec = latest_gameplay_records.get(session_id)
    payload = json.dumps({
        "session_id": session_id,
        "unix_ms": rec.unix_ms,
        # Use latest gameplay record for speed — hr_only records always have
        # speed=0 and would give the dashboard a false "stopped" reading.
        "speed": gameplay_rec.speed if gameplay_rec is not None else None,
        # Use dedicated HR tracker (updated from every record type) instead of
        # the latest record's heart_rate field which may be 0 for gameplay records
        # that arrived between HR broadcasts.
        "heart_rate": latest_hr.get(session_id) or rec.heart_rate,
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
            latest_gameplay_records.pop(sid, None)
            latest_hr.pop(sid, None)
            # Read participant BEFORE clearing the cache entry.
            pid = web_api_client.get_cached_participant(sid)
            # clear_participant_cache cleans _participant_cache, cooldown, and
            # _warned_userid_zero in one call — avoid popping _participant_cache
            # directly which would leave those other dicts stale.
            web_api_client.clear_participant_cache(sid)

            # ── Update SQLite end_unix_ms ─────────────────────────────
            # Use current wall-clock time as the authoritative end time.
            _evict_now = datetime.now(_TZ)
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
                    "ended_at": _evict_now.isoformat(),
                    "local_time": _evict_now.strftime("%Y-%m-%d %H:%M:%S %Z"),
                    # last_record_at = actual last telemetry timestamp so
                    # analysts can see when the rider truly stopped transmitting.
                    "last_record_at": _unix_ms_to_cph_iso(last_rec.unix_ms),
                    "record_count": final_record_count,
                    "reason": "idle_eviction",
                })
            elif last_rec is not None and not pid:
                logger.debug(
                    "Evicting session %r: no participant linked — session_end not written to JSONL",
                    sid,
                )

            # ── Unlink or mark done in questionnaire DB ───────────────
            # Sessions that received records: the participant completed their
            # session (normal disconnect called mark_participant_done, but the
            # HTTP call may have failed, leaving the cache populated).  Retry
            # mark_participant_done so they are permanently excluded from the
            # FIFO pool — not silently recycled.
            # Sessions with NO records: the participant was linked but never
            # sent data (e.g. headset put on briefly then taken off without
            # starting the Unity scene).  Return them to the FIFO pool so they
            # can be auto-linked when the next Unity session starts.
            if pid:
                if last_rec is not None:
                    # Session had data → permanent done (not FIFO-recyclable)
                    await web_api_client.mark_participant_done(pid, session_id=sid)
                    logger.info(
                        "_evict_stale_sessions: participant %r marked done "
                        "(session %r had records — eviction retry of done-mark)",
                        pid, sid,
                    )
                else:
                    # Session had no data → return to FIFO pool
                    await web_api_client.clear_participant_session_link(pid, session_id=sid)
                    logger.info(
                        "_evict_stale_sessions: participant %r unlinked "
                        "(session %r had no records — returned to FIFO pool)",
                        pid, sid,
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
