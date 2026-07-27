"""The design snapshot must find the protagonist in a real premise.

Field failure (2026-07-25, 《仇人膝上养帝王》): the book reached planning and
then died with ``book_design_consistency_failed`` —
``protagonist_identity_mismatch: expected 李玄, actual 姬衡``.

Chain of causation:

1. The tournament champion concept carried NO name ("二十岁的废太子被仇人抱在
   怀里喂奶…"), so every downstream step invented its own.
2. ``premise`` / ``synopsis`` and ALL 23 planning artifacts settled on 姬衡.
   The early concept artifacts (story_spine / hook_card / concept_contract /
   seriality_proof, 9 fields) had already settled on 李玄.
3. ``extract_creation_protagonist_name`` tries the premise FIRST — which is the
   right priority, since the premise is the user-facing story statement that
   planning also reads. But ``_protagonist_name_from_text`` anchors its regex
   at ``^``, so it only ever matched a premise that OPENS with the bare name.
   The real premise reads "前世眼看就要亲政登基的废太子姬衡，…" — name after a
   descriptive clause — so extraction returned "" and fell through to
   story_spine, capturing the MINORITY name.
4. Snapshot(李玄) vs identity_manifest(姬衡) → consistency gate → needs_replan.

So a correct book was blocked because the name matcher could not read the
sentence its own pipeline writes. These tests pin extraction against the shapes
Chinese premises actually take.
"""

from __future__ import annotations

import pytest

from bestseller.services.book_design import (
    _protagonist_name_from_text,
    extract_creation_protagonist_name,
)


pytestmark = pytest.mark.unit


class TestNameInRealPremiseShapes:
    def test_name_at_the_very_start_still_works(self) -> None:
        """The one shape the old regex handled — must not regress."""

        assert _protagonist_name_from_text("李玄，二十岁的废太子，重生为婴儿。") == "李玄"

    def test_name_after_a_descriptive_clause(self) -> None:
        """THE field failure, verbatim in shape."""

        premise = "前世眼看就要亲政登基的废太子姬衡，醒来发现自己缩进一具啼哭不止的婴儿躯壳。"

        assert _protagonist_name_from_text(premise) == "姬衡"

    def test_name_after_a_role_noun(self) -> None:
        assert _protagonist_name_from_text("少年剑修陆沉，被逐出宗门。") == "陆沉"

    def test_name_after_a_title(self) -> None:
        assert _protagonist_name_from_text("废太子姬衡在襁褓中睁开眼。") == "姬衡"

    def test_three_character_name(self) -> None:
        assert _protagonist_name_from_text("落魄画师欧阳追，替死人画最后一张脸。") == "欧阳追"


class TestDoesNotInventNames:
    """Extraction must fail closed: a wrong name is worse than no name, because
    it becomes the snapshot the whole book is then judged against."""

    @pytest.mark.parametrize(
        "premise",
        [
            "一个少年在雨夜里捡到一把断剑。",
            "少女被逐出家门，只带走一盏灯。",
            "主角必须在天亮前做出选择。",
        ],
    )
    def test_generic_role_words_are_not_names(self, premise: str) -> None:
        assert _protagonist_name_from_text(premise) == ""

    def test_empty_and_garbage_input(self) -> None:
        assert _protagonist_name_from_text("") == ""
        assert _protagonist_name_from_text(None) == ""

    def test_does_not_grab_a_common_verb_phrase(self) -> None:
        """No name present → must return nothing rather than a random bigram."""

        assert _protagonist_name_from_text("这一夜血洗皇宫，龙椅上钉着一具尸体。") == ""


