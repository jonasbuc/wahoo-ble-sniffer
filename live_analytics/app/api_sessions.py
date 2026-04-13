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
    return list_sessions(DB_PATH)


@router.get("/api/sessions/{session_id}", response_model=SessionDetail)
async def session_detail(session_id: str) -> SessionDetail:
    detail = get_session(DB_PATH, session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return detail


@router.get("/api/live/latest", response_model=LiveLatest | None)
async def live_latest() -> LiveLatest | None:
    """Return the latest live state across all active sessions."""
    if not latest_scores or not latest_records:
        return None
    # Pick the most recently updated session
    sid = max(latest_records, key=lambda s: latest_records[s].unix_ms)
    rec = latest_records[sid]
    scores = latest_scores.get(sid, ScoringResult())
    return LiveLatest(
        session_id=sid,
        unix_ms=rec.unix_ms,
        speed=rec.speed,
        heart_rate=rec.heart_rate,
        scores=scores,
    )
