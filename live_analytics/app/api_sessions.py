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


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@router.get("/api/sessions", response_model=list[SessionSummary])
async def sessions_list() -> list[SessionSummary]:
    try:
        return list_sessions(DB_PATH)
    except Exception:
        logger.exception("Failed to list sessions")
        return []


@router.get("/api/sessions/{session_id}", response_model=SessionDetail)
async def session_detail(session_id: str) -> SessionDetail:
    try:
        detail = get_session(DB_PATH, session_id)
    except Exception:
        logger.exception("Failed to get session %s", session_id)
        raise HTTPException(status_code=500, detail="Database error")
    if detail is None:
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
