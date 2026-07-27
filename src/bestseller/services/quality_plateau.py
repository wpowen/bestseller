"""Stop reworking a draft once rework stops buying quality (plan §5.3 C3).

Why this exists
---------------
The rework loop is bounded only by counters — ``max_total_scene_rounds_per_
chapter`` (20) and ``autonomous_quality_retrofit_max_attempts`` (5). Counters
answer "how many rounds may I spend", never "is spending another round worth
it", so a chapter the model cannot improve pays the full cap every single time.
The observed shape of that: one chapter churned ~30 minutes across 16+ scene
rounds without ever converging.

autonovel's loop (the external reference in plan §1.2) halts on a quality
plateau instead of on a counter. This module is the deterministic core of that
idea, kept pure so the decision is testable without a pipeline.

Two behaviours, not one
-----------------------
Stopping early is the visible half. The other half matters just as much: this
returns the index of the BEST attempt. The rewrite loop historically shipped
whichever attempt happened to come last, so an exhausted loop could publish a
draft strictly worse than one it had already generated and thrown away.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

#: A gain smaller than this is noise, not progress. Calibrated well below the
#: gate thresholds it feeds (reader-quality bars sit around 0.62) but above the
#: jitter of repeated scoring of the same text.
DEFAULT_MIN_DELTA = 0.02

#: Consecutive gain-less rounds tolerated before declaring a plateau. One flat
#: round is routinely noise; two in a row is a ceiling.
DEFAULT_PATIENCE = 2


@dataclass(frozen=True)
class PlateauDecision:
    """Outcome of inspecting a rework score history.

    ``best_index`` is always the attempt to keep, whether or not the loop stops
    — callers must promote that draft rather than the most recent one.
    """

    should_stop: bool
    reason: str
    best_index: int
    best_score: float
    rounds_without_gain: int


def _clean(scores: Sequence[float]) -> list[float]:
    """Map unusable scores to -inf so they can never win ``best``.

    A crashed scorer yields NaN. Comparisons against NaN are all False, so a
    naive ``max`` can silently return it and promote an unscored draft.
    """

    cleaned: list[float] = []
    for raw in scores:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = -math.inf
        cleaned.append(value if math.isfinite(value) else -math.inf)
    return cleaned


def evaluate_rework_plateau(
    scores: Sequence[float],
    *,
    patience: int = DEFAULT_PATIENCE,
    min_delta: float = DEFAULT_MIN_DELTA,
    target: float | None = None,
    max_rounds: int | None = None,
) -> PlateauDecision:
    """Decide whether another rework round is worth spending.

    ``scores`` is the quality score of each attempt so far, oldest first.

    Precedence, highest first:

    1. ``target`` reached — the loop achieved what it was asked for.
    2. ``max_rounds`` reached — the existing counter budget, kept as backstop
       for a loop that keeps making genuine but tiny gains.
    3. ``patience`` consecutive rounds failing to beat the running best by
       ``min_delta`` — the plateau.

    ``patience <= 0`` disables plateau detection and restores the historical
    count-only behaviour.
    """

    cleaned = _clean(scores)
    if not cleaned:
        return PlateauDecision(
            should_stop=False,
            reason="no_attempts",
            best_index=-1,
            best_score=-math.inf,
            rounds_without_gain=0,
        )

    # First occurrence wins ties: equal quality should not cost extra rounds,
    # and the earlier draft has already cleared whatever came before it.
    best_index = 0
    best_score = cleaned[0]
    for index in range(1, len(cleaned)):
        if cleaned[index] > best_score:
            best_score = cleaned[index]
            best_index = index

    # Trailing rounds that failed to raise the high-water mark by min_delta.
    rounds_without_gain = 0
    running_best = cleaned[0]
    for index in range(1, len(cleaned)):
        if cleaned[index] >= running_best + min_delta:
            rounds_without_gain = 0
            running_best = cleaned[index]
        else:
            rounds_without_gain += 1
            running_best = max(running_best, cleaned[index])

    def _decide(should_stop: bool, reason: str) -> PlateauDecision:
        return PlateauDecision(
            should_stop=should_stop,
            reason=reason,
            best_index=best_index,
            best_score=best_score,
            rounds_without_gain=rounds_without_gain,
        )

    if target is not None and best_score >= float(target):
        return _decide(True, "target_met")

    if max_rounds is not None and max_rounds > 0 and len(cleaned) >= int(max_rounds):
        return _decide(True, "budget_exhausted")

    if patience <= 0:
        return _decide(False, "plateau_detection_disabled")

    if rounds_without_gain >= int(patience):
        return _decide(True, "plateau")

    return _decide(False, "improving")


#: Chapter-level repair has no continuous score — the persisted quality report
#: carries ``blocking_codes``. One fewer blocking code IS the unit of progress
#: there, so the plateau delta is a whole code rather than a fraction.
BLOCKING_CODE_MIN_DELTA = 1.0


def blocking_code_scores(report_payloads: Sequence[object]) -> list[float]:
    """Turn a chapter's repair history into higher-is-better scores.

    ``report_payloads`` is the ``report_json`` of each
    ``ChapterQualityReportModel`` row, OLDEST FIRST. The score is the negated
    blocking-code count: a pass that removes a blocker scores +1 over its
    predecessor, and a pass that removes nothing scores flat — which is exactly
    the condition the plateau detector is looking for.

    An unreadable payload scores as ``-inf`` so it can never be selected as the
    best attempt.
    """

    scores: list[float] = []
    for payload in report_payloads:
        if not isinstance(payload, dict):
            scores.append(-math.inf)
            continue
        codes = payload.get("blocking_codes")
        if not isinstance(codes, (list, tuple)):
            codes = ()
        scores.append(-float(len(codes)))
    return scores


__all__ = [
    "BLOCKING_CODE_MIN_DELTA",
    "DEFAULT_MIN_DELTA",
    "DEFAULT_PATIENCE",
    "PlateauDecision",
    "blocking_code_scores",
    "evaluate_rework_plateau",
]
