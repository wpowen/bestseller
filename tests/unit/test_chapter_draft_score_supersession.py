"""Best-of-N must rank on a draft's *current* score, not a superseded one.

``_promote_best_scoring_chapter_draft_on_stall`` outer-joins ``quality_scores``
with no ``is_current`` predicate, so a draft that has been re-scored appears
once per score row — each carrying a different number. ``max()`` then picks the
row with the highest score, which may be a verdict that has already been
replaced.

Observed 2026-07-26 on xianxia-upgrade-1785036205 ch1: draft v2 carried two
``chapter_quality_v1`` rows for evaluation_round 1, one ``is_current=false`` and
one ``is_current=true``. They happened to agree at 0.59, so nothing shipped
wrong — but re-scoring after a repair pass is exactly the operation that makes
them disagree, and the stale, higher number would win.

A hard ``is_current`` filter is the wrong fix: 5 of the 15 scored drafts in the
live database have *only* superseded rows, and filtering would silently
demote them to "unscored". Collapse to one row per draft instead, preferring
the current verdict and falling back to the most recent one.
"""

from __future__ import annotations

import datetime as dt

import pytest

from bestseller.services.pipelines import dedupe_drafts_by_current_score

pytestmark = pytest.mark.unit


class _Draft:
    def __init__(self, draft_id: str) -> None:
        self.id = draft_id


class _Score:
    def __init__(
        self,
        score: float,
        *,
        is_current: bool,
        created_at: dt.datetime | None = None,
    ) -> None:
        self.score_overall = score
        self.is_current = is_current
        self.created_at = created_at or dt.datetime(2026, 7, 26, 12, 0)


def _scores(pairs):
    return {draft.id: quality for draft, quality in dedupe_drafts_by_current_score(pairs)}


class TestSupersededScoresLose:
    def test_current_score_wins_over_a_higher_stale_one(self) -> None:
        """The failure mode: repair lowered the verdict, the old one is higher."""

        draft = _Draft("d1")
        stale = _Score(0.71, is_current=False)
        current = _Score(0.55, is_current=True)

        assert _scores([(draft, stale), (draft, current)])["d1"] is current

    def test_current_score_wins_regardless_of_row_order(self) -> None:
        draft = _Draft("d1")
        current = _Score(0.55, is_current=True)
        stale = _Score(0.71, is_current=False)

        assert _scores([(draft, current), (draft, stale)])["d1"] is current


class TestNoDraftIsLost:
    def test_draft_with_only_superseded_scores_keeps_its_best_evidence(self) -> None:
        """5 live drafts are in this state — they must not become 'unscored'."""

        draft = _Draft("d1")
        older = _Score(0.40, is_current=False, created_at=dt.datetime(2026, 7, 26, 10, 0))
        newer = _Score(0.62, is_current=False, created_at=dt.datetime(2026, 7, 26, 11, 0))

        assert _scores([(draft, older), (draft, newer)])["d1"] is newer

    def test_unscored_draft_survives_as_unscored(self) -> None:
        draft = _Draft("d1")

        result = _scores([(draft, None)])
        assert "d1" in result
        assert result["d1"] is None

    def test_every_distinct_draft_appears_exactly_once(self) -> None:
        a, b, c = _Draft("a"), _Draft("b"), _Draft("c")
        pairs = [
            (a, _Score(0.5, is_current=False)),
            (a, _Score(0.6, is_current=True)),
            (b, None),
            (c, _Score(0.7, is_current=True)),
        ]

        collapsed = list(dedupe_drafts_by_current_score(pairs))
        assert sorted(draft.id for draft, _ in collapsed) == ["a", "b", "c"]

    def test_a_real_score_beats_a_missing_one_for_the_same_draft(self) -> None:
        """An outer join can yield a NULL row alongside a real one."""

        draft = _Draft("d1")
        scored = _Score(0.5, is_current=False)

        assert _scores([(draft, None), (draft, scored)])["d1"] is scored


class TestDegradesSafely:
    def test_missing_is_current_attribute_does_not_raise(self) -> None:
        class _Bare:
            score_overall = 0.5

        draft = _Draft("d1")
        collapsed = list(dedupe_drafts_by_current_score([(draft, _Bare())]))
        assert len(collapsed) == 1

    def test_missing_created_at_does_not_raise(self) -> None:
        class _NoTimestamp:
            score_overall = 0.5
            is_current = False

        draft = _Draft("d1")
        collapsed = list(
            dedupe_drafts_by_current_score([(draft, _NoTimestamp()), (draft, None)])
        )
        assert len(collapsed) == 1
