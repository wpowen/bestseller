from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.retention_safety_gate import (
    AUTO_REPAIR_RETENTION_CODES,
    CAST_VIOLATION_BLOCK_CODE,
    EXPOSITION_DUMP_BLOCK_CODE,
    HOOK_ECHO_BLOCK_CODE,
    SIGNATURE_SCENE_BLOCK_CODE,
    evaluate_retention_safety,
    stamp_retention_block_codes,
)
from bestseller.services.canon_guardrails import CanonGuardrails, CanonStateRule

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


def test_evaluate_retention_safety_signature_missing() -> None:
    """ch1 is a signature slot (revelation) — text without any hints triggers."""

    no_signature_text = "他走进房间。喝了一杯水。然后睡觉。" * 30

    report = evaluate_retention_safety(
        chapter_position=1,
        chapter_text=no_signature_text,
        prev_chapter_text=None,
        skip_hook_echo=True,
        skip_exposition=True,
    )

    assert SIGNATURE_SCENE_BLOCK_CODE in report.auto_repair_codes


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


def test_evaluate_skip_flags_all_set() -> None:
    """All skip flags set → no findings even with bad input."""

    report = evaluate_retention_safety(
        chapter_position=2,
        chapter_text=_FRESH_BRANCH_CHAPTER,
        prev_chapter_text=_PREV_CHAPTER,
        skip_signature=True,
        skip_hook_echo=True,
        skip_exposition=True,
    )

    assert report.passed
    assert report.findings == ()
