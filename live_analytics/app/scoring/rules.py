"""
Rule-based scoring engine.

This is the default live scoring pipeline.  It combines feature values
into stress_score and risk_score using simple heuristic rules that can
be tuned via the constants below.
"""

from __future__ import annotations

from typing import Sequence

from live_analytics.app.models import ScoringResult, TelemetryRecord
from live_analytics.app.scoring.features import (
    brake_reaction_ms,
    head_scan_count,
    hr_delta,
    mean_speed,
    steering_variance,
)

# ── Tuning constants ─────────────────────────────────────────────────
HR_BASELINE: float = 70.0  # resting HR assumed when no calibration
HR_STRESS_CEILING: float = 40.0  # hr_delta that maps to 100 % stress
SPEED_RISK_THRESHOLD: float = 8.0  # m/s above which speed adds risk
STEER_VAR_RISK_CEILING: float = 200.0  # steering variance mapped to 100 %
SCAN_BONUS_CAP: int = 6  # scans above this don't reduce risk further


def compute_scores(
    records: Sequence[TelemetryRecord],
    hr_baseline: float = HR_BASELINE,
) -> ScoringResult:
    """
    Evaluate the full scoring result from a sliding window of records.

    Returns a :class:`ScoringResult` with all six metrics populated.
    """
    if not records:
        return ScoringResult()

    # ── Feature extraction ────────────────────────────────────────────
    sv = steering_variance(records, window_sec=3.0)
    hrd = hr_delta(records, window_sec=10.0)
    hsc = head_scan_count(records, window_sec=5.0)
    avg_speed = mean_speed(records, window_sec=5.0)

    # Try to detect a recent trigger for brake-reaction
    last_trigger = ""
    for r in reversed(list(records)):
        if r.trigger_id:
            last_trigger = r.trigger_id
            break
    brm = brake_reaction_ms(records, trigger_id=last_trigger)

    # ── Stress score (0–100) ──────────────────────────────────────────
    # Primarily driven by heart-rate delta + steering variance
    hr_component = min(hrd / HR_STRESS_CEILING, 1.0) * 60.0
    steer_component = min(sv / STEER_VAR_RISK_CEILING, 1.0) * 40.0
    stress = min(hr_component + steer_component, 100.0)

    # ── Risk score (0–100) ────────────────────────────────────────────
    # High speed, high steering variance, low scanning → higher risk
    speed_risk = min(max(avg_speed - SPEED_RISK_THRESHOLD, 0) / 6.0, 1.0) * 35.0
    steer_risk = min(sv / STEER_VAR_RISK_CEILING, 1.0) * 35.0
    scan_reduction = min(hsc, SCAN_BONUS_CAP) / SCAN_BONUS_CAP * 20.0
    brake_penalty = min(brm / 2000.0, 1.0) * 10.0 if brm > 0 else 0.0
    risk = min(max(speed_risk + steer_risk - scan_reduction + brake_penalty, 0.0), 100.0)

    return ScoringResult(
        stress_score=round(stress, 2),
        risk_score=round(risk, 2),
        brake_reaction_ms=round(brm, 1),
        head_scan_count_5s=hsc,
        steering_variance_3s=round(sv, 4),
        hr_delta_10s=round(hrd, 2),
    )
