"""Pre-registered, fail-closed reader power and Holm gates."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import erf, sqrt

from bestseller.domain.reader_study import wilson_lower


@dataclass(frozen=True, slots=True)
class ReaderPowerGateResult:
    passed: bool
    power: float
    sample_size: int
    codes: tuple[str, ...]
    adjusted_alpha: float


def holm_adjusted_alpha(p_values: Iterable[float], alpha: float = 0.05) -> float:
    values = sorted(max(0.0, min(1.0, float(p))) for p in p_values)
    return alpha / max(1, len(values))


def normal_power(
    *, n: int, baseline: float = 0.5, alternative: float = 0.6, alpha: float = 0.05
) -> float:
    if n <= 0 or alternative <= baseline:
        return 0.0
    z = 1.959963984540054
    se = sqrt(max(1e-12, baseline * (1 - baseline) / n))
    threshold = baseline + z * se
    alt_se = sqrt(max(1e-12, alternative * (1 - alternative) / n))
    return 0.5 * (1 + erf((alternative - threshold) / (alt_se * sqrt(2))))


def evaluate_reader_power_gate(
    *,
    sample_size: int,
    observed_successes: int | None = None,
    preregistered_power: float | None = None,
    p_values: Mapping[str, float] | None = None,
    min_n: int = 60,
    min_power: float = 0.8,
    min_effect: float = 0.10,
) -> ReaderPowerGateResult:
    codes: set[str] = set()
    estimated = (
        float(preregistered_power)
        if preregistered_power is not None
        else normal_power(n=sample_size)
    )
    if sample_size < min_n:
        codes.add("INCONCLUSIVE_READER_POWER")
    if estimated < min_power:
        codes.add("POWER_BELOW_0_80")
    if observed_successes is not None:
        if not 0 <= observed_successes <= sample_size:
            codes.add("INVALID_SUCCESS_COUNT")
        elif observed_successes / sample_size < 0.5 + min_effect:
            codes.add("PRACTICAL_EFFECT_BELOW_0_10")
        elif wilson_lower(observed_successes, sample_size) <= 0.5:
            codes.add("PREFERENCE_CI_NOT_ABOVE_CHANCE")
    adjusted = holm_adjusted_alpha((p_values or {}).values())
    return ReaderPowerGateResult(not codes, estimated, sample_size, tuple(sorted(codes)), adjusted)


__all__ = [
    "ReaderPowerGateResult",
    "evaluate_reader_power_gate",
    "holm_adjusted_alpha",
    "normal_power",
]
