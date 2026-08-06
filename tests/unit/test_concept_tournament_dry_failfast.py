"""Dry concept tournament must fail fast, not coast into a guaranteed reject.

Field failure (2026-07-24 21:28, custom-xuanhuan-1784899694): a bare-genre
quickstart (auto premise "基于玄幻（玄幻）题材…") ran the tournament twice,
both attempts ended winner=None with every candidate rejected by the hook hard
gate — and conception CONTINUED anyway ("no injection"), burning ~10 minutes of
finalize/world/character/copywriting/persona calls before the logline gate
rejected the result at 3.0 using the same judged evidence. The user saw a
confusing logline verdict instead of the real cause (no qualified concept),
and asked "书是不是又丢了".

The fail-fast for winner=None already existed — but only for chapter_count
>= 200. Short books were left to coast on a known 2/3 death rate (the code
comment records it). These tests pin the extension: a dry tournament with NO
substantive story seed stops conception immediately for every length, carrying
the tournament's own rejection reasons. Creations that DID supply user-owned
story material (explicit seed / concept bundle / hook spec) may still continue.
A Story Architect seed is generated material and must pass the same tournament
gates; it cannot waive them.
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services.concept_tournament import (
    ConceptCandidate,
    dry_tournament_rejection_summary,
)

pytestmark = pytest.mark.unit


def _candidate(concept: str, reason: str | None) -> ConceptCandidate:
    return ConceptCandidate(
        dimension="资源分配#1:identity",
        concept=concept,
        rejected_reason=reason,
    )


class TestDryTournamentRejectionSummary:
    def test_summarizes_each_rejected_candidate(self) -> None:
        lines = dry_tournament_rejection_summary(
            [
                _candidate("拾荒女靠捡散修破烂喂饱童子", "钩子硬门失败: 新颖度/想点欲"),
                _candidate("分拣死人遗物听见剑灵开口", "钩子硬门失败: 新颖度"),
            ]
        )

        assert len(lines) == 2
        assert "拾荒女" in lines[0]
        assert "新颖度/想点欲" in lines[0]

    def test_missing_reason_still_produces_a_line(self) -> None:
        lines = dry_tournament_rejection_summary([_candidate("某概念", None)])

        assert len(lines) == 1
        assert "某概念" in lines[0]

    def test_empty_candidates_yields_a_generic_line(self) -> None:
        """Zero candidates (screen-dry path) must not produce an empty error —
        the user still needs to see SOMETHING actionable."""

        lines = dry_tournament_rejection_summary([])

        assert lines, "an empty rejection summary hides the failure cause"

    def test_output_is_bounded(self) -> None:
        """A wild-mode run can carry many candidates; the user-facing error
        must not become a wall of text."""

        many = [_candidate(f"概念{i}", "钩子硬门失败: 新颖度") for i in range(20)]

        lines = dry_tournament_rejection_summary(many)

        assert len(lines) <= 6

    def test_long_concepts_are_truncated(self) -> None:
        lines = dry_tournament_rejection_summary(
            [_candidate("八" * 300, "钩子硬门失败: 新颖度")]
        )

        assert all(len(line) < 200 for line in lines)


class TestConceptionDryTournamentFailFast:
    """Structural pins on the inlined branch in run_conception_pipeline.

    House convention (test_persona_click_judge_wiring.py): the ~4000-line
    pipeline can't be end-to-end mocked cheaply, so pin the control-flow
    anchors that must not be silently reverted.
    """

    @staticmethod
    def _no_winner_branch() -> str:
        from bestseller.services import conception as conception_services

        source = inspect.getsource(conception_services.run_conception_pipeline)
        start = source.index("Concept tournament produced no winner")
        end = source.index("except Exception as exc:", start)
        return source[start:end]

    def test_short_books_without_any_story_seed_also_abort(self) -> None:
        branch = self._no_winner_branch()

        assert "_has_substantive_story_seed" in branch, (
            "the no-winner branch must distinguish bare quickstarts from "
            "creations that carry real story material"
        )
        assert "raise ConceptContractError" in branch
        assert "chapter_count >= 200 or not _has_substantive_story_seed" in branch, (
            "fail-fast must cover every length when there is no story seed — "
            "coasting on a generic genre card is a guaranteed logline reject "
            "(2026-07-24: 3.0 reject after 10 wasted minutes)"
        )

    def test_abort_message_carries_tournament_evidence(self) -> None:
        branch = self._no_winner_branch()

        assert "dry_tournament_rejection_summary" in branch, (
            "the visible error must carry WHY candidates were rejected, not "
            "just that they were — misattribution to the logline gate is what "
            "made the user think the book was lost"
        )

    def test_substantive_seed_definition_matches_the_tournament_inputs(self) -> None:
        """Only user-owned story material may keep a dry run alive."""

        branch = self._no_winner_branch()

        assert "explicit_concept_seed" in branch
        assert "concept_bundle" in branch
        assert "selected_hook_spec" in branch
        definition = branch[
            branch.index("_has_substantive_story_seed") : branch.index(
                "if chapter_count", branch.index("_has_substantive_story_seed")
            )
        ]
        assert "automatic_story_seed" not in definition
