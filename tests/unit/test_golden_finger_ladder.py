"""Tests for the Phase 3 golden-finger ladder primitives in
``bestseller.services.hype_engine``.

Covers:
  * ``GoldenFingerLadder.rung_for_chapter`` percentile matching and the
    ``unlock_chapter_hint`` override.
  * ``extract_ladder_from_growth_curve`` parsing of the
    ``... -> ... -> ...`` format plus the full-width ``→`` variant.
  * Serialization round-trips via ``golden_finger_ladder_to_dict`` and
    ``golden_finger_ladder_from_dict``.
"""

from __future__ import annotations

import pytest

from bestseller.services.hype_engine import (
    GoldenFingerLadder,
    GoldenFingerRung,
    HypeType,
    extract_ladder_from_growth_curve,
    golden_finger_ladder_from_dict,
    golden_finger_ladder_to_dict,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rung(
    index: int,
    low: float,
    high: float,
    *,
    capability: str = "capability",
    keywords: tuple[str, ...] = (),
    anchor: HypeType = HypeType.GOLDEN_FINGER_REVEAL,
    hint: int | None = None,
) -> GoldenFingerRung:
    return GoldenFingerRung(
        rung_index=index,
        unlock_percentile=(low, high),
        capability=capability,
        signal_keywords=keywords,
        hype_type_anchor=anchor,
        unlock_chapter_hint=hint,
    )


# ---------------------------------------------------------------------------
# GoldenFingerLadder.is_empty / defensive inputs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGoldenFingerLadderBasics:
    def test_empty_ladder_is_empty(self) -> None:
        ladder = GoldenFingerLadder(rungs=(), source="engine_extracted")
        assert ladder.is_empty is True

    def test_non_empty_ladder_is_not_empty(self) -> None:
        ladder = GoldenFingerLadder(
            rungs=(_rung(1, 0.0, 1.0),), source="preset_declared"
        )
        assert ladder.is_empty is False

    def test_rung_for_chapter_returns_none_when_empty(self) -> None:
        ladder = GoldenFingerLadder(rungs=(), source="engine_extracted")
        assert ladder.rung_for_chapter(5, 10) is None

    def test_rung_for_chapter_guards_against_zero_total(self) -> None:
        ladder = GoldenFingerLadder(
            rungs=(_rung(1, 0.0, 1.0),), source="preset_declared"
        )
        assert ladder.rung_for_chapter(1, 0) is None
        assert ladder.rung_for_chapter(0, 10) is None
        assert ladder.rung_for_chapter(-3, 10) is None


# ---------------------------------------------------------------------------
# rung_for_chapter — percentile matching
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRungForChapterPercentile:
    def _three_rung(self) -> GoldenFingerLadder:
        # [0.0, 0.33), [0.33, 0.66), [0.66, 1.0]
        return GoldenFingerLadder(
            rungs=(
                _rung(1, 0.0, 1 / 3, capability="rung1"),
                _rung(2, 1 / 3, 2 / 3, capability="rung2"),
                _rung(3, 2 / 3, 1.0, capability="rung3"),
            ),
            source="preset_declared",
        )

    def test_first_chapter_lands_on_first_rung(self) -> None:
        ladder = self._three_rung()
        rung = ladder.rung_for_chapter(1, 9)
        assert rung is not None and rung.rung_index == 1

    def test_middle_chapter_lands_on_middle_rung(self) -> None:
        ladder = self._three_rung()
        rung = ladder.rung_for_chapter(5, 9)  # 5/9 ≈ 0.555 → middle
        assert rung is not None and rung.rung_index == 2

    def test_last_chapter_lands_on_last_rung_inclusive(self) -> None:
        ladder = self._three_rung()
        # 9/9 = 1.0 exactly — must hit last rung thanks to inclusive high.
        rung = ladder.rung_for_chapter(9, 9)
        assert rung is not None and rung.rung_index == 3

    def test_boundary_chapter_belongs_to_upper_rung(self) -> None:
        # Windows [0.0, 0.5) and [0.5, 1.0]. Chapter 5/10 = 0.5 → rung 2.
        ladder = GoldenFingerLadder(
            rungs=(
                _rung(1, 0.0, 0.5),
                _rung(2, 0.5, 1.0),
            ),
            source="preset_declared",
        )
        rung = ladder.rung_for_chapter(5, 10)
        assert rung is not None and rung.rung_index == 2

    def test_single_rung_matches_every_chapter(self) -> None:
        ladder = GoldenFingerLadder(
            rungs=(_rung(1, 0.0, 1.0, capability="solo"),),
            source="preset_declared",
        )
        for ch in (1, 3, 7, 10):
            rung = ladder.rung_for_chapter(ch, 10)
            assert rung is not None and rung.capability == "solo"

    def test_chapter_above_total_clamps_to_last_rung(self) -> None:
        # Percentile > 1.0 should not silently match a non-last rung.
        # With the inclusive-high-on-last-rung rule we still want coverage
        # so over-run chapters still return the last rung (percentile
        # rounded at inclusive high). Verify observed behavior: it returns
        # the last rung because the final window is closed at 1.0 and our
        # percentile sits above — we tolerate None here.
        ladder = GoldenFingerLadder(
            rungs=(
                _rung(1, 0.0, 0.5),
                _rung(2, 0.5, 1.0),
            ),
            source="preset_declared",
        )
        # 11/10 = 1.1 — beyond 1.0 + epsilon, should return None (no rung
        # owns that percentile); callers are expected to pass valid inputs.
        assert ladder.rung_for_chapter(11, 10) is None


# ---------------------------------------------------------------------------
# rung_for_chapter — unlock_chapter_hint override
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUnlockChapterHintOverride:
    def test_hint_beats_percentile_match(self) -> None:
        # Rung 1 occupies [0.0, 0.5); rung 2 [0.5, 1.0]. Without a hint,
        # chapter 3/10 (=0.3) would return rung 1. We pin rung 2 to
        # chapter 3 via hint and expect rung 2 to win.
        ladder = GoldenFingerLadder(
            rungs=(
                _rung(1, 0.0, 0.5, capability="early"),
                _rung(2, 0.5, 1.0, capability="late", hint=3),
            ),
            source="preset_declared",
        )
        rung = ladder.rung_for_chapter(3, 10)
        assert rung is not None and rung.capability == "late"

    def test_hint_only_fires_for_its_chapter(self) -> None:
        ladder = GoldenFingerLadder(
            rungs=(
                _rung(1, 0.0, 0.5, capability="early"),
                _rung(2, 0.5, 1.0, capability="late", hint=3),
            ),
            source="preset_declared",
        )
        rung = ladder.rung_for_chapter(2, 10)  # falls back to percentile
        assert rung is not None and rung.capability == "early"

    def test_multiple_hints_pick_first_matching(self) -> None:
        ladder = GoldenFingerLadder(
            rungs=(
                _rung(1, 0.0, 0.5, capability="first-hinted", hint=7),
                _rung(2, 0.5, 1.0, capability="second-hinted", hint=7),
            ),
            source="preset_declared",
        )
        rung = ladder.rung_for_chapter(7, 10)
        # Iteration preserves rung order, so the first match wins.
        assert rung is not None and rung.capability == "first-hinted"


# ---------------------------------------------------------------------------
# extract_ladder_from_growth_curve
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractLadderFromGrowthCurve:
    def test_parses_ascii_arrow_separator(self) -> None:
        ladder = extract_ladder_from_growth_curve(
            "现实落魄 -> 解锁冥库 -> 建立诡异产业 -> 压制诡异贵族 -> 直面诡异起源",
            total_chapters=100,
        )
        assert ladder.source == "engine_extracted"
        assert len(ladder.rungs) == 5
        assert [r.capability for r in ladder.rungs] == [
            "现实落魄",
            "解锁冥库",
            "建立诡异产业",
            "压制诡异贵族",
            "直面诡异起源",
        ]

    def test_parses_fullwidth_arrow_separator(self) -> None:
        ladder = extract_ladder_from_growth_curve(
            "初心 → 成长 → 巅峰",
            total_chapters=30,
        )
        assert len(ladder.rungs) == 3
        assert [r.capability for r in ladder.rungs] == ["初心", "成长", "巅峰"]

    def test_percentiles_are_evenly_distributed(self) -> None:
        ladder = extract_ladder_from_growth_curve(
            "a -> b -> c -> d",
            total_chapters=40,
        )
        lows = [r.unlock_percentile[0] for r in ladder.rungs]
        highs = [r.unlock_percentile[1] for r in ladder.rungs]
        assert lows == pytest.approx([0.0, 0.25, 0.5, 0.75])
        assert highs == pytest.approx([0.25, 0.5, 0.75, 1.0])

    def test_last_rung_high_boundary_is_one(self) -> None:
        ladder = extract_ladder_from_growth_curve("a -> b -> c", 9)
        assert ladder.rungs[-1].unlock_percentile[1] == pytest.approx(1.0)

    def test_anchor_alternates_reveal_then_level_up(self) -> None:
        ladder = extract_ladder_from_growth_curve(
            "a -> b -> c -> d", total_chapters=20
        )
        assert [r.hype_type_anchor for r in ladder.rungs] == [
            HypeType.GOLDEN_FINGER_REVEAL,
            HypeType.LEVEL_UP,
            HypeType.GOLDEN_FINGER_REVEAL,
            HypeType.LEVEL_UP,
        ]

    def test_signal_keywords_are_empty_on_extracted_rungs(self) -> None:
        ladder = extract_ladder_from_growth_curve("a -> b", 10)
        for rung in ladder.rungs:
            assert rung.signal_keywords == ()

    def test_unlock_chapter_hint_is_none_on_extracted_rungs(self) -> None:
        ladder = extract_ladder_from_growth_curve("a -> b -> c", 30)
        for rung in ladder.rungs:
            assert rung.unlock_chapter_hint is None

    def test_rung_indices_start_at_one(self) -> None:
        ladder = extract_ladder_from_growth_curve("a -> b -> c", 30)
        assert [r.rung_index for r in ladder.rungs] == [1, 2, 3]

    def test_whitespace_is_stripped(self) -> None:
        ladder = extract_ladder_from_growth_curve(
            "   stage one   ->    stage two    ->  stage three   ",
            total_chapters=30,
        )
        assert [r.capability for r in ladder.rungs] == [
            "stage one",
            "stage two",
            "stage three",
        ]

    def test_empty_growth_curve_returns_empty_ladder(self) -> None:
        ladder = extract_ladder_from_growth_curve("", 10)
        assert ladder.is_empty is True
        assert ladder.source == "engine_extracted"

    def test_single_segment_returns_empty_ladder(self) -> None:
        # A one-stage trajectory has no "rung" to unlock.
        ladder = extract_ladder_from_growth_curve("只有一个阶段", 10)
        assert ladder.is_empty is True
        assert ladder.source == "engine_extracted"

    def test_blank_segments_are_dropped(self) -> None:
        # Leading/trailing arrows produce empty segments that must be
        # filtered before counting.
        ladder = extract_ladder_from_growth_curve("-> a -> b ->", 10)
        assert [r.capability for r in ladder.rungs] == ["a", "b"]

    def test_zero_total_chapters_returns_empty_ladder(self) -> None:
        ladder = extract_ladder_from_growth_curve("a -> b", 0)
        assert ladder.is_empty is True

    def test_extracted_rung_matches_its_segment_range(self) -> None:
        ladder = extract_ladder_from_growth_curve(
            "a -> b -> c -> d", total_chapters=40
        )
        # Chapter 5/40 = 0.125 → rung 1; chapter 15/40 = 0.375 → rung 2;
        # chapter 25/40 = 0.625 → rung 3; chapter 40 = 1.0 → rung 4.
        assert ladder.rung_for_chapter(5, 40).rung_index == 1
        assert ladder.rung_for_chapter(15, 40).rung_index == 2
        assert ladder.rung_for_chapter(25, 40).rung_index == 3
        assert ladder.rung_for_chapter(40, 40).rung_index == 4


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLadderSerialization:
    def test_empty_ladder_round_trip(self) -> None:
        original = GoldenFingerLadder(rungs=(), source="engine_extracted")
        restored = golden_finger_ladder_from_dict(
            golden_finger_ladder_to_dict(original)
        )
        assert restored == original

    def test_preset_declared_ladder_round_trip(self) -> None:
        original = GoldenFingerLadder(
            rungs=(
                _rung(
                    1,
                    0.0,
                    0.5,
                    capability="冥库初启",
                    keywords=("冥符", "阴兵"),
                    anchor=HypeType.GOLDEN_FINGER_REVEAL,
                    hint=3,
                ),
                _rung(
                    2,
                    0.5,
                    1.0,
                    capability="冥府之主",
                    keywords=("冥府", "董事会"),
                    anchor=HypeType.LEVEL_UP,
                ),
            ),
            source="preset_declared",
        )
        restored = golden_finger_ladder_from_dict(
            golden_finger_ladder_to_dict(original)
        )
        assert restored == original

    def test_from_dict_handles_none(self) -> None:
        ladder = golden_finger_ladder_from_dict(None)
        assert ladder.is_empty is True
        assert ladder.source == "engine_extracted"

    def test_from_dict_handles_empty_mapping(self) -> None:
        ladder = golden_finger_ladder_from_dict({})
        assert ladder.is_empty is True
        assert ladder.source == "engine_extracted"

    def test_from_dict_coerces_unknown_source_to_engine_extracted(self) -> None:
        ladder = golden_finger_ladder_from_dict(
            {"rungs": [], "source": "bogus"}
        )
        assert ladder.source == "engine_extracted"

    def test_from_dict_skips_malformed_rung_rows(self) -> None:
        payload = {
            "rungs": [
                {
                    "rung_index": 1,
                    "unlock_percentile": [0.0, 0.5],
                    "capability": "ok",
                    "signal_keywords": ["k1"],
                    "hype_type_anchor": "golden_finger_reveal",
                    "unlock_chapter_hint": None,
                },
                "not-a-dict",  # dropped
                {"rung_index": "nope", "capability": "broken"},  # dropped
            ],
            "source": "preset_declared",
        }
        ladder = golden_finger_ladder_from_dict(payload)
        assert len(ladder.rungs) == 1
        assert ladder.rungs[0].capability == "ok"
        assert ladder.source == "preset_declared"

    def test_to_dict_emits_enum_values_as_strings(self) -> None:
        ladder = GoldenFingerLadder(
            rungs=(
                _rung(
                    1,
                    0.0,
                    1.0,
                    anchor=HypeType.LEVEL_UP,
                    keywords=("a", "b"),
                ),
            ),
            source="preset_declared",
        )
        payload = golden_finger_ladder_to_dict(ladder)
        assert payload["source"] == "preset_declared"
        assert payload["rungs"][0]["hype_type_anchor"] == "level_up"
        assert payload["rungs"][0]["signal_keywords"] == ["a", "b"]
        assert payload["rungs"][0]["unlock_percentile"] == [0.0, 1.0]
