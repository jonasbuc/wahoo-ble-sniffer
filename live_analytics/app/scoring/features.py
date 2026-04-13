"""
Feature extraction from raw telemetry windows.

All functions accept a list of TelemetryRecord dicts (or Pydantic objects)
and return scalar features.
"""

from __future__ import annotations

import math
import statistics
from typing import Sequence

from live_analytics.app.models import TelemetryRecord


def steering_variance(records: Sequence[TelemetryRecord], window_sec: float = 3.0) -> float:
    """Variance of steering_angle over the last *window_sec* seconds."""
    if len(records) < 2:
        return 0.0
    cutoff = records[-1].unity_time - window_sec
    vals = [r.steering_angle for r in records if r.unity_time >= cutoff]
    if len(vals) < 2:
        return 0.0
    return statistics.variance(vals)


def hr_delta(records: Sequence[TelemetryRecord], window_sec: float = 10.0) -> float:
    """Absolute change in heart_rate over the last *window_sec* seconds."""
    if len(records) < 2:
        return 0.0
    cutoff = records[-1].unity_time - window_sec
    windowed = [r for r in records if r.unity_time >= cutoff and r.heart_rate > 0]
    if len(windowed) < 2:
        return 0.0
    return abs(windowed[-1].heart_rate - windowed[0].heart_rate)


def head_scan_count(records: Sequence[TelemetryRecord], window_sec: float = 5.0, threshold_deg: float = 15.0) -> int:
    """
    Count distinct head-rotation direction changes exceeding *threshold_deg*
    within the last *window_sec*.  A "scan" is when the yaw changes direction
    by more than the threshold.
    """
    if len(records) < 3:
        return 0
    cutoff = records[-1].unity_time - window_sec
    windowed = [r for r in records if r.unity_time >= cutoff]
    if len(windowed) < 3:
        return 0

    # Approximate yaw from quaternion (atan2 of y component)
    def yaw(r: TelemetryRecord) -> float:
        # Simplified yaw from quaternion
        siny_cosp = 2.0 * (r.head_rot_w * r.head_rot_y + r.head_rot_x * r.head_rot_z)
        cosy_cosp = 1.0 - 2.0 * (r.head_rot_y * r.head_rot_y + r.head_rot_z * r.head_rot_z)
        return math.degrees(math.atan2(siny_cosp, cosy_cosp))

    yaws = [yaw(r) for r in windowed]
    scans = 0
    for i in range(2, len(yaws)):
        d1 = yaws[i - 1] - yaws[i - 2]
        d2 = yaws[i] - yaws[i - 1]
        if d1 * d2 < 0 and abs(d1) > threshold_deg:
            scans += 1
    return scans


def brake_reaction_ms(
    records: Sequence[TelemetryRecord],
    trigger_id: str = "",
) -> float:
    """
    Milliseconds between the first record with *trigger_id* set and the
    first subsequent record where brake_front > 0 or brake_rear > 0.
    Returns 0.0 if no trigger or no brake event found.
    """
    if not trigger_id or len(records) < 2:
        return 0.0

    trigger_time: float | None = None
    for r in records:
        if trigger_time is None:
            if r.trigger_id == trigger_id:
                trigger_time = r.unity_time
        else:
            if r.brake_front > 0 or r.brake_rear > 0:
                return (r.unity_time - trigger_time) * 1000.0
    return 0.0


def mean_speed(records: Sequence[TelemetryRecord], window_sec: float = 5.0) -> float:
    """Mean speed over the last *window_sec* seconds."""
    if not records:
        return 0.0
    cutoff = records[-1].unity_time - window_sec
    vals = [r.speed for r in records if r.unity_time >= cutoff]
    return statistics.mean(vals) if vals else 0.0
