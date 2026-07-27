"""Best-of-N must judge the whole length band, not just the floor.

``_promote_best_scoring_chapter_draft_on_stall`` already exists — it was added
in 2026-07-21 after a chapter shipped a 1635-word draft while a compliant
2030-word attempt sat unused. Its first ranking term is ``meets_floor``.

Only the floor. The ceiling was computed and thrown away
(``hard_min, _target, _hard_max = _chapter_length_contract_band(...)``).

Consequence, observed 2026-07-26 on urban-power-reversal-1785030326 ch1《纸背》
(target 2600):

    v1  2702 words  ← inside the band
    v2  4942 words  ← SHIPPED, 90% over, chapter marked quality_debt
    v3  4113 words

Every draft cleared the floor, so term 1 could not discriminate; term 2 is the
quality score, and a longer draft tends to score higher on content-signal
metrics simply by containing more text. The over-long draft won on the very
metric its over-length inflates.

Symmetry is the fix: a draft outside the contract band — either end — loses to
one inside it, exactly as the floor rule already intended.
"""

from __future__ import annotations

import pytest

from bestseller.services.pipelines import rank_chapter_draft_candidate


pytestmark = pytest.mark.unit


class _Draft:
    def __init__(self, words: int, version: int) -> None:
        self.word_count = words
        self.version_no = version


class _Quality:
    def __init__(self, score: float | None) -> None:
        self.score_overall = score


def _rank(words: int, version: int, score: float | None, *, band=(1800, 2600, 3500)):
    hard_min, target, hard_max = band
    return rank_chapter_draft_candidate(
        _Draft(words, version),
        _Quality(score),
        hard_min=hard_min,
        hard_max=hard_max,
        target_words=target,
    )


class TestBandCompliance:
    def test_in_band_beats_over_long_even_with_a_lower_score(self) -> None:
        """THE field case: 2702 in-band vs 4942 over-long with a better score."""

        in_band = _rank(2702, version=1, score=0.61)
        over_long = _rank(4942, version=2, score=0.78)

        assert in_band > over_long

    def test_in_band_beats_too_short_even_with_a_lower_score(self) -> None:
        """The original 2026-07-21 case must keep working."""

        in_band = _rank(2030, version=3, score=0.55)
        too_short = _rank(1635, version=2, score=0.72)

        assert in_band > too_short

    def test_both_outside_the_band_falls_through_to_score(self) -> None:
        higher = _rank(4900, version=1, score=0.80)
        lower = _rank(4800, version=2, score=0.40)

        assert higher > lower


class TestExistingBehaviourPreserved:
    def test_among_in_band_drafts_the_better_score_wins(self) -> None:
        better = _rank(2400, version=1, score=0.80)
        worse = _rank(2600, version=2, score=0.50)

        assert better > worse

    def test_equal_scores_prefer_the_closer_length(self) -> None:
        closer = _rank(2600, version=1, score=0.70)
        farther = _rank(3400, version=2, score=0.70)

        assert closer > farther

    def test_unscored_drafts_sort_below_scored_ones(self) -> None:
        scored = _rank(2600, version=1, score=0.10)
        unscored = _rank(2600, version=2, score=None)

        assert scored > unscored

    def test_version_number_is_the_final_tiebreak(self) -> None:
        newer = _rank(2600, version=5, score=0.70)
        older = _rank(2600, version=2, score=0.70)

        assert newer > older


class TestDegradesSafely:
    def test_no_band_known_does_not_invent_a_preference(self) -> None:
        a = _rank(9000, version=1, score=0.70, band=(0, 0, 0))
        b = _rank(2600, version=2, score=0.70, band=(0, 0, 0))

        # Falls through to version tiebreak; neither length is preferred.
        assert b > a

    def test_only_a_floor_known_still_rejects_short_drafts(self) -> None:
        compliant = _rank(2600, version=1, score=0.50, band=(1800, 2600, 0))
        short = _rank(1200, version=2, score=0.90, band=(1800, 2600, 0))

        assert compliant > short
