from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.canon_guardrails import CanonGuardrails, CanonStateRule
from bestseller.services.character_role_gate import CharacterProfile
from bestseller.services.dialogue_voice_profile import parse_dialogue_voice_profiles
from bestseller.services.retention_safety_gate import (
    AUTO_REPAIR_RETENTION_CODES,
    CAST_VIOLATION_BLOCK_CODE,
    DIALOGUE_AI_FLAVOR_BLOCK_CODE,
    EXPOSITION_DUMP_BLOCK_CODE,
    HOOK_ECHO_BLOCK_CODE,
    HOOK_ECHO_LOW_BLOCK_CODE,
    SIGNATURE_SCENE_BLOCK_CODE,
    evaluate_retention_safety,
    stamp_retention_block_codes,
)

pytestmark = pytest.mark.unit


_PREV_CHAPTER = (
    "夜色如墨。\n"
    "他握紧剑柄，心中暗想：今夜若不退，便是死路一条。\n"
    "下一刻，门外脚步声响起。\n"
    "突然，墙后传来一声低咳——竟是他以为已死之人。\n"
    "未完——\n"
)

_ECHOING_CHAPTER = (
    "门外的脚步声越来越近。"
    "下一刻，门被推开，竟是他失踪三年的师兄。"
    "突然，名单从怀里掉了出来。"
    "他后退一步。"
)

_FRESH_BRANCH_CHAPTER = (
    "三日后，清晨。"
    "李四走进客栈。"
    "店小二殷勤地擦着桌子。"
)


def test_evaluate_retention_safety_passes_when_all_ok() -> None:
    report = evaluate_retention_safety(
        chapter_position=2,
        chapter_text=_ECHOING_CHAPTER,
        prev_chapter_text=_PREV_CHAPTER,
        prev_chapter_position=1,
        skip_signature=True,  # skip — _ECHOING_CHAPTER won't have signature hints
        skip_chapter_length=True,  # _ECHOING_CHAPTER is intentionally a stub
    )

    assert report.passed
    assert report.auto_repair_codes == ()


def test_evaluate_retention_safety_critical_hook_echo() -> None:
    report = evaluate_retention_safety(
        chapter_position=2,
        chapter_text=_FRESH_BRANCH_CHAPTER,
        prev_chapter_text=_PREV_CHAPTER,
        prev_chapter_position=1,
        skip_signature=True,
    )

    assert not report.passed
    assert HOOK_ECHO_BLOCK_CODE in report.auto_repair_codes
    assert report.has_critical
    assert report.findings[0].evidence
    assert report.findings[0].evidence["missed_tokens"]


def test_evaluate_retention_safety_low_hook_echo_is_advisory() -> None:
    report = evaluate_retention_safety(
        chapter_position=2,
        chapter_text="名单还在桌上，他先按住第一页。",
        prev_chapter_text="那份名单还在抽屉里，回执被他扣在掌心。",
        prev_chapter_position=1,
        skip_signature=True,
        skip_chapter_length=True,
    )

    assert report.passed
    assert HOOK_ECHO_LOW_BLOCK_CODE not in report.auto_repair_codes
    assert any(f.code == HOOK_ECHO_LOW_BLOCK_CODE for f in report.findings)


def test_evaluate_retention_safety_skips_skeleton_signature_mandate() -> None:
    """R25: the gate replans without book anchors → all mandates are empty
    skeletons that were never rendered into the writer prompt. Grading the
    chapter against that empty standard made SIGNATURE_SCENE_MISSING
    structurally unavoidable — skeleton mandates must be skipped."""

    no_signature_text = "他走进房间。喝了一杯水。然后睡觉。" * 30

    report = evaluate_retention_safety(
        chapter_position=1,
        chapter_text=no_signature_text,
        prev_chapter_text=None,
        skip_hook_echo=True,
        skip_exposition=True,
    )

    assert SIGNATURE_SCENE_BLOCK_CODE not in report.auto_repair_codes
    assert all(
        f.code != SIGNATURE_SCENE_BLOCK_CODE for f in report.findings
    )


