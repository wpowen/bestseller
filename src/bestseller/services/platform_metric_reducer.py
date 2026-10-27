"""Deterministic, pre-registered platform metric calculations."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
import math
import random
from statistics import NormalDist

from bestseller.domain.platform_observation import FixedFollowupRiskSet, PlatformObservationEvent


def _account_events(
    events: Iterable[PlatformObservationEvent],
) -> dict[str, list[PlatformObservationEvent]]:
    grouped: dict[str, list[PlatformObservationEvent]] = {}
    for event in events:
        if event.platform_account_key and event.identity_source == "platform_account":
            grouped.setdefault(event.platform_account_key, []).append(event)
    return grouped


def qualified_unique_readers(events: Iterable[PlatformObservationEvent]) -> int:
    """Count account-level readers; device/cookie keys intentionally return zero."""
    return len(_account_events(events))


def exposure_denominator(events: Iterable[PlatformObservationEvent]) -> int:
    return len(
        {
            key
            for key, rows in _account_events(events).items()
            if any(row.exposure_eligible for row in rows)
        }
    )


def first_10_arrival(events: Iterable[PlatformObservationEvent]) -> float | None:
    accounts = _account_events(events)
    eligible = {
        key
        for key, rows in accounts.items()
        if any(
            row.event == "chapter_start" and row.chapter_number == 1 and row.exposure_eligible
            for row in rows
        )
    }
    reached = {
        key
        for key, rows in accounts.items()
        if any(row.event == "chapter_start" and (row.chapter_number or 0) >= 10 for row in rows)
    }
    return len(eligible & reached) / len(eligible) if eligible else None


def build_fixed_followup_risk_set(
    events: Iterable[PlatformObservationEvent],
    *,
    data_cutoff: datetime,
) -> FixedFollowupRiskSet:
    acquisition_cutoff = data_cutoff - timedelta(hours=192)
    accounts = _account_events(events)
    starts = {
        key: min(
            (
                row.occurred_at
                for row in rows
                if row.event == "chapter_start" and row.chapter_number == 1
            ),
            default=None,
        )
        for key, rows in accounts.items()
    }
    eligible = frozenset(
        key for key, at in starts.items() if at is not None and at <= acquisition_cutoff
    )
    late = frozenset(
        key for key, at in starts.items() if at is not None and at > acquisition_cutoff
    )
    retained_keys: set[str] = set()
    for key in eligible:
        started_at = starts[key]
        if started_at is None:
            continue
        if any(
            row.event == "chapter_start"
            and (row.chapter_number or 0) > 1
            and timedelta(hours=168) <= row.occurred_at - started_at < timedelta(hours=192)
            for row in accounts[key]
        ):
            retained_keys.add(key)
    retained = frozenset(retained_keys)
    result = FixedFollowupRiskSet(data_cutoff, acquisition_cutoff, eligible, retained, late)
    result.validate()
    return result


def empirical_quantile_type7(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    if not 0 <= q <= 1:
        raise ValueError("q must be between 0 and 1")
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    h = (len(ordered) - 1) * q
    lower = math.floor(h)
    upper = math.ceil(h)
    return ordered[lower] + (h - lower) * (ordered[upper] - ordered[lower])


def wilson_interval(
    successes: int, trials: int, confidence: float = 0.95
) -> tuple[float, float] | None:
    if trials <= 0 or successes < 0 or successes > trials:
        return None
    z = NormalDist().inv_cdf((1 + confidence) / 2)
    p = successes / trials
    denominator = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * trials)) / trials) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def holm_adjust(p_values: Sequence[float], alpha: float = 0.05) -> tuple[bool, ...]:
    """Return Holm step-down rejection flags in original order."""
    if not p_values:
        return ()
    indexed = sorted(enumerate(p_values), key=lambda item: item[1])
    rejected = [False] * len(p_values)
    still_reject = True
    for rank, (index, p_value) in enumerate(indexed):
        if still_reject and p_value <= alpha / (len(p_values) - rank):
            rejected[index] = True
        else:
            still_reject = False
    return tuple(rejected)


def percentile_bootstrap(
    values: Sequence[float],
    *,
    q: float = 0.5,
    repetitions: int = 10_000,
    seed: int = 0,
    confidence: float = 0.95,
) -> tuple[float, float] | None:
    """Book-cluster bootstrap interval for a frozen empirical percentile."""
    if not values or repetitions <= 0:
        return None
    generator = random.Random(seed)  # noqa: S311 — reproducible statistical resampling, not secrets.
    samples: list[float] = []
    for _ in range(repetitions):
        estimate = empirical_quantile_type7(
            [values[generator.randrange(len(values))] for _ in values], q
        )
        if estimate is not None:
            samples.append(estimate)
    tail = (1 - confidence) / 2
    return (
        empirical_quantile_type7(samples, tail) or 0.0,
        empirical_quantile_type7(samples, 1 - tail) or 0.0,
    )


def all_primary_metrics_pass(
    results: Sequence[tuple[float | None, float | None, float | None, float | None]],
) -> bool:
    """Each tuple is (estimate, lower_ci, cohort_p25/p50); no averaging."""
    return bool(results) and all(
        estimate is not None
        and lower is not None
        and p25 is not None
        and p50 is not None
        and estimate >= p50
        and lower >= p25
        for estimate, lower, p25, p50 in results
    )


platform_percentile = empirical_quantile_type7


__all__ = [
    "all_primary_metrics_pass",
    "build_fixed_followup_risk_set",
    "empirical_quantile_type7",
    "exposure_denominator",
    "first_10_arrival",
    "holm_adjust",
    "percentile_bootstrap",
    "platform_percentile",
    "qualified_unique_readers",
    "wilson_interval",
]
