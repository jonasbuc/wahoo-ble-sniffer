"""
Pydantic models for the questionnaire API.

``ParticipantCreate`` / ``ParticipantOut`` are used for the participant registry
(test-person tracking).  ``AnswerSave`` and ``AnswersBulkSave`` cover the two
save strategies: single-question autosave (triggered on every blur event in the
SPA) and bulk submission at the end of a phase.  ``QuestionDef`` and
``QuestionnaireDef`` are the structures served to the frontend when it requests
the question definitions for a given phase.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ParticipantCreate(BaseModel):
    participant_id: str = Field(..., min_length=1, description="Unique test-person ID")
    display_name: str = ""
    session_id: str = ""
    metadata: dict = Field(default_factory=dict)


class ParticipantOut(BaseModel):
    participant_id: str
    display_name: str
    session_id: str
    created_at: str
    updated_at: str
    metadata: str  # JSON string from DB


class LinkSession(BaseModel):
    session_id: str = Field(..., min_length=1)


class AnswerSave(BaseModel):
    question_id: str = Field(..., min_length=1)
    answer: Any


class AnswersBulkSave(BaseModel):
    answers: dict[str, Any] = Field(..., description="Map of question_id → answer value")


class QuestionDef(BaseModel):
    """Definition of a single questionnaire question (served to the frontend)."""
    id: str
    type: str = "text"  # text | number | radio | checkbox | scale | textarea
    label: str
    required: bool = True
    options: list[str] = Field(default_factory=list)
    min_value: Optional[int] = None
    max_value: Optional[int] = None
    placeholder: str = ""
    section: str = ""


class QuestionnaireDef(BaseModel):
    """Full questionnaire definition for a phase."""
    phase: str  # "pre" | "post"
    title: str
    description: str = ""
    questions: list[QuestionDef]


# ── Pulse data ────────────────────────────────────────────────────────

class PulseDataCreate(BaseModel):
    """Payload sent from the analytics ingest server to POST /api/pulse.

    The questionnaire service resolves ``participant_id`` automatically from
    ``session_id``.  Callers do not need to supply it.
    """
    session_id: str = Field(..., min_length=1, description="Analytics session identifier")
    unix_ms: int = Field(..., description="Timestamp of the sample (ms since Unix epoch, UTC)")
    pulse: int = Field(..., gt=0, le=300, description="Heart-rate in BPM (1–300)")


class PulseDataOut(BaseModel):
    """Response from POST /api/pulse."""
    id: int
    session_id: str
    participant_id: Optional[str]
    unix_ms: int
    pulse: int
    created_at: str
