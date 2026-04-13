"""
Tests for ingest payload validation (Pydantic schemas).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from live_analytics.app.models import (
    ScoringResult,
    TelemetryBatch,
    TelemetryRecord,
)


class TestTelemetryRecord:
    def test_valid_minimal(self) -> None:
        rec = TelemetryRecord(session_id="abc", unix_ms=1700000000000, unity_time=1.0)
        assert rec.session_id == "abc"
        assert rec.record_type == "gameplay"

    def test_all_fields(self) -> None:
        rec = TelemetryRecord(
            session_id="s1",
            unix_ms=1700000000000,
            unity_time=10.5,
            scenario_id="intersection",
            trigger_id="stop_sign",
            speed=8.5,
            steering_angle=-3.2,
            brake_front=128,
            brake_rear=64,
            heart_rate=85.0,
            head_pos_x=1.0,
            head_pos_y=1.7,
            head_pos_z=0.0,
            head_rot_x=0.0,
            head_rot_y=0.1,
            head_rot_z=0.0,
            head_rot_w=1.0,
            record_type="headpose",
        )
        assert rec.speed == 8.5
        assert rec.record_type == "headpose"

    def test_missing_required_field(self) -> None:
        with pytest.raises(ValidationError):
            TelemetryRecord(unix_ms=123, unity_time=1.0)  # type: ignore[call-arg]

    def test_extra_fields_ignored(self) -> None:
        data = {"session_id": "x", "unix_ms": 0, "unity_time": 0, "extra_field": 42}
        rec = TelemetryRecord(**data)
        assert rec.session_id == "x"


class TestTelemetryBatch:
    def test_valid_batch(self) -> None:
        rec = TelemetryRecord(session_id="s1", unix_ms=0, unity_time=0.0)
        batch = TelemetryBatch(records=[rec], count=1, sent_at="2024-01-01T00:00:00Z")
        assert batch.count == 1

    def test_empty_records(self) -> None:
        batch = TelemetryBatch(records=[], count=0, sent_at="2024-01-01T00:00:00Z")
        assert len(batch.records) == 0


class TestScoringResult:
    def test_defaults(self) -> None:
        s = ScoringResult()
        assert s.stress_score == 0.0
        assert s.risk_score == 0.0

    def test_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ScoringResult(stress_score=101.0)  # > 100

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ScoringResult(risk_score=-1.0)
