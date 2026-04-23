"""
Feature extraction from raw telemetry windows.

All functions accept a list of TelemetryRecord dicts (or Pydantic objects)
and return scalar features.

``compute_features()`` is the preferred entry-point: it filters the window
*once* and computes every feature in a single pass, rather than having each
function independently iterate over the same list.
"""

from __future__ import annotations

import math
import statistics
from typing import Sequence

from live_analytics.app.models import TelemetryRecord


# ── Individual feature functions (kept for external / test use) ──────

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

    yaws = [_yaw(r) for r in windowed]
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
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


# ── Helpers ───────────────────────────────────────────────────────────

def _yaw(r: TelemetryRecord) -> float:
    """Approximate yaw (degrees) from a unit quaternion."""
    siny_cosp = 2.0 * (r.head_rot_w * r.head_rot_y + r.head_rot_x * r.head_rot_z)
    cosy_cosp = 1.0 - 2.0 * (r.head_rot_y * r.head_rot_y + r.head_rot_z * r.head_rot_z)
    return math.degrees(math.atan2(siny_cosp, cosy_cosp))


# ── Batch computation (preferred hot path) ────────────────────────────

class WindowFeatures:
    """All features computed from a single window scan."""
    __slots__ = (
        "steering_variance_3s",
        "hr_delta_10s",
        "head_scan_count_5s",
        "mean_speed_5s",
        "brake_reaction_ms",
    )

    def __init__(
        self,
        sv: float,
        hrd: float,
        hsc: int,
        spd: float,
        brm: float,
    ) -> None:
        self.steering_variance_3s = sv
        self.hr_delta_10s = hrd
        self.head_scan_count_5s = hsc
        self.mean_speed_5s = spd
        self.brake_reaction_ms = brm


def compute_features(
    records: Sequence[TelemetryRecord],
    steer_window: float = 3.0,
    hr_window: float = 10.0,
    scan_window: float = 5.0,
    speed_window: float = 5.0,
    scan_threshold_deg: float = 15.0,
) -> WindowFeatures:
    """
    Compute all scoring features in a single pass over *records*.

    This avoids the 5 independent O(n) list-comprehension passes that would
    occur if each feature function were called separately.  The window is
    iterated once; per-feature accumulators collect what they need.

    Returns a :class:`WindowFeatures` with all five scalars populated.
    """
    if not records:
        return WindowFeatures(0.0, 0.0, 0, 0.0, 0.0)

    last_t = records[-1].unity_time

    # Cutoff times for each sub-window
    steer_cut = last_t - steer_window
    hr_cut = last_t - hr_window
    scan_cut = last_t - scan_window
    speed_cut = last_t - speed_window

    # Accumulators
    steer_vals: list[float] = []
    hr_vals: list[float] = []  # (unity_time, heart_rate) where hr > 0
    hr_times: list[float] = []
    scan_recs: list[TelemetryRecord] = []
    speed_vals: list[float] = []

    # Trigger detection for brake reaction.
    # We want the reaction time from the *first* trigger event in the window
    # to the *next* brake event after it.  Tracking only last_trigger means
    # we'd miss early triggers if a later trigger also appears.  Instead,
    # record the time of the *first* trigger seen and whether a brake already
    # fired after it.
    first_trigger_time: float | None = None
    first_trigger_id: str = ""
    brm = 0.0

    for r in records:
        t = r.unity_time

        if t >= steer_cut:
            steer_vals.append(r.steering_angle)

        if t >= hr_cut and r.heart_rate > 0:
            hr_vals.append(r.heart_rate)
            hr_times.append(t)

        if t >= scan_cut:
            scan_recs.append(r)

        if t >= speed_cut:
            speed_vals.append(r.speed)

        # Track first trigger for brake reaction (only set once)
        if r.trigger_id and first_trigger_time is None:
            first_trigger_id = r.trigger_id
            first_trigger_time = t

    # ── Steering variance ─────────────────────────────────────────────
    sv = statistics.variance(steer_vals) if len(steer_vals) >= 2 else 0.0

    # ── HR delta ──────────────────────────────────────────────────────
    hrd = abs(hr_vals[-1] - hr_vals[0]) if len(hr_vals) >= 2 else 0.0

    # ── Head scan count ───────────────────────────────────────────────
    hsc = 0
    if len(scan_recs) >= 3:
        yaws = [_yaw(r) for r in scan_recs]
        for i in range(2, len(yaws)):
            d1 = yaws[i - 1] - yaws[i - 2]
            d2 = yaws[i] - yaws[i - 1]
            if d1 * d2 < 0 and abs(d1) > scan_threshold_deg:
                hsc += 1

    # ── Mean speed ────────────────────────────────────────────────────
    avg_speed = sum(speed_vals) / len(speed_vals) if speed_vals else 0.0

    # ── Brake reaction ────────────────────────────────────────────────
    # Use first_trigger_time so the reaction window starts from the actual
    # trigger event rather than re-scanning the whole record list.
    if first_trigger_id and first_trigger_time is not None:
        # Find the first brake event *after* the trigger in the original list.
        for r in records:
            if r.unity_time <= first_trigger_time:
                continue
            if r.brake_front > 0 or r.brake_rear > 0:
                brm = (r.unity_time - first_trigger_time) * 1000.0
                break

    return WindowFeatures(sv, hrd, hsc, avg_speed, brm)

