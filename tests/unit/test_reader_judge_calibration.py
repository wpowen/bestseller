"""B1 shadow calibration: decide the voice-axis floor from data, not a guess.

``enforce_reader_judge_voice_axes`` is false and ``min_ai_taste`` /
``min_human_voice`` sit at 0.55 — a number nobody has checked against a real
score distribution. Plan §11.5 P0-P item 5 says to shadow-calibrate before
enforcing, and §5.2 B1 adds the reason thresholds cannot be trusted raw:
same-family judges over-score by 0.15-0.20, so an absolute floor imported from
intuition can block most of a book or none of it.

This module answers the only question that matters before flipping the switch:
**at threshold T, what fraction of already-written chapters would have been
blocked?** A floor that would have blocked 80% is not a gate, it is an outage.
"""

from __future__ import annotations

import pytest

from bestseller.services.reader_judge_calibration import (
    calibrate_voice_axes,
    summarize_axis,
)


pytestmark = pytest.mark.unit


def _chapter(ai_taste: float | None = None, human_voice: float | None = None) -> dict:
    dims: dict[str, float] = {}
    if ai_taste is not None:
        dims["ai_taste"] = ai_taste
    if human_voice is not None:
        dims["human_voice"] = human_voice
    return {"reader_judge": {"dimensions": dims}}


def test_summarize_axis_reports_distribution_not_just_a_mean() -> None:
    """A mean hides the tail, and the tail is what a floor cuts into."""

    stats = summarize_axis([0.2, 0.4, 0.5, 0.6, 0.9])

    assert stats["samples"] == 5
    assert stats["mean"] == pytest.approx(0.52)
    assert stats["median"] == pytest.approx(0.5)
    assert stats["p10"] <= stats["median"] <= stats["p90"]


def test_summarize_axis_handles_an_empty_sample() -> None:
    stats = summarize_axis([])

    assert stats["samples"] == 0
    assert stats["mean"] is None


def test_block_rate_is_reported_per_candidate_threshold() -> None:
    scores = [0.30, 0.45, 0.55, 0.60, 0.80]

    stats = summarize_axis(scores, thresholds=(0.5, 0.55, 0.7))

    # Strictly-below counts as blocked, matching voice_axis_failures.
    assert stats["would_block"][0.5] == 2
    assert stats["would_block"][0.55] == 2
    assert stats["would_block"][0.7] == 4
    assert stats["would_block_rate"][0.7] == pytest.approx(0.8)


def test_calibration_covers_each_voice_axis_separately() -> None:
    """The two axes measure different things and can need different floors."""

    report = calibrate_voice_axes(
        [
            _chapter(ai_taste=0.4, human_voice=0.9),
            _chapter(ai_taste=0.5, human_voice=0.8),
        ],
        thresholds=(0.55,),
    )

    assert report["axes"]["ai_taste"]["would_block"][0.55] == 2
    assert report["axes"]["human_voice"]["would_block"][0.55] == 0


def test_chapters_without_a_judge_blob_are_excluded_not_counted_as_zero() -> None:
    """Un-judged chapters must not be scored as failures — that would invent a
    catastrophic block rate out of missing data."""

    report = calibrate_voice_axes(
        [_chapter(ai_taste=0.8), {}, {"reader_judge": {}}, None],
        thresholds=(0.55,),
    )

    assert report["chapters_total"] == 4
    assert report["chapters_judged"] == 1
    assert report["axes"]["ai_taste"]["samples"] == 1
    assert report["axes"]["ai_taste"]["would_block"][0.55] == 0


def test_recommendation_refuses_to_advise_on_a_thin_sample() -> None:
    """Same lesson as the arena: a handful of chapters is not a calibration."""

    report = calibrate_voice_axes([_chapter(ai_taste=0.9)], thresholds=(0.55,))

    assert report["ready_to_enforce"] is False
    assert "sample" in report["recommendation"].lower()


def test_recommendation_flags_a_threshold_that_would_block_most_chapters() -> None:
    """The outage case: enforcing here stalls the whole book."""

    chapters = [_chapter(ai_taste=0.30, human_voice=0.30) for _ in range(30)]

    report = calibrate_voice_axes(chapters, thresholds=(0.55,))

    assert report["ready_to_enforce"] is False
    assert report["axes"]["ai_taste"]["would_block_rate"][0.55] == pytest.approx(1.0)


def test_recommendation_accepts_a_threshold_that_cuts_only_the_tail() -> None:
    chapters = [_chapter(ai_taste=0.80, human_voice=0.80) for _ in range(27)]
    chapters += [_chapter(ai_taste=0.40, human_voice=0.40) for _ in range(3)]

    report = calibrate_voice_axes(chapters, thresholds=(0.55,))

    assert report["axes"]["ai_taste"]["would_block_rate"][0.55] == pytest.approx(0.1)
    assert report["ready_to_enforce"] is True


def test_config_default_threshold_is_among_the_candidates() -> None:
    """The report must evaluate the number actually configured, otherwise it
    calibrates something the gate will not use."""

    from bestseller.services.reader_judge_calibration import DEFAULT_THRESHOLDS

    assert 0.55 in DEFAULT_THRESHOLDS


def test_shadow_mode_is_on_but_cannot_block_anything() -> None:
    """B1 step 1: collect the distribution WITHOUT gating on it.

    Three flags decide this and it is easy to half-flip. The judge must run
    (otherwise there is nothing to calibrate) while remaining unable to block a
    chapter or move a score — enabling judging and enforcement in one step is
    how a floor lands above the model's ceiling and stalls every chapter.
    """

    from bestseller.services.quality_gates_config import load_quality_gates_config

    gate = load_quality_gates_config().reader_quality

    assert gate.enable_llm_reader_judge is True, "no judging → no calibration data"
    assert gate.reader_judge_audit_only is True, "shadow must not feed scores"
    assert gate.enforce_reader_judge_voice_axes is False, (
        "enforcement waits on the calibration report"
    )


def test_calibration_script_exists_and_is_read_only() -> None:
    from pathlib import Path

    source = Path("scripts/reader_judge_shadow_calibration.py").read_text(
        encoding="utf-8"
    )
    assert "calibrate_voice_axes" in source
    for mutation in ("UPDATE ", "DELETE ", "session.add(", "commit()"):
        assert mutation not in source, (
            f"calibration must not write to the library ({mutation!r} found)"
        )
