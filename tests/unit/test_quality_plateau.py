"""C3 plateau stop: stop reworking when rework stops working.

Today the rework loop is bounded only by counters
(``max_total_scene_rounds_per_chapter=20``,
``autonomous_quality_retrofit_max_attempts=5``). A chapter the model cannot
improve therefore pays the FULL cap every time, and the observed failure mode
is exactly that: ch9 churned 30 minutes over 16+ scene rounds without
converging.

Two distinct wins are pinned here:

1. **Stop early when rounds stop buying quality** — the autonovel-style
   plateau halt named in plan §5.3 C3.
2. **Return the BEST attempt, never the last.** The rewrite loop previously
   shipped whichever attempt came last, so an exhausted loop could publish a
   draft strictly worse than one it had already produced.
"""

from __future__ import annotations

import pytest

from bestseller.services.quality_plateau import (
    PlateauDecision,
    blocking_code_scores,
    evaluate_rework_plateau,
)


pytestmark = pytest.mark.unit


def test_no_attempts_yet_is_not_a_plateau() -> None:
    decision = evaluate_rework_plateau([])

    assert decision.should_stop is False
    assert decision.reason == "no_attempts"


def test_steady_improvement_keeps_going() -> None:
    decision = evaluate_rework_plateau([0.40, 0.52, 0.63], patience=2)

    assert decision.should_stop is False
    assert decision.reason == "improving"
    assert decision.rounds_without_gain == 0


def test_stops_after_patience_rounds_without_gain() -> None:
    """0.61 then 0.62 are both within min_delta of the 0.60 high-water mark."""

    decision = evaluate_rework_plateau(
        [0.60, 0.61, 0.62], patience=2, min_delta=0.05
    )

    assert decision.should_stop is True
    assert decision.reason == "plateau"
    assert decision.rounds_without_gain == 2


def test_one_flat_round_is_not_yet_a_plateau() -> None:
    """Patience exists because a single bad round is noise, not a ceiling."""

    decision = evaluate_rework_plateau([0.60, 0.61], patience=2, min_delta=0.05)

    assert decision.should_stop is False
    assert decision.rounds_without_gain == 1


def test_a_real_gain_resets_patience() -> None:
    decision = evaluate_rework_plateau(
        [0.40, 0.41, 0.70, 0.71], patience=2, min_delta=0.05
    )

    assert decision.should_stop is False
    assert decision.rounds_without_gain == 1
    assert decision.best_index == 3


def test_best_attempt_wins_even_when_the_last_is_worse() -> None:
    """The 'rewrite exhausted, shipped the worst draft' regression."""

    decision = evaluate_rework_plateau(
        [0.30, 0.82, 0.44, 0.50], patience=2, min_delta=0.05
    )

    assert decision.should_stop is True
    assert decision.reason == "plateau"
    assert decision.best_index == 1
    assert decision.best_score == pytest.approx(0.82)


def test_ties_prefer_the_earlier_attempt() -> None:
    """Equal quality should not cost extra rework rounds."""

    decision = evaluate_rework_plateau([0.70, 0.70, 0.70], patience=2)

    assert decision.best_index == 0


def test_target_met_stops_immediately_without_spending_patience() -> None:
    decision = evaluate_rework_plateau([0.91], patience=3, target=0.85)

    assert decision.should_stop is True
    assert decision.reason == "target_met"
    assert decision.best_score == pytest.approx(0.91)


def test_target_met_wins_over_a_plateau_reading() -> None:
    decision = evaluate_rework_plateau(
        [0.90, 0.90, 0.90], patience=2, min_delta=0.05, target=0.85
    )

    assert decision.reason == "target_met"


