"""
Questionnaire Service – FastAPI application.

Hosts:
  • REST API for participant management & answer CRUD
  • Static SPA frontend served from ./static/
  • Runs on QS_PORT (default 8090)
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from live_analytics.questionnaire.config import ANALYTICS_API_URL, DB_PATH, HOST, LOG_LEVEL, PARTICIPANTS_DIR, PORT, ensure_dirs
from live_analytics.questionnaire.db import (
    create_participant,
    delete_participant_data,
    get_answers,
    get_oldest_unlinked_participant,
    get_participant,
    get_participant_by_session,
    get_progress,
    get_pulse_data,
    init_db,
    insert_pulse_data,
    link_session,
    list_participants,
    mark_participant_done,
    save_answer,
    save_answers_bulk,
    unlink_session,
)
from live_analytics.questionnaire.models import AnswerSave, AnswersBulkSave, LinkSession, ParticipantCreate, PulseDataCreate, PulseDataOut
from live_analytics.questionnaire.questions import QUESTIONNAIRES
from live_analytics.app.storage.participant_logs import create_participant_log_dir

# ── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
)
logger = logging.getLogger("questionnaire")

STATIC_DIR = Path(__file__).resolve().parent / "static"


# ── Lifespan ──────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """Startup / shutdown logic for the questionnaire service."""
    logger.info("── Questionnaire service startup ──────────────────")
    logger.info("  DB_PATH = %s", DB_PATH)
    logger.info("  Listen  = %s:%d", HOST, PORT)
    try:
        ensure_dirs()
    except Exception as exc:
        logger.critical(
            "Questionnaire startup failed: could not create data directories: %s",
            exc,
        )
        raise
    try:
        init_db(DB_PATH)
    except Exception as exc:
        logger.critical(
            "Questionnaire startup failed: could not initialise DB at '%s': %s",
            DB_PATH, exc,
        )
        raise
    logger.info("Questionnaire service ready on %s:%d", HOST, PORT)
    yield
    logger.info("── Questionnaire service shutdown ─────────────────")


# ── App ───────────────────────────────────────────────────────────────
app = FastAPI(title="Questionnaire Service", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Serve SPA ─────────────────────────────────────────────────────────

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# Mount static files AFTER explicit routes so /api/* takes priority
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Questionnaire definitions ─────────────────────────────────────────

@app.get("/api/questionnaire/{phase}")
async def get_questionnaire(phase: str) -> dict:
    """Return the questionnaire definition for a phase (pre/post)."""
    qdef = QUESTIONNAIRES.get(phase)
    if not qdef:
        raise HTTPException(404, f"Unknown phase: {phase}")
    return qdef.model_dump()


@app.get("/api/questionnaire")
async def list_questionnaires() -> dict:
    """Return available phases."""
    return {"phases": list(QUESTIONNAIRES.keys())}


# ── Participants ──────────────────────────────────────────────────────

@app.post("/api/participants")
async def create_participant_endpoint(body: ParticipantCreate) -> dict:
    try:
        result = create_participant(DB_PATH, body.participant_id, body.display_name, body.session_id, body.metadata)
    except Exception as exc:
        logger.exception(
            "DB error creating participant '%s': %s", body.participant_id, exc
        )
        raise HTTPException(status_code=500, detail="Failed to create participant – see server log")
    if result is None:
        logger.error(
            "create_participant returned None for participant_id='%s' – "
            "insert may have succeeded but the subsequent SELECT failed",
            body.participant_id,
        )
        raise HTTPException(status_code=500, detail="Participant created but could not be retrieved")

    # Create the on-disk log directory and placeholder files for this participant
    # so that pulse.jsonl, session.jsonl and info.json are ready immediately.
    try:
        create_participant_log_dir(
            PARTICIPANTS_DIR,
            body.participant_id,
            display_name=body.display_name,
            created_at=result.get("created_at", ""),
        )
    except Exception:
        # Never fail the API call because of a filesystem error — the participant
        # is already in the DB.  The log dir can be re-created on next startup.
        logger.exception(
            "create_participant_endpoint: could not create log dir for participant %r "
            "(DB record was saved — filesystem error only)",
            body.participant_id,
        )

    logger.info(
        "Participant registered: id=%r display_name=%r session_id=%r",
        body.participant_id, body.display_name, body.session_id or "(none)",
    )

    # If Unity is already running there may be active sessions with no participant
    # yet.  Notify the analytics API so it immediately retries resolution for
    # those sessions — the newly registered participant will be auto-linked
    # within seconds via the oldest-unlinked (FIFO) mechanism.
    # IMPORTANT: FIFO ordering ensures this new participant is only linked to a
    # session that has no participant yet.  If all active sessions are already
    # linked, trigger-relink is a no-op.
    async def _trigger_relink() -> None:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
                resp = await client.post(f"{ANALYTICS_API_URL}/api/sessions/trigger-relink")
                logger.debug(
                    "create_participant_endpoint: trigger-relink → HTTP %d  sessions=%s",
                    resp.status_code,
                    resp.json().get("sessions", []) if resp.status_code == 200 else "?",
                )
        except Exception as exc:  # noqa: BLE001
            # Analytics API may not be running — non-fatal.
            logger.debug(
                "create_participant_endpoint: could not trigger relink (%s: %s)",
                type(exc).__name__, exc,
            )

    asyncio.create_task(_trigger_relink())
    return result


@app.get("/api/participants")
async def list_participants_endpoint() -> list[dict]:
    try:
        return list_participants(DB_PATH)
    except Exception as exc:
        logger.exception("DB error listing participants: %s", exc)
        raise HTTPException(status_code=503, detail="Failed to list participants – see server log")


@app.get("/api/participants/by-session/{session_id}")
async def get_participant_by_session_endpoint(session_id: str) -> dict:
    """Look up which participant is linked to an analytics session_id."""
    p = get_participant_by_session(DB_PATH, session_id)
    if not p:
        raise HTTPException(404, f"No participant linked to session {session_id!r}")
    return p


@app.get("/api/participants/oldest-unlinked")
async def get_oldest_unlinked_endpoint() -> dict:
    """Return the oldest registered participant that has no session linked yet (FIFO).

    Used by the analytics server for automatic session ↔ participant linking:
    FIFO ordering ensures that if multiple participants are pre-registered,
    each is linked to sessions in the order they registered — preventing a
    newly created P2 from being accidentally linked to a session already
    mid-ride for P1.
    Returns 404 when every registered participant is already linked (or none exist).
    """
    p = get_oldest_unlinked_participant(DB_PATH)
    if not p:
        raise HTTPException(404, "No unlinked participant found")
    return p


@app.get("/api/participants/{participant_id}")
async def get_participant_endpoint(participant_id: str) -> dict:
    p = get_participant(DB_PATH, participant_id)
    if not p:
        raise HTTPException(404, "Participant not found")
    return p


@app.put("/api/participants/{participant_id}/session")
async def link_session_endpoint(participant_id: str, body: LinkSession) -> dict:
    p = get_participant(DB_PATH, participant_id)
    if not p:
        raise HTTPException(404, "Participant not found")

    # Find the old holder BEFORE link_session() unlinks them, so we can
    # notify the analytics API to clear its cache for the displaced participant.
    old_holder = get_participant_by_session(DB_PATH, body.session_id)
    old_participant_id = (
        old_holder["participant_id"]
        if old_holder and old_holder["participant_id"] != participant_id
        else None
    )

    try:
        link_session(DB_PATH, participant_id, body.session_id)
    except ValueError as exc:
        # Participant is already linked to a different active session.
        # Return 409 Conflict so the caller (resolve_participant in
        # web_api_client.py) knows to retry with a different participant
        # rather than silently overwriting the existing link.
        logger.warning(
            "link_session_endpoint: conflict for participant %r ↔ session %r: %s",
            participant_id, body.session_id, exc,
        )
        raise HTTPException(status_code=409, detail=str(exc))

    if old_participant_id:
        logger.info(
            "Session reassigned: session=%r  %r → %r",
            body.session_id, old_participant_id, participant_id,
        )
    else:
        logger.info("Session linked: participant=%r ↔ session=%r", participant_id, body.session_id)

    # Notify the analytics API so it clears its participant cache immediately.
    # This allows _resolve_and_link_participant to pick up the link on the very
    # next retry (within seconds) rather than waiting for the cooldown to expire.
    # When this is a reassignment (old_participant_id is set) we also clear the
    # cache for the displaced participant so stale mappings are removed.
    # Fire-and-forget — a failure here must never block the questionnaire response.
    async def _notify_analytics() -> None:
        url = f"{ANALYTICS_API_URL}/api/sessions/{body.session_id}/participant"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
                await client.put(url, json={"participant_id": participant_id})
            logger.debug(
                "link_session_endpoint: notified analytics API to link %r → %r",
                body.session_id, participant_id,
            )
        except Exception as exc:  # noqa: BLE001
            # Analytics API may not be running yet — this is non-fatal.
            logger.debug(
                "link_session_endpoint: could not notify analytics API (%s: %s) — "
                "participant cache will be cleared on next retry cycle",
                type(exc).__name__, exc,
            )

    asyncio.create_task(_notify_analytics())
    return {"ok": True, "reassigned_from": old_participant_id}


@app.put("/api/participants/{participant_id}/done")
async def mark_participant_done_endpoint(participant_id: str) -> dict:
    """Mark a participant as permanently done (session_id = '__done__').

    Called automatically by the analytics server at normal session end so
    the participant cannot be recycled into a future Unity session.
    """
    p = get_participant(DB_PATH, participant_id)
    if not p:
        raise HTTPException(404, "Participant not found")
    mark_participant_done(DB_PATH, participant_id)
    logger.info("Participant marked done: id=%r", participant_id)
    return {"ok": True, "participant_id": participant_id}


@app.delete("/api/participants/{participant_id}/session")
async def unlink_session_endpoint(participant_id: str) -> dict:
    """Restore a participant to the unlinked pool (sets session_id to '').

    Called by the analytics server safety-net path (participant was linked
    but received no records) and by stale-session eviction.  Restores the
    participant to the FIFO unlinked pool so they can be auto-linked to the
    next Unity session.  NOT called at normal session end — after a normal
    session the participant keeps their completed session_id.
    """
    p = get_participant(DB_PATH, participant_id)
    if not p:
        raise HTTPException(404, "Participant not found")
    unlink_session(DB_PATH, participant_id)
    logger.info("Session unlinked: participant=%r", participant_id)
    return {"ok": True, "participant_id": participant_id}


@app.delete("/api/participants/{participant_id}")
async def delete_participant_endpoint(participant_id: str) -> dict:
    delete_participant_data(DB_PATH, participant_id)
    logger.info("Participant deleted: id=%r", participant_id)
    return {"ok": True}


# ── Answers ───────────────────────────────────────────────────────────

@app.post("/api/participants/{participant_id}/answers/{phase}")
async def save_single_answer(participant_id: str, phase: str, body: AnswerSave) -> dict:
    """Save (auto-save) a single answer."""
    p = get_participant(DB_PATH, participant_id)
    if not p:
        raise HTTPException(404, "Participant not found")
    try:
        save_answer(DB_PATH, participant_id, phase, body.question_id, body.answer)
    except Exception as exc:
        logger.exception(
            "DB error saving answer for participant '%s' phase='%s' question='%s': %s",
            participant_id, phase, body.question_id, exc,
        )
        raise HTTPException(500, "Failed to save answer – see server log")
    return {"ok": True}


@app.put("/api/participants/{participant_id}/answers/{phase}")
async def save_bulk_answers(participant_id: str, phase: str, body: AnswersBulkSave) -> dict:
    """Save many answers at once (submit / bulk auto-save)."""
    p = get_participant(DB_PATH, participant_id)
    if not p:
        raise HTTPException(404, "Participant not found")
    try:
        save_answers_bulk(DB_PATH, participant_id, phase, body.answers)
    except Exception as exc:
        logger.exception(
            "DB error bulk-saving %d answers for participant '%s' phase='%s': %s",
            len(body.answers), participant_id, phase, exc,
        )
        raise HTTPException(500, "Failed to save answers – see server log")

    # When the pre-questionnaire is bulk-submitted, register the participant in
    # the car-data system so external tooling can correlate by participant.
    # Fire-and-forget — a failure here must never block the API response.
    if phase == "pre":
        age_group: str = body.answers.get("pre_age_group", "")
        display_name: str = p.get("display_name", "")

        async def _register_cardata_user() -> None:
            payload = {
                "participant_id": participant_id,
                "age_group": age_group,
                "display_name": display_name,
            }
            _max_retries = 5
            _delay = 5.0  # seconds; doubles after each failed attempt, capped at 120 s
            for attempt in range(1, _max_retries + 1):
                try:
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(5.0),
                        verify=False,  # self-signed cert on local network
                    ) as client:
                        resp = await client.post(
                            "https://10.200.130.98:5001/api/cardata/newuser",
                            json=payload,
                        )
                    logger.info(
                        "cardata newuser: participant=%r age_group=%r → HTTP %d (attempt %d/%d)",
                        participant_id, age_group, resp.status_code, attempt, _max_retries,
                    )
                    return  # success – stop retrying
                except Exception as exc:  # noqa: BLE001
                    if attempt < _max_retries:
                        logger.warning(
                            "cardata newuser: attempt %d/%d failed for participant %r "
                            "(%s: %s) — retrying in %.0fs",
                            attempt, _max_retries, participant_id,
                            type(exc).__name__, exc, _delay,
                        )
                        await asyncio.sleep(_delay)
                        _delay = min(_delay * 2, 120.0)
                    else:
                        logger.warning(
                            "cardata newuser: all %d attempts failed for participant %r "
                            "(%s: %s) — giving up",
                            _max_retries, participant_id, type(exc).__name__, exc,
                        )

        asyncio.create_task(_register_cardata_user())

    return {"ok": True}


@app.get("/api/participants/{participant_id}/answers/{phase}")
async def get_answers_endpoint(participant_id: str, phase: str) -> list[dict]:
    """Load saved answers for resume."""
    try:
        return get_answers(DB_PATH, participant_id, phase)
    except Exception as exc:
        logger.exception(
            "DB error loading answers for participant '%s' phase='%s': %s",
            participant_id, phase, exc,
        )
        raise HTTPException(500, "Failed to load answers – see server log")


@app.get("/api/participants/{participant_id}/answers")
async def get_all_answers_endpoint(participant_id: str) -> list[dict]:
    """Load all answers for a participant (both phases)."""
    try:
        return get_answers(DB_PATH, participant_id)
    except Exception as exc:
        logger.exception(
            "DB error loading all answers for participant '%s': %s", participant_id, exc
        )
        raise HTTPException(status_code=503, detail="Failed to load answers – see server log")


@app.get("/api/participants/{participant_id}/progress")
async def get_progress_endpoint(participant_id: str) -> dict:
    return get_progress(DB_PATH, participant_id)


# ── Pulse data ────────────────────────────────────────────────────────

@app.post("/api/pulse", response_model=PulseDataOut, status_code=201)
async def create_pulse_sample(body: PulseDataCreate) -> dict:
    """Receive a heart-rate sample from the analytics ingest server and persist it."""
    try:
        result = insert_pulse_data(DB_PATH, body.session_id, body.unix_ms, body.pulse)
    except ValueError as exc:
        logger.warning("Rejected invalid pulse payload: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception(
            "DB error saving pulse data session=%r unix_ms=%d pulse=%d: %s",
            body.session_id, body.unix_ms, body.pulse, exc,
        )
        raise HTTPException(status_code=500, detail="Failed to save pulse data – see server log")
    return result


@app.get("/api/pulse/{session_id}", response_model=list[PulseDataOut])
async def get_pulse_samples(session_id: str, limit: int = 500) -> list[dict]:
    """Return persisted heart-rate samples for a session."""
    try:
        return get_pulse_data(DB_PATH, session_id, limit)
    except Exception as exc:
        logger.exception("DB error loading pulse data for session %r: %s", session_id, exc)
        raise HTTPException(status_code=503, detail="Failed to load pulse data – see server log")


# ── Health ────────────────────────────────────────────────────────────

@app.get("/api/healthz")
async def healthz() -> dict:
    """Health probe. Includes a lightweight DB check so monitoring can detect a
    broken database even if the service process itself is running."""
    try:
        from live_analytics.questionnaire.db import _connect
        conn = _connect(DB_PATH)  # returns cached pooled connection – do not close
        conn.execute("SELECT 1").fetchone()
        db_ok, db_detail = True, "ok"
    except Exception as exc:
        db_ok, db_detail = False, str(exc)
    return {"status": "ok", "db_ok": db_ok, "db_path": str(DB_PATH), "db_detail": db_detail}


# ── Entry point ───────────────────────────────────────────────────────

def main() -> None:
    # ensure_dirs / init_db are handled by the lifespan context manager.
    uvicorn.run("live_analytics.questionnaire.app:app", host=HOST, port=PORT, log_level=LOG_LEVEL.lower())


if __name__ == "__main__":
    main()
