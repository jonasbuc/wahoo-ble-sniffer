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

from pydantic import BaseModel, Field, field_validator


class ParticipantCreate(BaseModel):
    """Request body for ``POST /api/participants``.

    ``participant_id`` must be a positive integer (supplied as a string).
    Leading zeros are stripped and the canonical integer form is stored in the
    database.  ``display_name`` and ``session_id`` are optional at creation
    time and can be set later via PATCH or the link-session endpoint.
    """
    participant_id: str = Field(..., min_length=1, description="Unique test-person ID — must be a positive integer")
    display_name: str = ""
    session_id: str = ""
    metadata: dict = Field(default_factory=dict)

    @field_validator("participant_id")
    @classmethod
    def must_be_positive_integer(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped.isdigit() or int(stripped) < 1:
            raise ValueError(
                f"participant_id must be a positive integer (e.g. 1, 2, 42), got: {v!r}"
            )
        # Normalise: remove any leading zeros, return canonical form.
        return str(int(stripped))


class ParticipantOut(BaseModel):
    """Read model returned by GET/POST participant endpoints.

    ``metadata`` is stored as a raw JSON string in the database and is
    surfaced here as-is.  Callers that need a structured object should
    call ``json.loads(metadata)``.
    """
    participant_id: str
    display_name: str
    session_id: str
    created_at: str
    updated_at: str
    metadata: str  # JSON string from DB


class LinkSession(BaseModel):
    """Request body for ``PUT /api/participants/{id}/session``.

    Links a questionnaire participant to a live analytics session so that
    pulse logs, answer records, and session metrics can be joined on
    ``session_id`` in offline analysis.
    """
    session_id: str = Field(..., min_length=1)


class AnswerSave(BaseModel):
    """Request body for single-question autosave (``POST /api/answers``).

    Triggered on every ``blur`` event in the questionnaire SPA so that
    partial progress is persisted even if the participant closes the tab
    before submitting.
    """
    question_id: str = Field(..., min_length=1)
    answer: Any


class AnswersBulkSave(BaseModel):
    """Request body for bulk-submission at the end of a questionnaire phase.

    All answers for a phase are submitted in one call.  The server upserts
    each ``question_id → answer`` pair, so re-submitting the same phase is
    idempotent and overwrites previous autosave values.
    """
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
