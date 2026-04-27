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

from live_analytics.questionnaire.config import DB_PATH, HOST, LOG_LEVEL, PORT, ensure_dirs
from live_analytics.questionnaire.db import (
    create_participant,
    delete_participant_data,
    get_answers,
    get_participant,
    get_progress,
    init_db,
    link_session,
    list_participants,
    save_answer,
    save_answers_bulk,
)
from live_analytics.questionnaire.models import AnswerSave, AnswersBulkSave, LinkSession, ParticipantCreate
from live_analytics.questionnaire.questions import QUESTIONNAIRES

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
    return result


@app.get("/api/participants")
async def list_participants_endpoint() -> list[dict]:
    try:
        return list_participants(DB_PATH)
    except Exception as exc:
        logger.exception("DB error listing participants: %s", exc)
        raise HTTPException(status_code=503, detail="Failed to list participants – see server log")


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
    return {"ok": True}


@app.delete("/api/participants/{participant_id}")
async def delete_participant_endpoint(participant_id: str) -> dict:
    delete_participant_data(DB_PATH, participant_id)
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
