"""
Tests for live_analytics.app.scoring.rules
"""

from __future__ import annotations

import pytest

from live_analytics.app.models import TelemetryRecord
from live_analytics.app.scoring.rules import compute_scores


def _rec(
    unity_time: float = 0.0,
    steering_angle: float = 0.0,
    heart_rate: float = 70.0,
    speed: float = 5.0,
    brake_front: int = 0,
    brake_rear: int = 0,
    trigger_id: str = "",
) -> TelemetryRecord:
    return TelemetryRecord(
        session_id="test",
        unix_ms=int(unity_time * 1000),
        unity_time=unity_time,
        steering_angle=steering_angle,
        heart_rate=heart_rate,
        speed=speed,
        brake_front=brake_front,
        brake_rear=brake_rear,
        trigger_id=trigger_id,
    )


class TestComputeScores:
    def test_empty(self) -> None:
        result = compute_scores([])
        assert result.stress_score == 0.0
        assert result.risk_score == 0.0

    def test_calm_baseline(self) -> None:
        recs = [_rec(unity_time=i * 0.05, heart_rate=70.0, speed=3.0, steering_angle=0.0) for i in range(100)]
        result = compute_scores(recs)
        assert result.stress_score < 10.0
        assert result.risk_score < 20.0

    def test_high_hr_delta_raises_stress(self) -> None:
        recs = [
            _rec(unity_time=0.0, heart_rate=70.0),
            *[_rec(unity_time=i * 0.1, heart_rate=70.0 + i * 2) for i in range(1, 50)],
        ]
        result = compute_scores(recs)
        assert result.stress_score > 20.0  # elevated

    def test_high_speed_raises_risk(self) -> None:
        recs = [_rec(unity_time=i * 0.05, speed=15.0) for i in range(100)]
        result = compute_scores(recs)
        assert result.risk_score > 10.0  # speed above threshold

    def test_scores_bounded(self) -> None:
        """Scores must stay within [0, 100]."""
        recs = [
            _rec(unity_time=i * 0.05, speed=50.0, heart_rate=70.0 + i, steering_angle=i * 10)
            for i in range(100)
        ]
        result = compute_scores(recs)
        assert 0.0 <= result.stress_score <= 100.0
        assert 0.0 <= result.risk_score <= 100.0

    def test_brake_reaction_detected(self) -> None:
        recs = [
            _rec(unity_time=0.0, trigger_id="red_light"),
            _rec(unity_time=0.3, trigger_id="red_light"),
            _rec(unity_time=0.8, trigger_id="red_light", brake_front=200),
        ]
        result = compute_scores(recs)
        assert result.brake_reaction_ms > 0
