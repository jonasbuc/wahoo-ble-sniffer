"""
API router – Pulse Session management endpoints.

Provides explicit HTTP control over pulse-logging sessions so that an
operator or test-runner can start/end sessions independently of the
WebSocket ingest pipeline (e.g. for manual test runs or crash recovery).

Endpoints
---------
POST  /api/pulse-session/start
      Body: {"test_person_id": "TP_001", "session_id": "optional-override"}
      Starts a new pulse log session for the given participant.
      If a session is already open it is auto-closed first.

POST  /api/pulse-session/end
      Body: {"test_person_id": "TP_001"}
      Closes the currently open pulse log session for the given participant.
      Returns 404 if no session is open.

GET   /api/pulse-session/current
      Returns all currently open pulse sessions (all participants).

GET   /api/pulse-session/current/{test_person_id}
      Returns the open session info for a specific participant.
      Returns 404 if no session is open.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from live_analytics.app.pulse_session_logger import get_pulse_logger

router = APIRouter(prefix="/api/pulse-session", tags=["pulse-session"])


# ── Request / response models ─────────────────────────────────────────

class StartSessionRequest(BaseModel):
    test_person_id: str = Field(..., min_length=1, description="Questionnaire / test-person ID")
    session_id: str | None = Field(
        default=None,
        description="Optional explicit session ID. Defaults to current unix-ms string.",
    )
    extra: dict[str, Any] | None = Field(
        default=None,
        description="Optional extra fields written into the session_start record.",
    )


class EndSessionRequest(BaseModel):
    test_person_id: str = Field(..., min_length=1, description="Questionnaire / test-person ID")
    extra: dict[str, Any] | None = Field(
        default=None,
        description="Optional extra fields written into the session_end record.",
    )


# ── Helpers ───────────────────────────────────────────────────────────

def _require_logger():
    """Return the shared ``PulseSessionLogger`` instance, or raise HTTP 503 if not yet initialised.

    The logger is created during the FastAPI lifespan startup.  If an endpoint
    is called before startup completes (or after an unexpected startup failure),
    this guard surfaces a clear 503 rather than an ``AttributeError``.
    """
    psl = get_pulse_logger()
    if psl is None:
        raise HTTPException(
            status_code=503,
            detail="PulseSessionLogger not initialised — server may still be starting up.",
        )
    return psl


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/start", status_code=200)
async def start_pulse_session(body: StartSessionRequest) -> dict[str, Any]:
    """Start a new pulse-log session for a participant.

    If the participant already has an open session it is auto-closed first.
    """
    psl = _require_logger()
    session_id = body.session_id or str(int(time.time() * 1000))

    psl.start_session(
        body.test_person_id,
        session_id,
        extra=body.extra,
    )

    active = psl.active_sessions().get(body.test_person_id)
    return {
        "status": "started",
        "test_person_id": body.test_person_id,
        "session_id": session_id,
        "log_file": active["log_file"] if active else None,
    }


@router.post("/end", status_code=200)
async def end_pulse_session(body: EndSessionRequest) -> dict[str, Any]:
    """Close the currently open pulse-log session for a participant.

    Returns 404 if no session is open.
    """
    psl = _require_logger()

    active = psl.active_sessions()
    if body.test_person_id not in active:
        raise HTTPException(
            status_code=404,
            detail=f"No open pulse session for participant {body.test_person_id!r}.",
        )

    info = active[body.test_person_id]
    psl.close_session(body.test_person_id, extra=body.extra)

    return {
        "status": "ended",
        "test_person_id": body.test_person_id,
        "session_id": info["session_id"],
        "log_file": info["log_file"],
        "pulse_records": info["pulse_records"],
    }


@router.get("/current", status_code=200)
async def get_current_pulse_sessions() -> dict[str, Any]:
    """Return all currently open pulse sessions."""
    psl = _require_logger()
    sessions = psl.active_sessions()
    return {
        "active_session_count": len(sessions),
        "sessions": list(sessions.values()),
    }


@router.get("/current/{test_person_id}", status_code=200)
async def get_current_pulse_session_for_participant(test_person_id: str) -> dict[str, Any]:
    """Return the open session info for a specific participant.

    Returns 404 if no session is open.
    """
    psl = _require_logger()
    sessions = psl.active_sessions()
    if test_person_id not in sessions:
        raise HTTPException(
            status_code=404,
            detail=f"No open pulse session for participant {test_person_id!r}.",
        )
    return sessions[test_person_id]