class TestOnlyUserChoicesGetVetoPower:
    """The durable fix, and the module's own stated contract.

    ``book_design`` opens with: "The creation boundary is the only place
    allowed to choose the protagonist, tone, and whole-book budget." Its job is
    to FREEZE USER CHOICES so downstream cannot silently overwrite them.

    In this book the user never chose a protagonist name — the tournament
    champion carried none, and each downstream step invented its own (李玄 in
    9 early artifacts, 姬衡 in the premise and all 23 planning artifacts). A
    "mismatch" between two names the system invented for itself is not a design
    violation; it is the pipeline naming a character twice. Killing a finished
    conception over it protects nothing.

    Better extraction (above) reduces how often the two names differ, but it is
    a heuristic over free text and will always have gaps. The invariant that
    actually holds: a name nobody chose may not veto a book.
    """

    @staticmethod
    def _issue_codes(report) -> list[str]:
        return [i.code for i in report.issues]

    def test_heuristic_name_disagreement_is_not_a_blocking_issue(self) -> None:
        from types import SimpleNamespace

        from bestseller.services.book_design import validate_project_book_design

        project = SimpleNamespace(
            slug="name-drift-book",
            title="仇人膝上养帝王",
            genre="玄幻",
            sub_genre="玄幻",
            language="zh-CN",
            audience="男频",
            target_chapters=50,
            target_word_count=130_000,
            metadata_json={
                # No explicit user choice anywhere — the name was invented.
                "premise": "前世眼看就要亲政登基的废太子姬衡，醒来发现自己缩进婴儿躯壳。",
                "story_spine": {"who": "李玄：二十岁的废太子。"},
                "identity_manifest": [{"role": "protagonist", "name": "姬衡"}],
            },
        )

        report = validate_project_book_design(project)

        # Still DETECTED — the disagreement is real and worth repairing.
        # Just not a reason to pause a finished conception.
        assert not report.blocks_production, (
            "two auto-invented names disagreeing must not block a book — "
            f"nobody chose either of them (issues={self._issue_codes(report)})"
        )
        assert "protagonist_identity_mismatch" in report.to_dict()["advisory_codes"]

    def test_an_explicit_user_choice_is_still_enforced(self) -> None:
        """No loosening where it matters: if the user DID name the protagonist,
        downstream may not silently rename them."""

        from types import SimpleNamespace

        from bestseller.services.book_design import validate_project_book_design

        project = SimpleNamespace(
            slug="user-named-book",
            title="书",
            genre="玄幻",
            sub_genre="玄幻",
            language="zh-CN",
            audience="男频",
            target_chapters=50,
            target_word_count=130_000,
            metadata_json={
                "protagonist_name": "姬衡",  # explicit user choice
                "premise": "前世的废太子姬衡，醒来缩进婴儿躯壳。",
                "identity_manifest": [{"role": "protagonist", "name": "萧崇"}],
            },
        )

        report = validate_project_book_design(project)

        assert "protagonist_identity_mismatch" in self._issue_codes(report), (
            "an explicitly chosen protagonist must not be silently replaced"
        )


class TestExtractionPriority:
    def test_explicit_metadata_key_wins(self) -> None:
        meta = {
            "protagonist_name": "姬衡",
            "premise": "落魄画师欧阳追，替死人画最后一张脸。",
        }

        assert extract_creation_protagonist_name(meta) == "姬衡"

    def test_premise_beats_story_spine_when_they_disagree(self) -> None:
        """The exact field disagreement. The premise is what planning reads, so
        the snapshot must agree with it — otherwise the consistency gate fires
        on a book that is internally fine apart from one stale artifact.
        """

        meta = {
            "premise": "前世眼看就要亲政登基的废太子姬衡，醒来发现自己缩进婴儿躯壳。",
            "story_spine": {"who": "李玄：二十岁的废太子，重生为婴儿。"},
        }

        assert extract_creation_protagonist_name(meta) == "姬衡"

    def test_falls_back_to_spine_when_premise_has_no_name(self) -> None:
        meta = {
            "premise": "一个少年在雨夜里捡到一把断剑。",
            "story_spine": {"who": "李玄：二十岁的废太子。"},
        }

        assert extract_creation_protagonist_name(meta) == "李玄"
