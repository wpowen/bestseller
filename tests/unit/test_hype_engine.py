"""Unit tests for the Reader Hype Engine (Phase 0 + Phase 1 primitives).

Covers:
  * Distribution normalisation (HYPE_DISTRIBUTION sums to 1.0)
  * HYPE_DENSITY_CURVE percentile continuity + BEAT_SHEET alignment
  * score_hype 6-component weighting + clamping
  * target_hype_for_chapter pacing-profile shift
  * evaluate_hype_diversity: immediate repeat, run-length forbid, suggestion
  * select_recipe_for_chapter: LRU fallback + band preference + forbid
  * pick_hype_for_chapter: empty deck + preset deck
  * HypeScheme / HypeMoment JSONB round-trip
  * classify_hype keyword detection
  * extract_ending_sentence
"""

from __future__ import annotations

import pytest

from bestseller.services.hype_engine import (
    HYPE_DENSITY_CURVE,
    HYPE_DISTRIBUTION,
    HYPE_SCORE_WEIGHTS,
    HypeDensityBand,
    HypeMoment,
    HypeRecipe,
    HypeScheme,
    HypeType,
    classify_hype,
    evaluate_hype_diversity,
    extract_ending_sentence,
    hype_moment_from_dict,
    hype_moment_to_dict,
    hype_scheme_from_dict,
    hype_scheme_to_dict,
    pick_hype_for_chapter,
    score_hype,
    select_recipe_for_chapter,
    target_hype_for_chapter,
)
from bestseller.services.pacing_engine import BEAT_SHEET

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Distribution + curve invariants.
# ---------------------------------------------------------------------------


class TestDistributionAndCurve:
    def test_hype_distribution_sums_to_one(self) -> None:
        total = sum(HYPE_DISTRIBUTION.values())
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_hype_distribution_covers_all_types(self) -> None:
        assert set(HYPE_DISTRIBUTION.keys()) == set(HypeType)

    def test_score_weights_sum_to_one(self) -> None:
        total = sum(HYPE_SCORE_WEIGHTS.values())
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_density_curve_is_continuous(self) -> None:
        # Each band's upper bound equals the next band's lower bound.
        for a, b in zip(HYPE_DENSITY_CURVE, HYPE_DENSITY_CURVE[1:]):
            assert a.percentile_high == b.percentile_low
        # First band starts at 0, last ends past 1.
        assert HYPE_DENSITY_CURVE[0].percentile_low == pytest.approx(0.0)
        assert HYPE_DENSITY_CURVE[-1].percentile_high >= 1.0

    def test_density_curve_aligns_with_beat_sheet_boundaries(self) -> None:
        # Every HYPE_DENSITY_CURVE boundary (except the 1.01 sentinel) must
        # appear somewhere in BEAT_SHEET boundaries, so the two engines
        # rhythm-sync.
        beat_boundaries = {b.percentile_low for b in BEAT_SHEET} | {
            b.percentile_high for b in BEAT_SHEET
        }
        for band in HYPE_DENSITY_CURVE:
            for edge in (band.percentile_low, band.percentile_high):
                if edge >= 1.01:
                    continue
                assert edge in beat_boundaries, (
                    f"Hype band edge {edge} not in BEAT_SHEET boundaries"
                )

    def test_golden_three_chapters_require_two_hype_peaks(self) -> None:
        first = HYPE_DENSITY_CURVE[0]
        assert first.percentile_low == pytest.approx(0.0)
        assert first.min_count_per_chapter >= 2


# ---------------------------------------------------------------------------
# score_hype.
# ---------------------------------------------------------------------------


class TestScoreHype:
    def test_all_fives_returns_five(self) -> None:
        components = {k: 5.0 for k in HYPE_SCORE_WEIGHTS}
        assert score_hype(components) == 5.0

    def test_missing_components_default_to_five(self) -> None:
        assert score_hype({}) == 5.0

    def test_clamps_out_of_range_components(self) -> None:
        # 99 should clamp to 10; -5 to 0.
        components = {"setup_contrast": 99.0, "pacing_crisp": -5.0}
        result = score_hype(components)
        # setup_contrast (weight 0.20) at 10 + pacing_crisp (0.15) at 0 +
        # defaults at 5 for the other 4 (weight 0.65).
        expected = 10 * 0.20 + 0 * 0.15 + 5 * 0.65
        assert result == pytest.approx(round(expected, 2))

    def test_weighted_high_components_produce_high_score(self) -> None:
        components = {k: 9.0 for k in HYPE_SCORE_WEIGHTS}
        assert score_hype(components) == pytest.approx(9.0)


# ---------------------------------------------------------------------------
# target_hype_for_chapter.
# ---------------------------------------------------------------------------


