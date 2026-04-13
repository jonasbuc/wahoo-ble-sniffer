"""
Questionnaire Service – FastAPI application.

Hosts:
  • REST API for participant management & answer CRUD
  • Static SPA frontend served from ./static/
  • Runs on QS_PORT (default 8090)
"""

from __future__ import annotations

import logging
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

# ── App ───────────────────────────────────────────────────────────────
app = FastAPI(title="Questionnaire Service", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.on_event("startup")
async def _startup() -> None:
    ensure_dirs()
    init_db(DB_PATH)
    logger.info("Questionnaire service ready on %s:%d", HOST, PORT)


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
    return create_participant(DB_PATH, body.participant_id, body.display_name, body.session_id, body.metadata)


@app.get("/api/participants")
async def list_participants_endpoint() -> list[dict]:
    return list_participants(DB_PATH)


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
    save_answer(DB_PATH, participant_id, phase, body.question_id, body.answer)
    return {"ok": True}


@app.put("/api/participants/{participant_id}/answers/{phase}")
async def save_bulk_answers(participant_id: str, phase: str, body: AnswersBulkSave) -> dict:
    """Save many answers at once (submit / bulk auto-save)."""
    p = get_participant(DB_PATH, participant_id)
    if not p:
        raise HTTPException(404, "Participant not found")
    save_answers_bulk(DB_PATH, participant_id, phase, body.answers)
    return {"ok": True}


@app.get("/api/participants/{participant_id}/answers/{phase}")
async def get_answers_endpoint(participant_id: str, phase: str) -> list[dict]:
    """Load saved answers for resume."""
    return get_answers(DB_PATH, participant_id, phase)


@app.get("/api/participants/{participant_id}/answers")
async def get_all_answers_endpoint(participant_id: str) -> list[dict]:
    """Load all answers for a participant (both phases)."""
    return get_answers(DB_PATH, participant_id)


@app.get("/api/participants/{participant_id}/progress")
async def get_progress_endpoint(participant_id: str) -> dict:
    return get_progress(DB_PATH, participant_id)


# ── Health ────────────────────────────────────────────────────────────

@app.get("/api/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


# ── Entry point ───────────────────────────────────────────────────────

def main() -> None:
    ensure_dirs()
    init_db(DB_PATH)
    uvicorn.run("live_analytics.questionnaire.app:app", host=HOST, port=PORT, log_level=LOG_LEVEL.lower())


if __name__ == "__main__":
    main()
