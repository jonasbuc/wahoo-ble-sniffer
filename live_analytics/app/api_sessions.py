"""
REST API endpoints for session data.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from live_analytics.app.config import DB_PATH
from live_analytics.app.models import LiveLatest, ScoringResult, SessionDetail, SessionSummary
from live_analytics.app.storage.sqlite_store import get_session, list_sessions
from live_analytics.app.ws_ingest import latest_records, latest_scores

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
    try:
        result = list_sessions(DB_PATH)
        logger.debug("sessions_list: returned %d sessions", len(result))
        return result
    except Exception:
        logger.exception("Failed to list sessions from DB '%s'", DB_PATH)
        return []


@router.get("/api/sessions/{session_id}", response_model=SessionDetail)
async def session_detail(session_id: str) -> SessionDetail:
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
        scores = latest_scores.get(sid, ScoringResult())
        return LiveLatest(
            session_id=sid,
            unix_ms=rec.unix_ms,
            speed=rec.speed,
            heart_rate=rec.heart_rate,
            scores=scores,
        )
    except Exception:
        logger.warning("live_latest snapshot failed, returning None", exc_info=True)
        return None