class TestTargetHypeForChapter:
    def test_first_chapter_hits_golden_three_band(self) -> None:
        band = target_hype_for_chapter(1, 100)
        assert band.min_count_per_chapter >= 2
        assert HypeType.POWER_REVEAL in band.expected_types

    def test_midpoint_hits_reversal_band(self) -> None:
        band = target_hype_for_chapter(50, 100)
        assert HypeType.REVERSAL in band.expected_types
        assert band.intensity_target >= 8.0

    def test_fast_profile_adds_intensity(self) -> None:
        medium = target_hype_for_chapter(50, 100, "medium")
        fast = target_hype_for_chapter(50, 100, "fast")
        assert fast.intensity_target == pytest.approx(medium.intensity_target + 0.5)

    def test_slow_profile_subtracts_intensity(self) -> None:
        medium = target_hype_for_chapter(50, 100, "medium")
        slow = target_hype_for_chapter(50, 100, "slow")
        assert slow.intensity_target == pytest.approx(medium.intensity_target - 0.5)


# ---------------------------------------------------------------------------
# evaluate_hype_diversity.
# ---------------------------------------------------------------------------


class TestEvaluateHypeDiversity:
    def test_immediate_repeat_is_forbidden(self) -> None:
        diversity = evaluate_hype_diversity([HypeType.FACE_SLAP])
        assert HypeType.FACE_SLAP.value in diversity["forbid_types"]

    def test_two_consecutive_same_type_forbids_that_type(self) -> None:
        diversity = evaluate_hype_diversity(
            [HypeType.FACE_SLAP, HypeType.FACE_SLAP]
        )
        assert HypeType.FACE_SLAP.value in diversity["forbid_types"]

    def test_recent_recipe_keys_are_forbidden(self) -> None:
        diversity = evaluate_hype_diversity(
            [HypeType.FACE_SLAP],
            ["冥符拍脸-当众羞辱反转", "阴兵列阵-当场亮牌"],
        )
        assert "冥符拍脸-当众羞辱反转" in diversity["forbid_recipe_keys"]
        assert "阴兵列阵-当场亮牌" in diversity["forbid_recipe_keys"]

    def test_suggested_prefers_underused_types(self) -> None:
        # Use FACE_SLAP 10 times and nothing else — suggested should
        # rank COMEDIC_BEAT / CARESS_BY_FATE (under-used) high.
        history = [HypeType.FACE_SLAP] * 10
        diversity = evaluate_hype_diversity(history)
        assert HypeType.FACE_SLAP.value not in diversity["suggested"]
        # Top suggestions should not include the forbid list.
        assert set(diversity["suggested"]) & set(diversity["forbid_types"]) == set()

    def test_empty_history_returns_no_forbid(self) -> None:
        diversity = evaluate_hype_diversity([])
        assert diversity["forbid_types"] == []
        assert diversity["forbid_recipe_keys"] == []
        assert len(diversity["suggested"]) == 5


# ---------------------------------------------------------------------------
# select_recipe_for_chapter.
# ---------------------------------------------------------------------------


def _recipe(
    key: str,
    hype_type: HypeType,
    intensity_floor: float = 6.0,
) -> HypeRecipe:
    return HypeRecipe(
        key=key,
        hype_type=hype_type,
        trigger_keywords=("冥符", "阴兵"),
        narrative_beats=("a", "b"),
        intensity_floor=intensity_floor,
        cadence_hint="test",
    )


class TestSelectRecipe:
    def test_empty_deck_returns_none(self) -> None:
        band = target_hype_for_chapter(1, 100)
        assert select_recipe_for_chapter(band, []) is None

    def test_prefers_band_expected_type(self) -> None:
        band = HypeDensityBand(
            percentile_low=0.45,
            percentile_high=0.55,
            expected_types=(HypeType.REVERSAL,),
            min_count_per_chapter=1,
            intensity_target=8.5,
            notes="mid",
        )
        deck = [
            _recipe("comedy-1", HypeType.COMEDIC_BEAT),
            _recipe("reversal-1", HypeType.REVERSAL),
            _recipe("level-up-1", HypeType.LEVEL_UP),
        ]
        chosen = select_recipe_for_chapter(band, deck)
        assert chosen is not None
        assert chosen.hype_type == HypeType.REVERSAL

    def test_skips_recently_used_recipe_key(self) -> None:
        band = HypeDensityBand(
            percentile_low=0.0,
            percentile_high=0.05,
            expected_types=(HypeType.FACE_SLAP,),
            min_count_per_chapter=2,
            intensity_target=7.5,
            notes="golden-three",
        )
        deck = [
            _recipe("face-slap-a", HypeType.FACE_SLAP),
            _recipe("face-slap-b", HypeType.FACE_SLAP),
        ]
        chosen = select_recipe_for_chapter(
            band, deck, recent_recipe_keys=["face-slap-a"],
        )
        assert chosen is not None
        assert chosen.key == "face-slap-b"

    def test_deck_exhaustion_returns_lru(self) -> None:
        band = HypeDensityBand(
            percentile_low=0.0,
            percentile_high=0.05,
            expected_types=(HypeType.FACE_SLAP,),
            min_count_per_chapter=2,
            intensity_target=7.5,
            notes="golden-three",
        )
        deck = [
            _recipe("face-slap-a", HypeType.FACE_SLAP),
            _recipe("face-slap-b", HypeType.FACE_SLAP),
        ]
        # Both recipes used recently — "a" is oldest (further back in list).
        chosen = select_recipe_for_chapter(
            band,
            deck,
            recent_recipe_keys=["face-slap-b", "face-slap-a"],
        )
        assert chosen is not None
        assert chosen.key == "face-slap-a"


