"""
Questionnaire Service – FastAPI application.

Hosts:
  • REST API for participant management & answer CRUD
  • Static SPA frontend served from ./static/
  • Runs on QS_PORT (default 8090)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from live_analytics.questionnaire.config import DB_PATH, HOST, LOG_LEVEL, PARTICIPANTS_DIR, PORT, ensure_dirs
from live_analytics.questionnaire.db import (
    create_participant,
    delete_participant_data,
    get_answers,
    get_participant,
    get_participant_by_session,
    get_progress,
    get_pulse_data,
    init_db,
    insert_pulse_data,
    link_session,
    list_participants,
    save_answer,
    save_answers_bulk,
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
    link_session(DB_PATH, participant_id, body.session_id)
    logger.info("Session linked: participant=%r ↔ session=%r", participant_id, body.session_id)
    return {"ok": True}


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
