"""
REST API endpoints for session data.

Mounted on the main FastAPI app (``main.py``).  All endpoints read from the
same SQLite database used by the ingest pipeline — no separate read-replica
is needed at this scale.

Endpoints:
  GET /healthz                      – liveness + DB reachability probe
  GET /api/sessions                 – list all sessions (newest first)
  GET /api/sessions/{session_id}    – full detail for one session
  GET /api/live/latest              – most-recent live state across all active sessions
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from live_analytics.app.config import DB_PATH
from live_analytics.app.models import LiveLatest, ScoringResult, SessionDetail, SessionSummary
from live_analytics.app.storage.sqlite_store import get_session, list_sessions, set_session_participant
from live_analytics.app.storage import web_api_client
from live_analytics.app.ws_ingest import latest_gameplay_records, latest_hr, latest_records, latest_scores

logger = logging.getLogger("live_analytics.api_sessions")

router = APIRouter()


def _db_health_check() -> tuple[bool, str]:
    """Probe the SQLite database with a lightweight SELECT 1.

    Returns a (ok, detail) tuple where *ok* is True when the DB is reachable
    and *detail* is either ``"ok"`` or an error message.

    This is intentionally lightweight — it opens the pooled connection (no
    file I/O if already open) and executes a single no-op query.  It is called
    on every /healthz request so that the dashboard can surface DB failures
    even when no sessions have been created yet.
    """
    try:
        from live_analytics.app.storage.sqlite_store import _connect
        conn = _connect(DB_PATH)
        conn.execute("SELECT 1").fetchone()
        return True, "ok"
    except Exception as exc:
        logger.warning("healthz: DB health check failed for '%s': %s", DB_PATH, exc)
        return False, str(exc)


@router.get("/healthz")
async def healthz() -> dict:
    """Health check endpoint.

    Response fields:
    - ``status``: always ``"ok"`` when the API process is alive.
    - ``db_ok``: ``True`` when the SQLite database is reachable, ``False`` otherwise.
    - ``db_path``: Resolved path to the SQLite database file.
    - ``db_detail``: ``"ok"`` on success or the error message on failure.
    """
    db_ok, db_detail = _db_health_check()
    return {
        "status": "ok",
        "db_ok": db_ok,
        "db_path": str(DB_PATH),
        "db_detail": db_detail,
    }


@router.get("/api/sessions", response_model=list[SessionSummary])
async def sessions_list() -> list[SessionSummary]:
    """Return all recorded sessions, newest first.

    Returns an empty list (not 404) when no sessions exist.
    Raises 503 if the database is unavailable rather than letting SQLite
    exceptions bubble up as an unformatted 500.
    """
    try:
        result = list_sessions(DB_PATH)
        logger.debug("sessions_list: returned %d sessions", len(result))
        return result
    except Exception as exc:
        logger.exception("Failed to list sessions from DB '%s'", DB_PATH)
        raise HTTPException(
            status_code=503,
            detail=f"Database unavailable: {type(exc).__name__}: {exc}",
        )


@router.get("/api/sessions/{session_id}", response_model=SessionDetail)
async def session_detail(session_id: str) -> SessionDetail:
    """Return full detail for a single session including the last stored scores.

    ``latest_scores`` reflects the most recently persisted snapshot
    (written every 20 records by the ingest pipeline) and may lag
    the real-time scores visible in the dashboard by up to 1 second.
    """
    try:
        detail = get_session(DB_PATH, session_id)
    except Exception:
        logger.exception(
            "Failed to get session '%s' from DB '%s'", session_id, DB_PATH
        )
        raise HTTPException(status_code=500, detail="Database error")
    if detail is None:
        logger.debug("session_detail: session '%s' not found in DB '%s'", session_id, DB_PATH)
        raise HTTPException(status_code=404, detail="Session not found")
    return detail


@router.get("/api/live/latest", response_model=LiveLatest | None)
async def live_latest() -> LiveLatest | None:
    """Return the latest live state across all active sessions."""
    if not latest_scores or not latest_records:
        return None
    try:
        # Snapshot to avoid RuntimeError if dict mutates during iteration
        records_snapshot = dict(latest_records)
        if not records_snapshot:
            return None
        sid = max(records_snapshot, key=lambda s: records_snapshot[s].unix_ms)
        rec = records_snapshot[sid]
        # Use the latest gameplay record for speed (never overwritten by
        # hr_only relay records which always have speed=0).
        gameplay_rec = latest_gameplay_records.get(sid)
        # Use the dedicated HR tracker which is updated from any record source.
        hr_val = latest_hr.get(sid) or rec.heart_rate
        scores = latest_scores.get(sid, ScoringResult())
        return LiveLatest(
            session_id=sid,
            unix_ms=rec.unix_ms,
            speed=gameplay_rec.speed if gameplay_rec is not None else None,
            heart_rate=hr_val,
            scores=scores,
        )
    except Exception:
        logger.warning("live_latest snapshot failed, returning None", exc_info=True)
        return None


# ── Participant linking ────────────────────────────────────────────────

class _LinkParticipantBody(BaseModel):
    participant_id: str


@router.put("/api/sessions/{session_id}/participant")
async def link_participant_to_session(session_id: str, body: _LinkParticipantBody) -> dict:
    """Link a questionnaire participant to an analytics session.

    Stores the ``participant_id`` in the local analytics DB and clears the
    in-memory participant cache so the next pulse write uses the updated ID.

    Body: ``{ "participant_id": "P001" }``
    """
    try:
        set_session_participant(DB_PATH, session_id, body.participant_id)
    except Exception as exc:
        logger.exception(
            "link_participant_to_session: DB error for session %r participant %r",
            session_id, body.participant_id,
        )
        raise HTTPException(status_code=500, detail=f"Database error: {exc}")

    # Clear cache so next pulse triggers a fresh lookup
    web_api_client.clear_participant_cache(session_id)
    logger.info(
        "link_participant_to_session: session %r → participant %r",
        session_id, body.participant_id,
    )
    return {"ok": True, "session_id": session_id, "participant_id": body.participant_id}


@router.post("/api/sessions/trigger-relink")
async def trigger_relink() -> dict:
    """Re-run participant resolution for every active session that has no participant yet.

    Called automatically by the questionnaire service whenever a new participant
    is created — this covers the case where Unity was already running when the
    test person registered.  For each unlinked active session the existing
    ``_resolve_and_link_participant`` retry loop is still running, but this
    endpoint also clears the cooldown cache and fires a fresh resolution task
    so linking happens within seconds rather than waiting for the next retry.

    Safe to call multiple times — duplicate tasks for the same session are
    harmless (participant cache is checked on every attempt).
    """
    import asyncio
    from live_analytics.app import ws_ingest
    from datetime import datetime, timezone

    # Find all sessions that are currently active (have an open sliding window)
    # but have no participant resolved yet.
    unlinked = [
        sid for sid in ws_ingest._windows
        if web_api_client.get_cached_participant(sid) is None
    ]

    if not unlinked:
        logger.debug("trigger_relink: no active unlinked sessions")
        return {"triggered": 0, "sessions": []}

    loop = asyncio.get_running_loop()
    triggered = []
    for sid in unlinked:
        # Clear cooldown so resolve_participant fires immediately.
        web_api_client.clear_participant_cache(sid)
        # Fire a fresh resolution task — uses the scenario / started_at from
        # the most recent record for this session if available, otherwise falls
        # back to sensible defaults.
        last = ws_ingest.latest_records.get(sid)
        scenario_id = (last.scenario_id if last and last.scenario_id else "") if last else ""
        started_at = (
            datetime.fromtimestamp(last.unix_ms / 1000, tz=timezone.utc).isoformat()
            if last else datetime.now(tz=timezone.utc).isoformat()
        )
        loop.create_task(
            ws_ingest._resolve_and_link_participant(sid, scenario_id, started_at)
        )
        triggered.append(sid)
        logger.info("trigger_relink: fired fresh resolution task for session %r", sid)

    return {"triggered": len(triggered), "sessions": triggered}