# ---------------------------------------------------------------------------
# pick_hype_for_chapter.
# ---------------------------------------------------------------------------


class TestPickHypeForChapter:
    def test_empty_deck_still_returns_type_from_band(self) -> None:
        band = target_hype_for_chapter(1, 100)
        hype_type, recipe, intensity = pick_hype_for_chapter(band, [])
        assert hype_type in band.expected_types
        assert recipe is None
        assert intensity == pytest.approx(band.intensity_target)

    def test_recipe_intensity_floor_overrides_band(self) -> None:
        band = HypeDensityBand(
            percentile_low=0.0,
            percentile_high=0.05,
            expected_types=(HypeType.FACE_SLAP,),
            min_count_per_chapter=2,
            intensity_target=6.0,
            notes="n",
        )
        deck = [_recipe("big-slap", HypeType.FACE_SLAP, intensity_floor=9.0)]
        hype_type, recipe, intensity = pick_hype_for_chapter(band, deck)
        assert hype_type == HypeType.FACE_SLAP
        assert recipe is not None and recipe.key == "big-slap"
        assert intensity == pytest.approx(9.0)  # floor raises above band target


# ---------------------------------------------------------------------------
# HypeScheme / HypeMoment round-trip.
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_hype_scheme_round_trip_preserves_fields(self) -> None:
        scheme = HypeScheme(
            recipe_deck=(
                _recipe("rec-1", HypeType.FACE_SLAP),
                _recipe("rec-2", HypeType.POWER_REVEAL, intensity_floor=8.5),
            ),
            comedic_beat_density_target=0.2,
            payoff_window_chapters=6,
            reader_promise="钱不再是钱",
            selling_points=("诡异", "神豪"),
            hook_keywords=("冥符", "阴兵"),
            chapter_hook_strategy="每章抛一条新诡异资产",
            min_hype_per_chapter=1,
        )
        restored = hype_scheme_from_dict(hype_scheme_to_dict(scheme))
        assert restored == scheme

    def test_hype_moment_round_trip(self) -> None:
        moment = HypeMoment(
            chapter_no=3,
            hype_type=HypeType.POWER_REVEAL,
            recipe_key="阴兵列阵-当场亮牌",
            intensity=8.5,
        )
        restored = hype_moment_from_dict(hype_moment_to_dict(moment))
        assert restored == moment

    def test_empty_scheme_has_is_empty_true(self) -> None:
        scheme = HypeScheme()
        assert scheme.is_empty
        # Round-trip of empty dict produces equivalent empty scheme.
        assert hype_scheme_from_dict({}) == HypeScheme()
        assert hype_scheme_from_dict(None) == HypeScheme()

    def test_malformed_recipe_dict_drops_silently(self) -> None:
        scheme = hype_scheme_from_dict(
            {"recipe_deck": [{"key": "ok", "hype_type": "face_slap"}, {"garbage": True}]}
        )
        assert len(scheme.recipe_deck) == 1
        assert scheme.recipe_deck[0].key == "ok"


# ---------------------------------------------------------------------------
# classify_hype + extract_ending_sentence.
# ---------------------------------------------------------------------------


class TestClassifyHype:
    def test_chinese_face_slap_detected(self) -> None:
        text = "他一巴掌打过去，那人僵住，脸色铁青，哑口无言。"
        result = classify_hype(text, "zh-CN")
        assert result is not None
        hype_type, score = result
        assert hype_type == HypeType.FACE_SLAP
        assert score >= 2.0

    def test_no_keywords_returns_none(self) -> None:
        # Pure atmospheric text with no hype cues.
        text = "清晨的雾气缓缓散去，他端起茶杯，慢慢品了一口。"
        assert classify_hype(text, "zh-CN") is None

    def test_segment_head_only_looks_at_start(self) -> None:
        # Hype is at the tail — head segment should miss it.
        text = ("平淡的叙述。" * 200) + "阴兵列阵，真身亮相"
        assert classify_hype(text, "zh-CN", segment="head", head_chars=200) is None
        full = classify_hype(text, "zh-CN", segment="full")
        assert full is not None and full[0] == HypeType.POWER_REVEAL


class TestExtractEndingSentence:
    def test_chinese_last_sentence(self) -> None:
        text = "清晨的雾气散去。他端起茶杯。门外一道身影越来越近。"
        assert extract_ending_sentence(text, "zh-CN") == "门外一道身影越来越近"

    def test_english_last_sentence(self) -> None:
        text = "The morning broke. He reached for the door. A shadow waited."
        assert extract_ending_sentence(text, "en") == "A shadow waited"

    def test_empty_returns_empty(self) -> None:
        assert extract_ending_sentence("", "zh-CN") == ""
        assert extract_ending_sentence("   \n\n  ", "zh-CN") == ""

    def test_no_terminator_falls_back_to_last_line(self) -> None:
        text = "第一行\n第二行\n最后一行没有句号"
        assert extract_ending_sentence(text, "zh-CN") == "最后一行没有句号"
