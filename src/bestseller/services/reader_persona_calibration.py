"""Calibration constants for reader-persona hard gates.

Thresholds were derived from production persona-feedback audits
(exorcist-detective: weighted_score ~0.51–0.55 when readers report
「白看一章」; payoff_density channel ~0.14–0.17). Adjust via
``config/quality_gates.yaml`` ``reader_quality_gate`` — not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# Reference baselines for eval harness regression (not enforced at runtime).
CALIBRATION_BASELINE: Final[dict[str, float]] = {
    "exorcist_ch003_weighted_score": 0.516,
    "exorcist_ch003_abandon_rate": 0.38,
    "exorcist_ch003_payoff_density": 0.17,
    "commercial_pass_weighted_score": 0.72,
    "commercial_pass_abandon_rate": 0.18,
    "commercial_pass_payoff_density": 0.35,
}


@dataclass(frozen=True)
class CalibrationVerdict:
    metric: str
    value: float
    baseline: float
    within_tolerance: bool
    tolerance: float


def compare_to_baseline(
    metric: str,
    value: float,
    *,
    tolerance: float = 0.08,
) -> CalibrationVerdict:
    """Check a single metric against CALIBRATION_BASELINE."""

    baseline = float(CALIBRATION_BASELINE.get(metric, value))
    return CalibrationVerdict(
        metric=metric,
        value=value,
        baseline=baseline,
        within_tolerance=abs(value - baseline) <= tolerance,
        tolerance=tolerance,
    )


__all__ = [
    "CALIBRATION_BASELINE",
    "CalibrationVerdict",
    "compare_to_baseline",
]
