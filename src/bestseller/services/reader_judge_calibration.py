"""Shadow calibration for the reader-judge voice axes (plan B1).

``enforce_reader_judge_voice_axes`` defaults to false and the floors sit at
0.55 — a number chosen before any score distribution existed. Turning the gate
on without checking that number against real chapters risks the outage shape
this repo has hit before: a bar set above the model's actual ceiling turns
every chapter into a rewrite loop (the retention gate's 0.62 vs the writer's
~0.51 ceiling did exactly that, and had to be made soft).

So the only question this module answers is the empirical one:

    at threshold T, what fraction of chapters ALREADY WRITTEN would have been
    blocked?

Everything here is pure and read-only. It reads what the shadow judge already
recorded in ``chapter.metadata_json["reader_judge"]``; it never scores, never
writes, and never gates.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from bestseller.services.reader_judge import extract_reader_judge_dimensions

#: The axes that ``voice_axis_failures`` can block on.
VOICE_AXES: tuple[str, ...] = ("ai_taste", "human_voice")

#: Candidate floors to evaluate. 0.55 must stay in the list — it is the value
#: ``min_ai_taste`` / ``min_human_voice`` currently carry, so leaving it out
#: would calibrate a threshold the gate does not use.
DEFAULT_THRESHOLDS: tuple[float, ...] = (0.45, 0.50, 0.55, 0.60, 0.65)

#: Chapters needed before the report will recommend anything. Same reasoning as
#: the arena's ARENA_MIN_PAIRS: a handful of chapters is an anecdote.
MIN_CALIBRATION_CHAPTERS = 20

#: Above this share of blocked chapters a floor is an outage, not a gate.
MAX_TOLERABLE_BLOCK_RATE = 0.25


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    if not sorted_values:
        raise ValueError("empty sample")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = fraction * (len(sorted_values) - 1)
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    weight = position - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def summarize_axis(
    scores: Iterable[float],
    *,
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    """Distribution + would-have-blocked counts for one axis.

    Percentiles rather than a bare mean: a floor cuts into the tail, and a mean
    says nothing about where the tail sits.

    ``would_block`` counts scores STRICTLY below the threshold, matching
    ``reader_judge.voice_axis_failures`` so the projection reflects what the
    gate would really do.
    """

    values = sorted(float(s) for s in scores)
    if not values:
        return {
            "samples": 0,
            "mean": None,
            "median": None,
            "p10": None,
            "p25": None,
            "p90": None,
            "would_block": {float(t): 0 for t in thresholds},
            "would_block_rate": {float(t): 0.0 for t in thresholds},
        }

    blocked = {
        float(t): sum(1 for v in values if v < float(t)) for t in thresholds
    }
    return {
        "samples": len(values),
        "mean": round(sum(values) / len(values), 4),
        "median": round(_percentile(values, 0.5), 4),
        "p10": round(_percentile(values, 0.10), 4),
        "p25": round(_percentile(values, 0.25), 4),
        "p90": round(_percentile(values, 0.90), 4),
        "would_block": blocked,
        "would_block_rate": {
            t: round(count / len(values), 4) for t, count in blocked.items()
        },
    }


def calibrate_voice_axes(
    chapter_metadatas: Iterable[Any],
    *,
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
    axes: Sequence[str] = VOICE_AXES,
    min_chapters: int = MIN_CALIBRATION_CHAPTERS,
    max_block_rate: float = MAX_TOLERABLE_BLOCK_RATE,
) -> dict[str, Any]:
    """Project what enforcing each candidate floor would have done.

    ``chapter_metadatas`` is each chapter's ``metadata_json``. Chapters the
    shadow judge never scored are counted in ``chapters_total`` but excluded
    from every axis sample — treating missing data as a failing score would
    manufacture a block rate out of nothing.
    """

    metadatas = list(chapter_metadatas)
    per_axis: dict[str, list[float]] = {axis: [] for axis in axes}
    judged = 0
    for metadata in metadatas:
        dims = extract_reader_judge_dimensions(metadata)
        if not dims:
            continue
        scored_any = False
        for axis in axes:
            if axis in dims:
                per_axis[axis].append(float(dims[axis]))
                scored_any = True
        if scored_any:
            judged += 1

    axis_reports = {
        axis: summarize_axis(values, thresholds=thresholds)
        for axis, values in per_axis.items()
    }

    target = float(thresholds[len(thresholds) // 2]) if thresholds else 0.55
    if 0.55 in {float(t) for t in thresholds}:
        target = 0.55

    worst_rate = max(
        (report["would_block_rate"].get(target, 0.0) for report in axis_reports.values()),
        default=0.0,
    )

    if judged < int(min_chapters):
        ready = False
        recommendation = (
            f"Sample too small: {judged} judged chapter(s) < {min_chapters}. "
            "Run the shadow judge over more chapters before enforcing — a floor "
            "set on an anecdote is how a gate ends up above the model's ceiling."
        )
    elif worst_rate > float(max_block_rate):
        ready = False
        recommendation = (
            f"At {target}, {worst_rate:.0%} of judged chapters would be blocked "
            f"(tolerable ≤ {max_block_rate:.0%}). That is an outage, not a gate: "
            "either lower the floor to the observed p10-p25 band or fix the "
            "generation side first."
        )
    else:
        ready = True
        recommendation = (
            f"At {target}, {worst_rate:.0%} of judged chapters would be blocked "
            f"across {judged} chapters — a tail cut, not a stall. Safe to enable "
            "enforce_reader_judge_voice_axes, ideally on one book first. Note the "
            "scores come from a single judge family; cross-family judging is still "
            "required before treating the absolute number as calibrated."
        )

    return {
        "chapters_total": len(metadatas),
        "chapters_judged": judged,
        "thresholds": [float(t) for t in thresholds],
        "target_threshold": target,
        "axes": axis_reports,
        "ready_to_enforce": ready,
        "recommendation": recommendation,
    }


__all__ = [
    "DEFAULT_THRESHOLDS",
    "MAX_TOLERABLE_BLOCK_RATE",
    "MIN_CALIBRATION_CHAPTERS",
    "VOICE_AXES",
    "calibrate_voice_axes",
    "summarize_axis",
]