def test_evaluate_retention_safety_exposition_critical() -> None:
    dump = (
        "据说茅山术法分为内丹、外咒、罗盘三大门类，自唐代以来便有传承。"
        "传说三族指的是南茅山、北出马仙、东钱家三派，三百年前曾立下盟约。"
        "原来青囊秘卷中记载，凡入镜者，必须以血为引。"
        "事实上，林家先祖三百年前封印了第一面困魂镜。"
        "传说罗盘的用法是根据二十四山方位推演吉凶。"
    )

    report = evaluate_retention_safety(
        chapter_position=2,
        chapter_text=dump,
        prev_chapter_text=None,
        skip_hook_echo=True,
        skip_signature=True,
    )

    assert EXPOSITION_DUMP_BLOCK_CODE in report.auto_repair_codes


def test_evaluate_retention_safety_chapter_one_no_hook_echo() -> None:
    """ch1 has no prev chapter, hook echo can't run — should not be a problem."""

    report = evaluate_retention_safety(
        chapter_position=1,
        chapter_text=_ECHOING_CHAPTER,
        prev_chapter_text=None,
        skip_signature=True,
    )

    # No hook echo block fired
    assert HOOK_ECHO_BLOCK_CODE not in report.auto_repair_codes


def test_stamp_retention_block_codes_critical_blocks_chapter() -> None:
    chapter = SimpleNamespace(metadata_json={}, production_state="ok")

    report = evaluate_retention_safety(
        chapter_position=2,
        chapter_text=_FRESH_BRANCH_CHAPTER,
        prev_chapter_text=_PREV_CHAPTER,
        prev_chapter_position=1,
        skip_signature=True,
    )

    blocked = stamp_retention_block_codes(chapter, report)

    assert blocked is True
    assert chapter.production_state == "blocked"
    assert HOOK_ECHO_BLOCK_CODE in chapter.metadata_json["auto_repair_last_block_codes"]
    assert chapter.metadata_json["production_block_code"] == HOOK_ECHO_BLOCK_CODE


def test_stamp_retention_block_codes_passing_does_not_block() -> None:
    chapter = SimpleNamespace(
        metadata_json={
            "auto_repair_last_block_codes": [HOOK_ECHO_BLOCK_CODE, "LEGACY_CODE"],
            "production_block_code": HOOK_ECHO_BLOCK_CODE,
        },
        production_state="ok",
    )

    report = evaluate_retention_safety(
        chapter_position=2,
        chapter_text=_ECHOING_CHAPTER,
        prev_chapter_text=_PREV_CHAPTER,
        prev_chapter_position=1,
        skip_signature=True,
        skip_chapter_length=True,
    )

    blocked = stamp_retention_block_codes(chapter, report)

    assert blocked is False
    assert chapter.production_state == "ok"
    assert chapter.metadata_json["auto_repair_last_block_codes"] == ["LEGACY_CODE"]
    assert "production_block_code" not in chapter.metadata_json


def test_stamp_retention_block_codes_preserves_existing_codes() -> None:
    chapter = SimpleNamespace(
        metadata_json={
            "auto_repair_last_block_codes": ["LEGACY_CODE", CAST_VIOLATION_BLOCK_CODE]
        },
        production_state="ok",
    )

    report = evaluate_retention_safety(
        chapter_position=2,
        chapter_text=_FRESH_BRANCH_CHAPTER,
        prev_chapter_text=_PREV_CHAPTER,
        prev_chapter_position=1,
        skip_signature=True,
    )

    stamp_retention_block_codes(chapter, report)

    codes = chapter.metadata_json["auto_repair_last_block_codes"]
    assert "LEGACY_CODE" in codes
    assert HOOK_ECHO_BLOCK_CODE in codes
    assert CAST_VIOLATION_BLOCK_CODE not in codes


