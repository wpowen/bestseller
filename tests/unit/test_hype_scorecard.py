"""Unit tests for the hype-dimension scorecard logic (Phase 2).

Scope:
    * ``_aggregate_hype_metrics`` — turns per-chapter assignments into the
      7-tuple the driver forwards to ``compute_quality_score``.
    * Hype-specific slices of ``compute_quality_score`` — exercise each of
      the four 20-point hype components in isolation.
    * Legacy median defaults — verify the "unknown project" substitute
      behaviour specified in plan §Phase 3 "迁移策略".
    * ``NovelScorecard.to_dict`` — round-trip for all 8 new fields.

Integration-style DB-backed tests for ``compute_scorecard`` live under
``tests/integration/services/test_scorecard.py``.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.services.hype_engine import HypeScheme, HypeType
from bestseller.services.scorecard import (
    LEGACY_COMEDIC_BEAT_HIT_RATIO,
    LEGACY_HYPE_DISTRIBUTION_ENTROPY,
    LEGACY_HYPE_INTENSITY_MEAN,
    NovelScorecard,
    _aggregate_hype_metrics,
    compute_quality_score,
)


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _penalty_free_kwargs() -> dict:
    """Baseline kwargs where the 55-point penalty axis is fully earned."""
    return dict(
        total_chapters=100,
        missing_chapters=0,
        chapters_blocked=0,
        length_cv=0.05,
        cjk_leak_chapters=0,
        dialog_integrity_violations=0,
        pov_drift_chapters=0,
        opening_archetype_entropy=0.9,
        cliffhanger_entropy=0.9,
        vocab_hhi=0.02,
    )


def _zero_hype_kwargs() -> dict:
    """Baseline with every hype component at its zero-credit value.

    Diversity is pinned to max (identical to ``_full_hype_kwargs``) so the
    full-vs-zero delta isolates the 20-point hype pool — otherwise realistic
    diversity numbers would bleed into the difference.
    """
    return _penalty_free_kwargs() | dict(
        length_cv=0.0,
        opening_archetype_entropy=1.0,
        cliffhanger_entropy=1.0,
        vocab_hhi=0.0,
        hype_distribution_entropy=0.0,
        hype_intensity_mean=0.0,
        comedic_beat_hit_ratio=0.0,
        hype_missing_ratio=1.0,
        golden_three_weak=False,
    )


def _full_hype_kwargs() -> dict:
    """Baseline with every hype AND diversity component at maximum credit.

    Pinning diversity to its max too (entropy 1.0 / hhi 0.0) lets the
    ``test_full_hype_reaches_100`` assertion actually hit 100 — otherwise
    realistic 0.9/0.02 diversity values leave ~3 points on the table.
    """
    return _penalty_free_kwargs() | dict(
        length_cv=0.0,  # zero variation = full 4 points in the length slot.
        opening_archetype_entropy=1.0,
        cliffhanger_entropy=1.0,
        vocab_hhi=0.0,
        hype_distribution_entropy=1.0,
        hype_intensity_mean=10.0,
        comedic_beat_hit_ratio=1.0,
        hype_missing_ratio=0.0,
        golden_three_weak=False,
    )


# ---------------------------------------------------------------------------
# _aggregate_hype_metrics — pure summariser.
# ---------------------------------------------------------------------------


class TestAggregateHypeMetrics:
    def test_no_chapters_scored_returns_zeroes(self) -> None:
        entropy, mean, var, density, hit_ratio, missing, scored = (
            _aggregate_hype_metrics(
                total_chapters=10,
                hype_types_by_chapter={},
                hype_intensities=[],
                scheme=HypeScheme(),
            )
        )
        assert scored == 0
        assert missing == 10
        assert (entropy, mean, var, density, hit_ratio) == (
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        )

    def test_entropy_rewards_mixed_distribution(self) -> None:
        """Uniform 4-type mix across 8 chapters should hit entropy 1.0."""
        hype_by_ch = {
            1: HypeType.FACE_SLAP.value,
            2: HypeType.POWER_REVEAL.value,
            3: HypeType.LEVEL_UP.value,
            4: HypeType.COMEDIC_BEAT.value,
            5: HypeType.FACE_SLAP.value,
            6: HypeType.POWER_REVEAL.value,
            7: HypeType.LEVEL_UP.value,
            8: HypeType.COMEDIC_BEAT.value,
        }
        entropy, *_ = _aggregate_hype_metrics(
            total_chapters=8,
            hype_types_by_chapter=hype_by_ch,
            hype_intensities=[],
            scheme=HypeScheme(),
        )
        assert entropy == pytest.approx(1.0)

    def test_entropy_zero_when_single_type(self) -> None:
        hype_by_ch = {i: HypeType.FACE_SLAP.value for i in range(1, 6)}
        entropy, *_ = _aggregate_hype_metrics(
            total_chapters=5,
            hype_types_by_chapter=hype_by_ch,
            hype_intensities=[],
            scheme=HypeScheme(),
        )
        assert entropy == 0.0

    def test_intensity_mean_and_variance(self) -> None:
        _, mean, var, *_ = _aggregate_hype_metrics(
            total_chapters=3,
            hype_types_by_chapter={
                1: HypeType.FACE_SLAP.value,
                2: HypeType.FACE_SLAP.value,
                3: HypeType.FACE_SLAP.value,
            },
            hype_intensities=[6.0, 7.0, 8.0],
            scheme=HypeScheme(),
        )
        assert mean == pytest.approx(7.0)
        # pvariance([6, 7, 8]) = ((6-7)² + 0 + (8-7)²) / 3 = 2/3.
        assert var == pytest.approx(2 / 3)

    def test_single_intensity_yields_zero_variance(self) -> None:
        _, mean, var, *_ = _aggregate_hype_metrics(
            total_chapters=1,
            hype_types_by_chapter={1: HypeType.FACE_SLAP.value},
            hype_intensities=[8.0],
            scheme=HypeScheme(),
        )
        assert mean == 8.0
        assert var == 0.0

    def test_comedic_density_computed_over_scored_chapters(self) -> None:
        hype_by_ch = {
            1: HypeType.FACE_SLAP.value,
            2: HypeType.COMEDIC_BEAT.value,
            3: HypeType.LEVEL_UP.value,
            4: HypeType.COMEDIC_BEAT.value,
            5: HypeType.POWER_REVEAL.value,
        }
        _, _, _, density, hit_ratio, missing, scored = _aggregate_hype_metrics(
            total_chapters=5,
            hype_types_by_chapter=hype_by_ch,
            hype_intensities=[],
            scheme=HypeScheme(comedic_beat_density_target=0.2),
        )
        assert scored == 5
        assert missing == 0
        assert density == pytest.approx(2 / 5)
        # 0.4 observed / 0.2 target → capped to 1.0.
        assert hit_ratio == 1.0

    def test_comedic_hit_ratio_capped_at_one(self) -> None:
        hype_by_ch = {
            1: HypeType.COMEDIC_BEAT.value,
            2: HypeType.COMEDIC_BEAT.value,
        }
        *_, hit_ratio, _, _ = _aggregate_hype_metrics(
            total_chapters=2,
            hype_types_by_chapter=hype_by_ch,
            hype_intensities=[],
            scheme=HypeScheme(comedic_beat_density_target=0.1),
        )
        # observed 1.0 / target 0.1 = 10.0 → capped.
        assert hit_ratio == 1.0

    def test_comedic_below_target_gets_partial_credit(self) -> None:
        hype_by_ch = {
            1: HypeType.COMEDIC_BEAT.value,
            2: HypeType.FACE_SLAP.value,
            3: HypeType.POWER_REVEAL.value,
            4: HypeType.LEVEL_UP.value,
        }
        *_, hit_ratio, _, _ = _aggregate_hype_metrics(
            total_chapters=4,
            hype_types_by_chapter=hype_by_ch,
            hype_intensities=[],
            scheme=HypeScheme(comedic_beat_density_target=0.5),
        )
        # observed 0.25 / target 0.5 = 0.5.
        assert hit_ratio == pytest.approx(0.5)

    def test_zero_target_with_observations_yields_full_credit(self) -> None:
        hype_by_ch = {1: HypeType.COMEDIC_BEAT.value}
        *_, hit_ratio, _, _ = _aggregate_hype_metrics(
            total_chapters=1,
            hype_types_by_chapter=hype_by_ch,
            hype_intensities=[],
            scheme=HypeScheme(comedic_beat_density_target=0.0),
        )
        assert hit_ratio == 1.0

    def test_zero_target_without_observations_zero_credit(self) -> None:
        hype_by_ch = {1: HypeType.FACE_SLAP.value}
        *_, hit_ratio, _, _ = _aggregate_hype_metrics(
            total_chapters=1,
            hype_types_by_chapter=hype_by_ch,
            hype_intensities=[],
            scheme=HypeScheme(comedic_beat_density_target=0.0),
        )
        assert hit_ratio == 0.0

    def test_missing_is_total_minus_scored(self) -> None:
        hype_by_ch = {1: HypeType.FACE_SLAP.value, 2: HypeType.LEVEL_UP.value}
        *_, missing, scored = _aggregate_hype_metrics(
            total_chapters=10,
            hype_types_by_chapter=hype_by_ch,
            hype_intensities=[],
            scheme=HypeScheme(),
        )
        assert scored == 2
        assert missing == 8

    def test_missing_clamped_to_zero_when_scored_exceeds_total(self) -> None:
        """Defensive: if scored > total (shouldn't happen) missing stays 0."""
        hype_by_ch = {i: HypeType.FACE_SLAP.value for i in range(1, 6)}
        *_, missing, scored = _aggregate_hype_metrics(
            total_chapters=3,
            hype_types_by_chapter=hype_by_ch,
            hype_intensities=[],
            scheme=HypeScheme(),
        )
        assert scored == 5
        assert missing == 0


# ---------------------------------------------------------------------------
# compute_quality_score — hype-specific slices.
# ---------------------------------------------------------------------------


class TestComputeQualityScoreHype:
    def test_full_hype_reaches_100(self) -> None:
        """All hype axes maxed + full penalty/diversity → 100."""
        score = compute_quality_score(**_full_hype_kwargs())
        assert score == pytest.approx(100.0)

    def test_zero_hype_drops_full_20_points(self) -> None:
        """Zero across every hype component costs exactly the 20-point pool."""
        zero = compute_quality_score(**_zero_hype_kwargs())
        full = compute_quality_score(**_full_hype_kwargs())
        assert round(full - zero, 2) == 20.0

    def test_entropy_weight_is_six(self) -> None:
        base = _full_hype_kwargs() | {"hype_distribution_entropy": 0.0}
        full = compute_quality_score(**_full_hype_kwargs())
        lowered = compute_quality_score(**base)
        assert round(full - lowered, 2) == 6.0

    def test_intensity_weight_is_six(self) -> None:
        base = _full_hype_kwargs() | {"hype_intensity_mean": 0.0}
        full = compute_quality_score(**_full_hype_kwargs())
        lowered = compute_quality_score(**base)
        assert round(full - lowered, 2) == 6.0

    def test_comedic_weight_is_four(self) -> None:
        base = _full_hype_kwargs() | {"comedic_beat_hit_ratio": 0.0}
        full = compute_quality_score(**_full_hype_kwargs())
        lowered = compute_quality_score(**base)
        assert round(full - lowered, 2) == 4.0

    def test_coverage_weight_is_four(self) -> None:
        base = _full_hype_kwargs() | {"hype_missing_ratio": 1.0}
        full = compute_quality_score(**_full_hype_kwargs())
        lowered = compute_quality_score(**base)
        assert round(full - lowered, 2) == 4.0

    def test_intensity_clamps_above_ten(self) -> None:
        """Out-of-range intensity doesn't over-reward."""
        over = _full_hype_kwargs() | {"hype_intensity_mean": 99.0}
        at_max = _full_hype_kwargs() | {"hype_intensity_mean": 10.0}
        assert compute_quality_score(**over) == compute_quality_score(**at_max)

    def test_intensity_clamps_below_zero(self) -> None:
        under = _full_hype_kwargs() | {"hype_intensity_mean": -5.0}
        at_zero = _full_hype_kwargs() | {"hype_intensity_mean": 0.0}
        assert compute_quality_score(**under) == compute_quality_score(**at_zero)

    def test_missing_ratio_clamps_above_one(self) -> None:
        over = _full_hype_kwargs() | {"hype_missing_ratio": 2.5}
        at_one = _full_hype_kwargs() | {"hype_missing_ratio": 1.0}
        assert compute_quality_score(**over) == compute_quality_score(**at_one)

    def test_golden_three_weak_subtracts_three(self) -> None:
        without = compute_quality_score(**_full_hype_kwargs())
        with_weak = compute_quality_score(
            **(_full_hype_kwargs() | {"golden_three_weak": True})
        )
        assert round(without - with_weak, 2) == 3.0

    def test_golden_three_weak_cannot_push_below_zero(self) -> None:
        """A catastrophic project with golden_three_weak still clamps to 0."""
        awful = dict(
            total_chapters=5,
            missing_chapters=5,
            chapters_blocked=5,
            length_cv=5.0,
            cjk_leak_chapters=5,
            dialog_integrity_violations=5,
            pov_drift_chapters=5,
            opening_archetype_entropy=0.0,
            cliffhanger_entropy=0.0,
            vocab_hhi=1.0,
            hype_distribution_entropy=0.0,
            hype_intensity_mean=0.0,
            comedic_beat_hit_ratio=0.0,
            hype_missing_ratio=1.0,
            golden_three_weak=True,
        )
        assert compute_quality_score(**awful) == 0.0


# ---------------------------------------------------------------------------
# Legacy median defaults — project predating Phase 2.
# ---------------------------------------------------------------------------


class TestLegacyDefaults:
    def test_defaults_match_plan_medians(self) -> None:
        assert LEGACY_HYPE_DISTRIBUTION_ENTROPY == 0.5
        assert LEGACY_HYPE_INTENSITY_MEAN == 5.0
        assert LEGACY_COMEDIC_BEAT_HIT_RATIO == 0.5

    def test_compute_quality_score_default_kwargs_use_medians(self) -> None:
        """Calling compute_quality_score without hype kwargs applies the
        medians — legacy callers predating Phase 2 don't crater."""

        with_medians = compute_quality_score(**_penalty_free_kwargs())
        # Explicitly passing the same medians should be identical.
        explicit = compute_quality_score(
            **(
                _penalty_free_kwargs()
                | {
                    "hype_distribution_entropy": LEGACY_HYPE_DISTRIBUTION_ENTROPY,
                    "hype_intensity_mean": LEGACY_HYPE_INTENSITY_MEAN,
                    "comedic_beat_hit_ratio": LEGACY_COMEDIC_BEAT_HIT_RATIO,
                    "hype_missing_ratio": 0.0,
                }
            )
        )
        assert with_medians == explicit

    def test_legacy_project_between_zero_and_full_hype(self) -> None:
        """Median-scored project sits roughly midway between 0 and full hype."""

        zero = compute_quality_score(**_zero_hype_kwargs())
        full = compute_quality_score(**_full_hype_kwargs())
        legacy = compute_quality_score(**_penalty_free_kwargs())
        assert zero < legacy < full


# ---------------------------------------------------------------------------
# NovelScorecard.to_dict — hype field serialization.
# ---------------------------------------------------------------------------


class TestNovelScorecardHypeFields:
    def _sample(self, **overrides) -> NovelScorecard:
        base = dict(
            project_id=uuid4(),
            total_chapters=50,
            missing_chapters=0,
            chapters_blocked=0,
            length_mean=6100.0,
            length_stddev=150.0,
            length_cv=0.0246,
            cjk_leak_chapters=0,
            dialog_integrity_violations=0,
            pov_drift_chapters=0,
            opening_archetype_entropy=0.85,
            cliffhanger_entropy=0.75,
            vocab_hhi=0.03,
            top_overused_words=(),
            quality_score=92.5,
            hype_distribution_entropy=0.82,
            hype_intensity_mean=7.5,
            hype_intensity_variance=0.75,
            humiliation_payoff_lag=3.2,
            comedic_beat_density=0.15,
            comedic_beat_hit_ratio=0.8,
            hype_missing_chapters=2,
            golden_three_weak=False,
        )
        base.update(overrides)
        return NovelScorecard(**base)

    def test_all_new_hype_fields_serialized(self) -> None:
        sc = self._sample()
        data = sc.to_dict()
        # Entropy + ratios rounded to 4 decimals, intensity stats to 3,
        # lag to 2 — see to_dict for the rounding contract.
        assert data["hype_distribution_entropy"] == 0.82
        assert data["hype_intensity_mean"] == 7.5
        assert data["hype_intensity_variance"] == 0.75
        assert data["humiliation_payoff_lag"] == 3.2
        assert data["comedic_beat_density"] == 0.15
        assert data["comedic_beat_hit_ratio"] == 0.8
        assert data["hype_missing_chapters"] == 2
        assert data["golden_three_weak"] is False

    def test_rounding_applied_to_hype_fields(self) -> None:
        sc = self._sample(
            hype_distribution_entropy=0.123456789,
            hype_intensity_mean=7.8912345,
            hype_intensity_variance=0.6789123,
            humiliation_payoff_lag=3.14159,
            comedic_beat_density=0.123456789,
            comedic_beat_hit_ratio=0.987654321,
        )
        data = sc.to_dict()
        assert data["hype_distribution_entropy"] == 0.1235
        assert data["hype_intensity_mean"] == 7.891
        assert data["hype_intensity_variance"] == 0.679
        assert data["humiliation_payoff_lag"] == 3.14
        assert data["comedic_beat_density"] == 0.1235
        assert data["comedic_beat_hit_ratio"] == 0.9877

    def test_golden_three_weak_serialized_as_bool(self) -> None:
        sc = self._sample(golden_three_weak=True)
        data = sc.to_dict()
        assert data["golden_three_weak"] is True

    def test_defaults_keep_legacy_scorecard_serialisable(self) -> None:
        """Construct a scorecard without specifying hype fields — the
        dataclass defaults keep it serialisable and the resulting dict
        includes every new key with the zero value."""

        pid = uuid4()
        sc = NovelScorecard(
            project_id=pid,
            total_chapters=10,
            missing_chapters=0,
            chapters_blocked=0,
            length_mean=6000.0,
            length_stddev=100.0,
            length_cv=0.017,
            cjk_leak_chapters=0,
            dialog_integrity_violations=0,
            pov_drift_chapters=0,
            opening_archetype_entropy=0.7,
            cliffhanger_entropy=0.7,
            vocab_hhi=0.05,
            top_overused_words=(),
            quality_score=85.0,
        )
        data = sc.to_dict()
        assert data["hype_distribution_entropy"] == 0.0
        assert data["hype_intensity_mean"] == 0.0
        assert data["hype_missing_chapters"] == 0
        assert data["golden_three_weak"] is False
