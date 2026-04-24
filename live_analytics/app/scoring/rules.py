"""
Rule-based scoring engine.

This is the default live scoring pipeline.  It combines feature values
into stress_score and risk_score using simple heuristic rules that can
be tuned via the constants below.

Design rationale
----------------
A rule-based approach was chosen over an ML model because:
  1. Training data is limited during initial deployment.
  2. The rules are explainable — researchers can understand why a score is high.
  3. Constants can be adjusted without retraining.

See ``anomaly.py`` for an IsolationForest stub to replace or supplement this
pipeline once sufficient session data has been collected.
"""

from __future__ import annotations

from typing import Sequence

from live_analytics.app.models import ScoringResult, TelemetryRecord
from live_analytics.app.scoring.features import compute_features

# ── Tuning constants ─────────────────────────────────────────────────
# These are the primary levers for calibrating scoring sensitivity.
# Increase HR_STRESS_CEILING to make high HR matter less (wider dynamic range).
# Increase SPEED_RISK_THRESHOLD if riders routinely exceed 8 m/s without elevated risk.
# Decrease STEER_VAR_RISK_CEILING if subtle wobble should count more.
HR_BASELINE: float = 70.0          # assumed resting HR when no per-rider calibration is available
HR_STRESS_CEILING: float = 40.0    # HR delta (BPM above baseline) that maps to 100 % of the HR stress component
SPEED_RISK_THRESHOLD: float = 8.0  # m/s above which speed begins contributing to risk score
STEER_VAR_RISK_CEILING: float = 200.0  # steering angle variance mapped to 100 % of its risk component
SCAN_BONUS_CAP: int = 6            # additional head scans beyond this threshold give no further risk reduction


def compute_scores(
    records: Sequence[TelemetryRecord],
    hr_baseline: float = HR_BASELINE,
) -> ScoringResult:
    """
    Evaluate the full scoring result from a sliding window of records.

    All features are extracted in a single pass via ``compute_features()``.
    Returns a :class:`ScoringResult` with all six metrics populated.
    """
    if not records:
        return ScoringResult()

    # ── Feature extraction (single pass) ─────────────────────────────
    f = compute_features(records)

    sv = f.steering_variance_3s
    hrd = f.hr_delta_10s
    hsc = f.head_scan_count_5s
    avg_speed = f.mean_speed_5s
    brm = f.brake_reaction_ms

    # ── Stress score (0–100) ──────────────────────────────────────────
    # Weighted sum: HR delta contributes 60 %, steering variance 40 %.
    # Rationale: elevated HR is the primary physiological stress indicator;
    # steering variance is a secondary behavioural signal.
    hr_component = min(hrd / HR_STRESS_CEILING, 1.0) * 60.0
    steer_component = min(sv / STEER_VAR_RISK_CEILING, 1.0) * 40.0
    stress = min(hr_component + steer_component, 100.0)

    # ── Risk score (0–100) ────────────────────────────────────────────
    # Risk = speed exposure + steering instability - scanning vigilance + braking delay.
    # Head scanning reduces risk because it proxies situational awareness.
    # Brake reaction penalty is only added when a trigger event was detected
    # in the window (brm > 0); otherwise it contributes 0.
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