def test_auto_repair_retention_codes_constants_exposed() -> None:
    """All 3 block codes must be in the auto-repair-eligible tuple."""

    assert HOOK_ECHO_BLOCK_CODE in AUTO_REPAIR_RETENTION_CODES
    assert HOOK_ECHO_LOW_BLOCK_CODE in AUTO_REPAIR_RETENTION_CODES
    assert SIGNATURE_SCENE_BLOCK_CODE in AUTO_REPAIR_RETENTION_CODES
    assert EXPOSITION_DUMP_BLOCK_CODE in AUTO_REPAIR_RETENTION_CODES
    assert CAST_VIOLATION_BLOCK_CODE in AUTO_REPAIR_RETENTION_CODES


def test_evaluate_retention_safety_cast_violation() -> None:
    guardrails = CanonGuardrails(
        state_rules=(
            CanonStateRule(
                subject="裴镜渊",
                applies_after_chapter=16,
                forbidden_patterns=("裴镜渊.{0,20}开口",),
            ),
        )
    )

    report = evaluate_retention_safety(
        chapter_position=2,
        chapter_text="裴镜渊站在角落。裴镜渊忽然开口。",
        guardrails=guardrails,
        skip_hook_echo=True,
        skip_signature=True,
        skip_exposition=True,
    )

    assert CAST_VIOLATION_BLOCK_CODE in report.auto_repair_codes


def test_evaluate_retention_safety_dialogue_voice_violation() -> None:
    voice = parse_dialogue_voice_profiles(
        """
# Cast

## 林渊
原型：P2
"""
    )[0]
    report = evaluate_retention_safety(
        chapter_position=2,
        chapter_text="林渊按住罗盘，说：“看来这事不简单。”",
        character_profiles=(
            CharacterProfile(
                name="林渊",
                abilities=("罗盘",),
                dialogue_voice=voice,
            ),
        ),
        skip_hook_echo=True,
        skip_signature=True,
        skip_exposition=True,
        skip_chapter_length=True,
        skip_character_role=True,
    )

    assert DIALOGUE_AI_FLAVOR_BLOCK_CODE in report.auto_repair_codes


def test_evaluate_skip_flags_all_set() -> None:
    """All skip flags set → no findings even with bad input."""

    report = evaluate_retention_safety(
        chapter_position=2,
        chapter_text=_FRESH_BRANCH_CHAPTER,
        prev_chapter_text=_PREV_CHAPTER,
        skip_signature=True,
        skip_hook_echo=True,
        skip_exposition=True,
        skip_chapter_length=True,
    )

    assert report.passed
    assert report.findings == ()


def test_timeline_violation_triggers_auto_repair() -> None:
    """Regression for the ch1 silent-skip bug (2026-05-23).

    When ``timeline_canon`` is passed, the TimelineConsistencyGate must
    fire on the canonical ch1-style violation: "十七年前 + 七岁" with
    "戊子年" present. Previously the gate was silently skipped because
    pipelines.py did not forward this argument.
    """

    from bestseller.services.retention_safety_gate import TIMELINE_INCONSISTENT_BLOCK_CODE
    from bestseller.services.timeline_consistency_gate import (
        TimelineCanon,
        TimelineFact,
    )

    canon = TimelineCanon(
        present_year=2025,
        protagonist_name="林渊",
        protagonist_current_age=30,
        events=(
            TimelineFact(
                event_id="father_first_entry",
                label="父亲第一次入十七栋",
                years_ago=23,
                year_name="戊子年",
                protagonist_age_at_event=7,
                subjects=("林正淳", "林渊"),
                aliases=("父亲",),
            ),
        ),
        forbidden_anchors=(17, 10, 5, 50),
    )
    chapter_text = (
        "林渊低头，看见自己的倒影浮现——不是现在的模样，"
        "而是十七年前的自己。那时候他才七岁，"
        "站在十七栋门口，抬头看着四楼的窗口。"
    )
    report = evaluate_retention_safety(
        chapter_position=1,
        chapter_text=chapter_text,
        skip_signature=True,
        skip_hook_echo=True,
        skip_exposition=True,
        timeline_canon=canon,
    )

    assert not report.passed, "ch1-style timeline violation must NOT pass"
    assert TIMELINE_INCONSISTENT_BLOCK_CODE in report.auto_repair_codes