def test_hard_round_cap_still_stops_a_slowly_improving_loop() -> None:
    """Plateau detection is additive: the existing budget remains the backstop
    for a loop that keeps making tiny gains forever."""

    decision = evaluate_rework_plateau(
        [0.10, 0.20, 0.30, 0.40], patience=5, min_delta=0.01, max_rounds=4
    )

    assert decision.should_stop is True
    assert decision.reason == "budget_exhausted"
    assert decision.best_index == 3


def test_regression_run_stops_and_keeps_the_high_water_mark() -> None:
    decision = evaluate_rework_plateau([0.80, 0.50, 0.40], patience=2, min_delta=0.01)

    assert decision.should_stop is True
    assert decision.best_index == 0
    assert decision.best_score == pytest.approx(0.80)


def test_patience_zero_disables_plateau_detection() -> None:
    """No-op contract: a deployment can turn the behavior off entirely and get
    the historical count-capped loop back."""

    decision = evaluate_rework_plateau([0.5, 0.5, 0.5, 0.5], patience=0)

    assert decision.should_stop is False
    assert decision.reason == "plateau_detection_disabled"


def test_non_finite_scores_do_not_crash_the_loop() -> None:
    """Scorers fail; a NaN must not become a silent 'best' draft."""

    decision = evaluate_rework_plateau(
        [0.40, float("nan"), 0.55], patience=2, min_delta=0.05
    )

    assert decision.best_index == 2
    assert decision.best_score == pytest.approx(0.55)


def test_decision_is_immutable() -> None:
    decision = evaluate_rework_plateau([0.5])
    assert isinstance(decision, PlateauDecision)
    with pytest.raises(Exception):
        decision.should_stop = True  # type: ignore[misc]


def test_blocking_code_scores_treat_fewer_blockers_as_better() -> None:
    scores = blocking_code_scores(
        [
            {"blocking_codes": ["A", "B", "C"]},
            {"blocking_codes": ["A", "B"]},
            {"blocking_codes": []},
        ]
    )

    assert scores == [-3.0, -2.0, -0.0]
    decision = evaluate_rework_plateau(scores, patience=2, min_delta=1.0)
    assert decision.should_stop is False, "each pass removed a blocker"


def test_blocking_code_scores_flag_a_stalled_repair_run() -> None:
    """Three passes that each removed nothing — the churn shape."""

    scores = blocking_code_scores([{"blocking_codes": ["A", "B"]}] * 3)
    decision = evaluate_rework_plateau(scores, patience=2, min_delta=1.0)

    assert decision.should_stop is True
    assert decision.reason == "plateau"


def test_blocking_code_scores_survive_unreadable_payloads() -> None:
    scores = blocking_code_scores([None, "junk", {"blocking_codes": ["A"]}])

    assert scores[0] == float("-inf")
    assert scores[1] == float("-inf")
    assert scores[2] == -1.0


def test_plateau_stop_is_wired_into_the_repair_trigger() -> None:
    """Guard against the repo's recurring "implemented but never reached"
    failure: a detector that only lives in tests changes no book.

    Pins that the decision runs inside ``maybe_prepare_chapter_auto_repair``
    (the single place that decides whether to spend another repair pass) and
    that it logs, so a real book run can prove execution from the worker log.
    """

    import inspect

    from bestseller.services import drafts

    source = inspect.getsource(drafts.maybe_prepare_chapter_auto_repair)

    assert "evaluate_rework_plateau(" in source
    assert "blocking_code_scores(" in source
    assert "rework_plateau_patience" in source
    assert "repair plateau after" in source, (
        "needs a log line: 日志一条没有 = 没执行"
    )
    assert "_load_chapter_repair_history(" in source


def test_settings_expose_the_knobs() -> None:
    from bestseller.settings import PipelineSettings

    settings = PipelineSettings()
    assert settings.rework_plateau_patience >= 1, (
        "shipped enabled: plateau stop can only halt EARLIER than the existing "
        "cap and returns the best draft, so it is strictly safer than the "
        "count-only loop"
    )
    assert 0 < settings.rework_plateau_min_delta < 1
