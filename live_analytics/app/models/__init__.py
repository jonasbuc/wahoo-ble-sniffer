"""
Pydantic schemas for telemetry payloads and API responses.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ── Ingest payloads (from Unity) ──────────────────────────────────────

class TelemetryRecord(BaseModel):
    """Single telemetry sample from Unity."""

    session_id: str
    unix_ms: int
    unity_time: float
    scenario_id: str = ""
    trigger_id: str = ""

    speed: float = 0.0
    steering_angle: float = 0.0
    brake_front: int = 0
    brake_rear: int = 0
    heart_rate: float = 0.0

    head_pos_x: float = 0.0
    head_pos_y: float = 0.0
    head_pos_z: float = 0.0

    head_rot_x: float = 0.0
    head_rot_y: float = 0.0
    head_rot_z: float = 0.0
    head_rot_w: float = 1.0

    record_type: str = "gameplay"  # "gameplay" | "headpose"


class TelemetryBatch(BaseModel):
    """Batch envelope sent over WebSocket."""

    records: list[TelemetryRecord]
    count: int
    sent_at: str


# ── Scoring output ────────────────────────────────────────────────────

class ScoringResult(BaseModel):
    """Output of the rule engine for a given window."""

    stress_score: float = Field(0.0, ge=0, le=100)
    risk_score: float = Field(0.0, ge=0, le=100)
    brake_reaction_ms: float = 0.0
    head_scan_count_5s: int = 0
    steering_variance_3s: float = 0.0
    hr_delta_10s: float = 0.0


# ── API response models ──────────────────────────────────────────────

class SessionSummary(BaseModel):
    """Lightweight session metadata."""

    session_id: str
    start_unix_ms: int
    end_unix_ms: Optional[int] = None
    scenario_id: str = ""
    record_count: int = 0


class SessionDetail(SessionSummary):
    """Full session detail returned by the API."""

    latest_scores: Optional[ScoringResult] = None


class LiveLatest(BaseModel):
    """Snapshot of the latest live state."""

    session_id: str
    unix_ms: int
    speed: float
    heart_rate: float
    scores: ScoringResult


class LiveFeedback(BaseModel):
    """Feedback pushed back to Unity."""

    stress_score: float = 0.0
    risk_score: float = 0.0
    event_tag: str = ""
    message: str = ""
