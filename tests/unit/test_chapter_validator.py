"""Unit tests for L5 chapter-assembly validators.

These cover the two checks that catch cross-scene defects invisible at L4:
    * DialogIntegrityCheck — paired-quote state machine (bug #2)
    * POVLockCheck — narrative-prose POV consistency (bug #12)

We fabricate minimal invariants objects to exercise the checks without
involving the seed logic (which is tested separately in test_invariants).
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services.chapter_validator import (
    CliffhangerRotationCheck,
    DialogIntegrityCheck,
    POVLockCheck,
    build_chapter_validator_checks,
    classify_cliffhanger,
    validate_chapter,
)
from bestseller.services.invariants import CliffhangerType, seed_invariants
from bestseller.services.output_validator import ValidationContext

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _ctx(
    language: str = "zh-CN",
    pov: str = "close_third",
    chapter_no: int = 1,
) -> ValidationContext:
    inv = seed_invariants(
        project_id=uuid4(),
        language=language,
        words_per_chapter=SimpleNamespace(min=5000, target=6400, max=7500),
        pov=pov,
    )
    return ValidationContext(invariants=inv, chapter_no=chapter_no, scope="chapter")


# ---------------------------------------------------------------------------
# DialogIntegrityCheck.
# ---------------------------------------------------------------------------


class TestDialogIntegrityCheck:
    def test_clean_zh_dialogue_passes(self) -> None:
        text = (
            "林奚抬眼望去。\n\n"
            "\u201c你是谁？\u201d他低声问道。\n\n"
            "\u201c我来自北境。\u201d来人答。\n"
        )
        violations = list(DialogIntegrityCheck().run(text, _ctx(language="zh-CN")))
        assert violations == []

    def test_unclosed_curly_double_in_zh_fails(self) -> None:
        # Opens a curly double quote but never closes it in the paragraph.
        text = (
            "林奚开口：\u201c这不可能\n\n"
            "他转身就走。\n"
        )
        violations = list(DialogIntegrityCheck().run(text, _ctx(language="zh-CN")))
        assert len(violations) == 1
        assert violations[0].code == "DIALOG_UNPAIRED"
        assert "curly_double" in violations[0].detail

    def test_unclosed_corner_quote_fails(self) -> None:
        text = (
            "她问：\u300c你去哪\n\n"
            "他没有回答。\n"
        )
        violations = list(DialogIntegrityCheck().run(text, _ctx(language="zh-CN")))
        assert len(violations) == 1
        assert "corner" in violations[0].detail

    def test_odd_straight_double_quotes_in_en_fails(self) -> None:
        text = 'He said "hello and walked away without closing the quote.'
        violations = list(DialogIntegrityCheck().run(text, _ctx(language="en", pov="close_third")))
        codes = {v.code for v in violations}
        assert "DIALOG_UNPAIRED" in codes

    def test_even_straight_quotes_pass(self) -> None:
        text = 'He said "hello" and then "goodbye" after a pause.'
        violations = list(DialogIntegrityCheck().run(text, _ctx(language="en", pov="close_third")))
        assert violations == []

    def test_empty_text_yields_no_violation(self) -> None:
        violations = list(DialogIntegrityCheck().run("", _ctx()))
        assert violations == []

    def test_nested_non_ambiguous_quotes_pass(self) -> None:
        # Curly double around a curly single (legitimate nesting).
        text = "\u201c她说\u2018走\u2019\u201d他犹豫。"
        violations = list(DialogIntegrityCheck().run(text, _ctx(language="zh-CN")))
        assert violations == []

    def test_feedback_contains_location_snippet(self) -> None:
        text = "林奚喊道：\u201c救命\n\n然后一切归于寂静。"
        violations = list(DialogIntegrityCheck().run(text, _ctx(language="zh-CN")))
        assert len(violations) == 1
        v = violations[0]
        assert "救命" in v.prompt_feedback

    def test_multi_paragraph_quoted_note_is_not_flagged(self) -> None:
        """Long handwritten notes or speeches legitimately span paragraphs.

        Only the opening paragraph has 「 and only the closing paragraph has
        」; the middle paragraphs are bare content. The chapter is
        *globally* balanced, so this must pass — a per-paragraph check
        would wrongly flag paragraph 1 as unclosed.
        """
        text = (
            "她打开信封，取出一张便条。\n\n"
            "\u300c姜澄三小时前往东区去了。\n\n"
            "霍沉在那里。\n\n"
            "别去找他。\u300d\n\n"
            "她把便条收好。"
        )
        violations = list(DialogIntegrityCheck().run(text, _ctx(language="zh-CN")))
        assert violations == []

    def test_globally_unclosed_corner_quote_is_flagged(self) -> None:
        """The ch-050 defect: opens 「 and never closes anywhere."""
        text = (
            "她低声说：\u300c我再也见不到他了。\n\n"
            "窗外的雨越下越大。\n\n"
            "她独自站在门前。"
        )
        violations = list(DialogIntegrityCheck().run(text, _ctx(language="zh-CN")))
        assert len(violations) == 1
        assert "corner" in violations[0].detail
        assert "再也见不到" in violations[0].prompt_feedback


# ---------------------------------------------------------------------------
# POVLockCheck.
# ---------------------------------------------------------------------------


class TestPOVLockCheck:
    def test_close_third_clean_narrative_passes(self) -> None:
        text = (
            "他走进庭院。她在石阶前等他。"
            "他们相视无言。他转身离去，她没有挽留。"
            "她低头，他的背影消失在夜色中。"
            "她在原地站了很久。他已经走远。"
            "她终于开口：\u201c再见。\u201d"
        )
        violations = list(POVLockCheck().run(text, _ctx(pov="close_third")))
        assert violations == []

    def test_close_third_with_first_person_narrative_fails(self) -> None:
        # Dialog (inside quotes) legitimately uses 我, but narrative should not.
        text = (
            "我走进庭院。我看见她在石阶前。"
            "我心中一阵激动。我没有上前。"
            "我转身离去。"
        )
        violations = list(POVLockCheck().run(text, _ctx(pov="close_third")))
        assert len(violations) == 1
        assert violations[0].code == "POV_DRIFT"
        assert "close_third" in violations[0].detail

    def test_close_third_dialogue_with_first_person_passes(self) -> None:
        # 我 occurs only inside dialogue — narrative prose is clean.
        text = (
            "他走进来。\u201c我不知道你会来。\u201d他说。"
            "她点头。\u201c我也是刚到。\u201d她答。"
            "他们沉默了片刻。他看着她。她低头避开。"
            "他开口：\u201c我一直在想你。\u201d"
            "她抬头：\u201c我不知道该说什么。\u201d"
        )
        violations = list(POVLockCheck().run(text, _ctx(pov="close_third")))
        assert violations == []

    def test_omniscient_pov_is_exempt(self) -> None:
        text = (
            "我走进庭院。我看见她。我转身。我离开。"
        )
        violations = list(POVLockCheck().run(text, _ctx(pov="omniscient")))
        assert violations == []

    def test_first_person_pov_passes_with_first_person_narrative(self) -> None:
        text = (
            "我走进庭院。我心中一阵悸动。"
            "我看见她在石阶前等我。"
            "我没有说话，只是站在那里。"
            "我们对视良久。"
        )
        violations = list(POVLockCheck().run(text, _ctx(pov="first")))
        assert violations == []

    def test_en_close_third_with_first_person_fails(self) -> None:
        text = (
            "I walked into the courtyard. "
            "I saw her on the steps. "
            "I felt my heart tighten. "
            "I did not approach. "
            "I turned away."
        )
        violations = list(POVLockCheck().run(text, _ctx(language="en", pov="close_third")))
        assert len(violations) == 1
        assert violations[0].code == "POV_DRIFT"

    def test_en_close_third_clean_passes(self) -> None:
        text = (
            "He walked into the courtyard. "
            "She was waiting on the steps. "
            "They did not speak. "
            "He turned and left. "
            "She watched him go."
        )
        violations = list(POVLockCheck().run(text, _ctx(language="en", pov="close_third")))
        assert violations == []

    def test_below_threshold_mismatches_do_not_fire(self) -> None:
        # Only two first-person sentences — below the default min_drift_sentences=3.
        text = (
            "他走进庭院。她在等他。"
            "我突然想起一件事。"  # one mismatch
            "他没有回答。她转身离开。"
            "我还是没有说出口。"  # two mismatches — below threshold of 3
            "他目送她消失在夜色中。"
        )
        violations = list(POVLockCheck().run(text, _ctx(pov="close_third")))
        assert violations == []

    def test_empty_text_yields_no_violation(self) -> None:
        assert list(POVLockCheck().run("", _ctx())) == []

    def test_first_person_tolerates_third_person_descriptions(self) -> None:
        """First-person narrators describe other characters constantly.

        A handful of sentences with pure third-person pronouns is normal
        ("She reached for the door"), not drift. Drift only means the
        narrator has actually slipped into close-third reportage for most
        of the passage.
        """
        text = (
            "我推开门。"        # 1st
            "她转过身来。"       # 3rd
            "我心跳加速。"       # 1st
            "他站在窗前。"       # 3rd
            "我走过去。"        # 1st
            "她没说话。"        # 3rd
            "我看着他们。"       # 1st
            "我终于开口。"       # 1st
        )
        # 3 out of 8 sentences are pure third-person (ratio 0.375 < 0.5),
        # so this should not fire — the narrator is still firmly first-person.
        violations = list(POVLockCheck().run(text, _ctx(pov="first")))
        assert violations == []

    def test_first_person_fires_only_when_majority_drifts(self) -> None:
        """When >= 50% of sampled sentences are pure third-person, the
        narrator has drifted into close-third reportage."""
        text = (
            "他走进庭院。她在石阶前等他。"
            "他看着她。她低下头。"
            "他转身离开。她没有挽留。"
            "他走远了。她目送他消失。"
            "他的背影消失在夜色中。她才转身回屋。"
        )  # 10 sentences, all pure-third — first-person has completely lapsed.
        violations = list(POVLockCheck().run(text, _ctx(pov="first")))
        assert len(violations) == 1
        assert violations[0].code == "POV_DRIFT"


# ---------------------------------------------------------------------------
# Orchestrator + factory.
# ---------------------------------------------------------------------------


class TestValidateChapter:
    def test_factory_returns_expected_checks(self) -> None:
        checks = build_chapter_validator_checks()
        # Phase 2 adds 4 hype checks on top of the base 3
        # (dialog / POV / cliffhanger). Phase B1 adds LineGapCheck. All
        # context-dependent checks no-op gracefully when their optional
        # context is absent.
        assert len(checks) == 8
        codes = {c.code for c in checks}
        assert codes == {
            "DIALOG_UNPAIRED",
            "POV_DRIFT",
            "CLIFFHANGER_REPEAT",
            "HYPE_MISSING",
            "HYPE_REPEAT",
            "ENDING_SENTENCE_WEAK",
            "GOLDEN_THREE_WEAK",
            "LINE_GAP",
        }

    def test_validate_chapter_accumulates_violations(self) -> None:
        # Fails both checks: first paragraph drops into 1st-person narrative
        # (POV drift), a *later* paragraph opens an unclosed quote.
        text = (
            "我走进庭院。我看见她。我心中一阵悸动。我没有上前。我转身。"
            "\n\n"
            "她开口：\u201c请留下\n"
        )
        report = validate_chapter(text, _ctx(pov="close_third"))
        codes = {v.code for v in report.violations}
        assert "DIALOG_UNPAIRED" in codes
        assert "POV_DRIFT" in codes
        assert report.blocks_write is True

    def test_clean_text_yields_empty_report(self) -> None:
        text = (
            "他走进庭院。她在石阶前等他。"
            "\u201c我来找你。\u201d他说。"
            "她点头。\u201c我知道。\u201d她轻声答。"
            "他们沉默了片刻。他转身离去。"
        )
        report = validate_chapter(text, _ctx(pov="close_third"))
        assert report.violations == ()
        assert report.blocks_write is False


# ---------------------------------------------------------------------------
# CliffhangerRotationCheck — bug #10.
# ---------------------------------------------------------------------------


class TestClassifyCliffhanger:
    def test_zh_revelation_detected(self) -> None:
        text = (
            "他盯着那块石碑许久。\n\n"
            "原来真相一直藏在这里。他终于揭开了秘密。"
        )
        assert classify_cliffhanger(text, "zh-CN") == CliffhangerType.REVELATION

    def test_zh_body_reaction_detected(self) -> None:
        text = (
            "她退后一步。\n\n"
            "心跳加速，冷汗顺着脊背滑落，胸口一阵剧痛。"
        )
        assert (
            classify_cliffhanger(text, "zh-CN")
            == CliffhangerType.BODY_REACTION
        )

    def test_en_decision_detected(self) -> None:
        text = (
            "The letter lay on the table. She read it twice.\n\n"
            "She had to decide. No choice remained. She had to act now."
        )
        assert classify_cliffhanger(text, "en") == CliffhangerType.DECISION

    def test_ambiguous_returns_none(self) -> None:
        # No cliffhanger keywords → None.
        text = "He closed the door. Outside the wind was quiet."
        assert classify_cliffhanger(text, "en") is None

    def test_empty_text_returns_none(self) -> None:
        assert classify_cliffhanger("", "zh-CN") is None
        assert classify_cliffhanger("\n  \n  ", "en") is None

    def test_single_weak_hit_returns_none(self) -> None:
        # One isolated "decided" below min-score threshold.
        text = "He decided to wait."
        assert classify_cliffhanger(text, "en") is None


class TestCliffhangerRotationCheck:
    def test_empty_recent_list_skips(self) -> None:
        check = CliffhangerRotationCheck()
        text = "原来真相一直藏在这里。他终于揭开了秘密。心中一片震惊。"
        ctx = _ctx()  # recent_cliffhangers defaults to ()
        assert check.run(text, ctx) == []

    def test_scene_scope_skipped(self) -> None:
        check = CliffhangerRotationCheck()
        inv_ctx = _ctx()
        ctx = ValidationContext(
            invariants=inv_ctx.invariants,
            chapter_no=1,
            scope="scene",  # not a chapter
            recent_cliffhangers=(CliffhangerType.REVELATION,),
        )
        text = "原来真相一直藏在这里。他终于揭开了秘密。心中一片震惊。"
        assert check.run(text, ctx) == []

    def test_unclassifiable_text_does_not_block(self) -> None:
        check = CliffhangerRotationCheck()
        inv_ctx = _ctx()
        ctx = ValidationContext(
            invariants=inv_ctx.invariants,
            chapter_no=5,
            scope="chapter",
            recent_cliffhangers=(CliffhangerType.REVELATION,),
        )
        text = "夜深了。他关上门。窗外的风安静下来。"
        # No cliffhanger keywords → detected=None → no violation.
        assert check.run(text, ctx) == []

    def test_matching_cliffhanger_blocks(self) -> None:
        check = CliffhangerRotationCheck()
        inv_ctx = _ctx()
        ctx = ValidationContext(
            invariants=inv_ctx.invariants,
            chapter_no=5,
            scope="chapter",
            recent_cliffhangers=(CliffhangerType.REVELATION,),
        )
        text = "原来真相一直藏在这里。他终于揭开了秘密。竟然身份如此不同。"
        result = list(check.run(text, ctx))
        assert len(result) == 1
        v = result[0]
        assert v.code == "CLIFFHANGER_REPEAT"
        assert v.severity == "block"
        assert "revelation" in v.detail.lower()
        assert "revelation" in v.prompt_feedback.lower()

    def test_non_matching_type_passes(self) -> None:
        check = CliffhangerRotationCheck()
        inv_ctx = _ctx()
        ctx = ValidationContext(
            invariants=inv_ctx.invariants,
            chapter_no=5,
            scope="chapter",
            recent_cliffhangers=(CliffhangerType.DECISION,),
        )
        # Revelation ending, decision was the recent one.
        text = "原来真相一直藏在这里。他终于揭开了秘密。竟然身份如此不同。"
        assert check.run(text, ctx) == []

    def test_feedback_lists_available_types(self) -> None:
        check = CliffhangerRotationCheck()
        inv_ctx = _ctx()
        recent = (
            CliffhangerType.REVELATION,
            CliffhangerType.BODY_REACTION,
        )
        ctx = ValidationContext(
            invariants=inv_ctx.invariants,
            chapter_no=5,
            scope="chapter",
            recent_cliffhangers=recent,
        )
        text = "原来真相一直藏在这里。他终于揭开了秘密。竟然身份如此不同。"
        [v] = list(check.run(text, ctx))
        # Feedback mentions at least one type that is NOT in the recent list.
        assert "decision" in v.prompt_feedback
        assert "revelation" not in v.prompt_feedback.lower().split("尚未使用的")[-1]
