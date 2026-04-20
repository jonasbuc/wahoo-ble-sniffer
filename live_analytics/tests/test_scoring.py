"""
Tests for the scoring engine (rules.py + features.py).

Covers:
  - Empty input
  - Single record
  - Realistic windows
  - Edge cases (zero HR, no triggers)
"""

from __future__ import annotations

import pytest

from live_analytics.app.models import ScoringResult, TelemetryRecord
from live_analytics.app.scoring.rules import compute_scores
from live_analytics.app.scoring.features import (
    brake_reaction_ms,
    head_scan_count,
    hr_delta,
    mean_speed,
    steering_variance,
)


def _rec(
    t: float,
    speed: float = 5.0,
    hr: float = 70.0,
    steer: float = 0.0,
    brake_f: int = 0,
    trigger: str = "",
) -> TelemetryRecord:
    return TelemetryRecord(
        session_id="test",
        unix_ms=int(t * 1000),
        unity_time=t,
        speed=speed,
        heart_rate=hr,
        steering_angle=steer,
        brake_front=brake_f,
        trigger_id=trigger,
    )


class TestFeatures:
    def test_steering_variance_empty(self):
        assert steering_variance([]) == 0.0

    def test_steering_variance_single(self):
        assert steering_variance([_rec(1.0, steer=10.0)]) == 0.0

    def test_steering_variance_constant(self):
        recs = [_rec(t, steer=5.0) for t in range(10)]
        assert steering_variance(recs) == pytest.approx(0.0)

    def test_steering_variance_varies(self):
        recs = [_rec(float(t), steer=float(t * 10)) for t in range(5)]
        assert steering_variance(recs) > 0.0

    def test_hr_delta_empty(self):
        assert hr_delta([]) == 0.0

    def test_hr_delta_constant(self):
        recs = [_rec(float(t), hr=70.0) for t in range(10)]
        assert hr_delta(recs) == pytest.approx(0.0)

    def test_hr_delta_rising(self):
        recs = [_rec(float(t), hr=70.0 + t) for t in range(10)]
        assert hr_delta(recs) == pytest.approx(9.0)

    def test_hr_delta_ignores_zero_hr(self):
        recs = [_rec(0.0, hr=0.0), _rec(1.0, hr=0.0), _rec(2.0, hr=80.0)]
        # Only the last record has hr > 0, so delta should be 0
        assert hr_delta(recs) == pytest.approx(0.0)

    def test_head_scan_count_empty(self):
        assert head_scan_count([]) == 0

    def test_mean_speed_empty(self):
        assert mean_speed([]) == 0.0

    def test_mean_speed_uniform(self):
        recs = [_rec(float(t), speed=10.0) for t in range(5)]
        assert mean_speed(recs) == pytest.approx(10.0)

    def test_brake_reaction_no_trigger(self):
        recs = [_rec(float(t)) for t in range(5)]
        assert brake_reaction_ms(recs, trigger_id="") == 0.0

    def test_brake_reaction_with_trigger(self):
        recs = [
            _rec(0.0),
            _rec(1.0, trigger="stop_sign"),
            _rec(2.0),
            _rec(3.0, brake_f=100),  # brake at t=3
        ]
        ms = brake_reaction_ms(recs, trigger_id="stop_sign")
        assert ms == pytest.approx(2000.0)  # 3.0 - 1.0 = 2s = 2000ms

    def test_brake_reaction_no_brake(self):
        """If trigger exists but no brake event follows, return 0."""
        recs = [_rec(0.0), _rec(1.0, trigger="stop_sign"), _rec(2.0)]
        assert brake_reaction_ms(recs, trigger_id="stop_sign") == 0.0


class TestComputeScores:
    def test_empty_returns_zeroes(self):
        result = compute_scores([])
        assert result.stress_score == 0.0
        assert result.risk_score == 0.0

    def test_single_record(self):
        result = compute_scores([_rec(0.0)])
        assert isinstance(result, ScoringResult)
        assert 0.0 <= result.stress_score <= 100.0
        assert 0.0 <= result.risk_score <= 100.0

    def test_scores_bounded(self):
        """Scores must always be in [0, 100]."""
        recs = [_rec(float(t), speed=50.0, hr=200.0, steer=float(t * 100)) for t in range(20)]
        result = compute_scores(recs)
        assert 0.0 <= result.stress_score <= 100.0
        assert 0.0 <= result.risk_score <= 100.0

    def test_high_hr_increases_stress(self):
        low_hr = [_rec(float(t), hr=70.0) for t in range(20)]
        high_hr = [_rec(float(t), hr=70.0 + t * 3) for t in range(20)]
        s_low = compute_scores(low_hr)
        s_high = compute_scores(high_hr)
        assert s_high.stress_score >= s_low.stress_score

    def test_all_fields_populated(self):
        recs = [_rec(float(t), speed=5.0 + t, hr=70.0, steer=float(t)) for t in range(20)]
        result = compute_scores(recs)
        assert isinstance(result.brake_reaction_ms, float)
        assert isinstance(result.head_scan_count_5s, int)
        assert isinstance(result.steering_variance_3s, float)
        assert isinstance(result.hr_delta_10s, float)
