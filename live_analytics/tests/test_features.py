"""
Tests for live_analytics.app.scoring.features
"""

from __future__ import annotations

import pytest

from live_analytics.app.models import TelemetryRecord
from live_analytics.app.scoring.features import (
    brake_reaction_ms,
    compute_features,
    head_scan_count,
    hr_delta,
    mean_speed,
    steering_variance,
)


def _rec(
    unity_time: float = 0.0,
    steering_angle: float = 0.0,
    heart_rate: float = 70.0,
    speed: float = 5.0,
    brake_front: int = 0,
    brake_rear: int = 0,
    trigger_id: str = "",
    head_rot_y: float = 0.0,
    head_rot_w: float = 1.0,
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
        head_rot_y=head_rot_y,
        head_rot_w=head_rot_w,
    )


class TestSteeringVariance:
    def test_empty(self) -> None:
        assert steering_variance([], window_sec=3.0) == 0.0

    def test_single(self) -> None:
        assert steering_variance([_rec(steering_angle=10.0)]) == 0.0

    def test_constant(self) -> None:
        recs = [_rec(unity_time=i, steering_angle=5.0) for i in range(5)]
        assert steering_variance(recs, window_sec=10) == 0.0

    def test_varying(self) -> None:
        recs = [
            _rec(unity_time=0.0, steering_angle=0.0),
            _rec(unity_time=1.0, steering_angle=10.0),
            _rec(unity_time=2.0, steering_angle=-10.0),
        ]
        assert steering_variance(recs, window_sec=5.0) > 0


class TestHrDelta:
    def test_empty(self) -> None:
        assert hr_delta([]) == 0.0

    def test_no_change(self) -> None:
        recs = [_rec(unity_time=i, heart_rate=72.0) for i in range(5)]
        assert hr_delta(recs, window_sec=10.0) == 0.0

    def test_increase(self) -> None:
        recs = [
            _rec(unity_time=0.0, heart_rate=70.0),
            _rec(unity_time=5.0, heart_rate=90.0),
        ]
        assert hr_delta(recs, window_sec=10.0) == pytest.approx(20.0)


class TestHeadScanCount:
    def test_empty(self) -> None:
        assert head_scan_count([]) == 0

    def test_no_scans(self) -> None:
        recs = [_rec(unity_time=i, head_rot_y=0.0) for i in range(5)]
        assert head_scan_count(recs, window_sec=10.0) == 0


class TestBrakeReactionMs:
    def test_no_trigger(self) -> None:
        recs = [_rec(unity_time=i) for i in range(5)]
        assert brake_reaction_ms(recs, trigger_id="") == 0.0

    def test_trigger_then_brake(self) -> None:
        recs = [
            _rec(unity_time=0.0, trigger_id="stop_sign"),
            _rec(unity_time=0.5, trigger_id="stop_sign"),
            _rec(unity_time=1.0, brake_front=100, trigger_id="stop_sign"),
        ]
        result = brake_reaction_ms(recs, trigger_id="stop_sign")
        assert result == pytest.approx(1000.0)

    def test_no_brake_after_trigger(self) -> None:
        recs = [
            _rec(unity_time=0.0, trigger_id="stop_sign"),
            _rec(unity_time=1.0, trigger_id="stop_sign"),
        ]
        assert brake_reaction_ms(recs, trigger_id="stop_sign") == 0.0


class TestMeanSpeed:
    def test_empty(self) -> None:
        assert mean_speed([]) == 0.0

    def test_constant(self) -> None:
        recs = [_rec(unity_time=i, speed=10.0) for i in range(5)]
        assert mean_speed(recs) == pytest.approx(10.0)


class TestComputeFeatures:
    """compute_features() must return the same values as calling each
    individual feature function, and must do so in a single pass."""

    def _make_recs(self) -> list:
        return [
            _rec(unity_time=0.0, speed=10.0, heart_rate=70, steering_angle=0.0, brake_front=0, trigger_id=""),
            _rec(unity_time=1.0, speed=20.0, heart_rate=80, steering_angle=5.0, brake_front=0, trigger_id="go"),
            _rec(unity_time=2.0, speed=15.0, heart_rate=75, steering_angle=-5.0, brake_front=100, trigger_id="go"),
        ]

    def test_empty_returns_zeros(self) -> None:
        f = compute_features([])
        assert f.mean_speed_5s == 0.0
        assert f.steering_variance_3s == 0.0
        assert f.hr_delta_10s == 0.0
        assert f.head_scan_count_5s == 0
        assert f.brake_reaction_ms == 0.0

    def test_mean_speed_matches(self) -> None:
        recs = self._make_recs()
        f = compute_features(recs)
        assert f.mean_speed_5s == pytest.approx(mean_speed(recs))

    def test_steering_variance_matches(self) -> None:
        recs = self._make_recs()
        f = compute_features(recs)
        assert f.steering_variance_3s == pytest.approx(steering_variance(recs))

    def test_hr_delta_matches(self) -> None:
        recs = self._make_recs()
        f = compute_features(recs)
        assert f.hr_delta_10s == pytest.approx(hr_delta(recs))

    def test_head_scans_matches(self) -> None:
        recs = self._make_recs()
        f = compute_features(recs)
        assert f.head_scan_count_5s == head_scan_count(recs)

    def test_brake_ms_matches(self) -> None:
        recs = self._make_recs()
        f = compute_features(recs)
        assert f.brake_reaction_ms == pytest.approx(brake_reaction_ms(recs, trigger_id="go"))

    def test_returns_windowfeatures_type(self) -> None:
        from live_analytics.app.scoring.features import WindowFeatures

        recs = self._make_recs()
        assert isinstance(compute_features(recs), WindowFeatures)

