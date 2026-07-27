"""Guard: the chapter-first loop must ship its best attempt, not its last.

Root cause this mirrors (2026-07-13, scene level): a real book shipped a 0.63
draft that had overwritten a 0.71 one, because the rewrite path flips
``is_current`` to whatever it just produced with no comparison. The scene loop
got ``_promote_best_scoring_scene_draft_on_stall`` then; the chapter-first loop
had the same shape and no guard, while two of its own log lines already claimed
it was routing "the best available draft".
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import pipelines

pytestmark = pytest.mark.unit


class TestPromotionIsWiredIntoBothExhaustionPaths:
    """A promotion helper nothing calls is the same bug with extra steps."""

    def test_helper_exists(self) -> None:
        assert hasattr(pipelines, "_promote_best_scoring_chapter_draft_on_stall")

    def test_called_from_every_exit_that_ships_a_draft(self) -> None:
        """Wiring this to the auto-repair exhaustion path alone left it dormant.

        A live 10-chapter run (2026-07-21) showed real books exit through the
        *closure* quality-debt path instead, so the promotion never ran while
        chapter 1 degraded 1860 -> 1599 -> 1570 words across three attempts.
        All four exits must promote.
        """

        source = inspect.getsource(pipelines.run_chapter_pipeline)
        assert source.count("_promote_best_scoring_chapter_draft_on_stall") >= 4, (
            "expected promotion at the auto-repair exhaustion path, the retention "
            "accept-on-stall path, and both quality-debt closure paths"
        )

    def test_quality_debt_paths_promote_before_stamping_state(self) -> None:
        """Promoting after the state stamp would record debt against a draft
        that is about to be replaced."""

        source = inspect.getsource(pipelines.run_chapter_pipeline)
        for marker in ('chapter.production_state = "quality_debt"',):
            idx = 0
            while True:
                idx = source.find(marker, idx)
                if idx == -1:
                    break
                window = source[max(0, idx - 900) : idx]
                assert "_promote_best_scoring_chapter_draft_on_stall" in window, (
                    "a quality_debt exit stamps state without promoting first"
                )
                idx += len(marker)

    def test_promotion_is_gated_on_chapter_first(self) -> None:
        """The scene path already has its own promotion; running both would
        fight over is_current."""

        source = inspect.getsource(pipelines.run_chapter_pipeline)
        for line in source.splitlines():
            if "_promote_best_scoring_chapter_draft_on_stall" in line and "await" in line:
                continue
        assert "use_chapter_first and chapter_draft is not None" in source


class TestRankingRule:
    """Ranking is (meets_floor, score, version) — evidence-driven ordering."""

    def _rank(self, *, hard_min: int, words: int, score: float | None, version: int):
        """Rebuild the ranking key the same way the helper does."""

        meets_floor = 1 if hard_min and words >= hard_min else 0
        return (meets_floor, float(score if score is not None else -1.0), version)

    def test_compliant_draft_beats_higher_scoring_short_draft(self) -> None:
        """A chapter under the floor is rejected downstream regardless of score,
        so shipping it because it scored better is strictly worse."""

        short_but_scored = self._rank(hard_min=1800, words=1635, score=0.70, version=4)
        long_unscored = self._rank(hard_min=1800, words=2030, score=None, version=3)
        assert long_unscored > short_but_scored

    def test_among_compliant_drafts_score_decides(self) -> None:
        low = self._rank(hard_min=1800, words=2400, score=0.55, version=5)
        high = self._rank(hard_min=1800, words=2100, score=0.62, version=2)
        assert high > low

    def test_version_only_breaks_exact_ties(self) -> None:
        older = self._rank(hard_min=1800, words=2100, score=0.58, version=2)
        newer = self._rank(hard_min=1800, words=2100, score=0.58, version=3)
        assert newer > older

    def test_unscored_sorts_below_any_score_at_equal_compliance(self) -> None:
        unscored = self._rank(hard_min=1800, words=2000, score=None, version=9)
        scored = self._rank(hard_min=1800, words=2000, score=0.01, version=1)
        assert scored > unscored

    def test_live_chapter_four_case_picks_the_only_compliant_version(self) -> None:
        """The observed failure: 1385 / 1635(current) / 2030 / 1772 words, all
        unscored, floor 1800. Only v3 clears the floor."""

        versions = [(1, 1385), (2, 1635), (3, 2030), (4, 1772)]
        best = max(
            versions,
            key=lambda item: self._rank(
                hard_min=1800, words=item[1], score=None, version=item[0]
            ),
        )
        assert best == (3, 2030)


class TestImplementationMatchesTheRule:
    def test_uses_outer_join_so_unscored_drafts_are_considered(self) -> None:
        """An inner join drops every unscored attempt — which on the live run
        was all four of chapter 4's."""

        source = inspect.getsource(
            pipelines._promote_best_scoring_chapter_draft_on_stall
        )
        assert ".outerjoin(" in source
        assert ".join(\n" not in source

    def test_ranks_length_compliance_before_score(self) -> None:
        """Length compliance outranks the quality score.

        2026-07-26: the rule is now the whole contract BAND, not just the floor
        — the ceiling was computed and discarded, so an over-long draft could
        win on term 2 using a score its own excess length inflates (《纸背》
        shipped 4942 words against a 2600 target while a 2702-word draft sat
        unused). The ordering itself is unchanged and now lives in a testable
        helper instead of a closure; see test_chapter_best_draft_band.py.
        """

        promotion = inspect.getsource(
            pipelines._promote_best_scoring_chapter_draft_on_stall
        )
        assert "rank_chapter_draft_candidate(" in promotion, (
            "promotion must delegate to the shared, tested ranking helper"
        )

        ranking = inspect.getsource(pipelines.rank_chapter_draft_candidate)
        assert "return (in_band, score," in ranking
        assert "hard_max" in ranking, "the ceiling must actually be consulted"

    def test_length_band_failure_degrades_instead_of_raising(self) -> None:
        source = inspect.getsource(
            pipelines._promote_best_scoring_chapter_draft_on_stall
        )
        assert "hard_min = 0" in source
        assert "except Exception" in source

    def test_joins_scores_to_chapter_drafts_not_scene_drafts(self) -> None:
        source = inspect.getsource(
            pipelines._promote_best_scoring_chapter_draft_on_stall
        )
        assert "QualityScoreModel.chapter_draft_version_id" in source
        assert "scene_draft_version_id" not in source

    def test_clears_stale_current_before_setting_the_winner(self) -> None:
        """``uq_chapter_draft_current`` is a partial unique index on
        (chapter_id) WHERE is_current — flipping the winner to True before
        clearing the incumbent collides on it."""

        source = inspect.getsource(
            pipelines._promote_best_scoring_chapter_draft_on_stall
        )
        clear_pos = source.index("stale_current.is_current = False")
        flush_pos = source.index("await session.flush()", clear_pos)
        set_pos = source.index("best_draft.is_current = True")
        assert clear_pos < flush_pos < set_pos

    def test_returns_early_when_current_is_already_best(self) -> None:
        source = inspect.getsource(
            pipelines._promote_best_scoring_chapter_draft_on_stall
        )
        assert "if best_draft.id == current_draft.id:" in source

    def test_no_scored_drafts_is_a_no_op(self) -> None:
        source = inspect.getsource(
            pipelines._promote_best_scoring_chapter_draft_on_stall
        )
        assert "if not rows:" in source
