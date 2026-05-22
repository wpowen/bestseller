from __future__ import annotations

import pytest

from bestseller.services.canon_guardrails import (
    CanonForbiddenTerm,
    CanonGuardrails,
    CanonStateRule,
)
from bestseller.services.cast_compliance_gate import check_cast_compliance

pytestmark = pytest.mark.unit


def _guardrails() -> CanonGuardrails:
    return CanonGuardrails(
        forbidden_terms=(
            CanonForbiddenTerm(term="守夜人", reason="旧版世界观"),
        ),
        state_rules=(
            CanonStateRule(
                subject="裴镜渊",
                status="第一卷只作旧账名",
                applies_after_chapter=16,
                forbidden_patterns=(
                    "裴镜渊.{0,20}(走进|出现|开口|抬头|冷笑|亲自)",
                ),
                reason="裴镜渊不能在第一卷抢走十七栋主线",
                allowed_next="只通过钱家账页、旧名、过户记录、回执链条作为线索存在",
            ),
        ),
    )


def test_blocks_repeated_subject_before_threshold() -> None:
    text = "裴镜渊站在门口。裴镜渊抬头。裴镜渊开口。裴镜渊冷笑。裴镜渊走进屋内。"

    report = check_cast_compliance(text, 2, _guardrails())

    assert not report.passed
    assert any(v.pattern_matched == "name_appears_before_threshold" for v in report.violations)


def test_repeated_subject_does_not_block_plain_lifecycle_state_rule() -> None:
    guardrails = CanonGuardrails(
        state_rules=(
            CanonStateRule(
                subject="小雨",
                status="第 4 章已认账获救",
                applies_after_chapter=4,
                forbidden_patterns=(
                    "小雨.{0,40}(被困在镜子|被拖进镜面|被拖入303)",
                ),
                reason="小雨的玉镯镜眼救援已完成",
                allowed_next="只能作为证人、陈默情感锚点、现实侧压力继续推进",
            ),
        )
    )

    report = check_cast_compliance("小雨攥着玉镯。小雨抬头。小雨说她不认账。", 2, guardrails)

    assert report.passed


def test_allows_subject_after_threshold() -> None:
    text = "裴镜渊站在门口。裴镜渊抬头。裴镜渊开口。裴镜渊冷笑。裴镜渊走进屋内。"

    report = check_cast_compliance(text, 20, _guardrails())

    assert report.passed


def test_blocks_absolute_forbidden_term_any_chapter() -> None:
    report = check_cast_compliance("守夜人留下了旧徽记。", 20, _guardrails())

    assert not report.passed
    assert report.violations[0].subject == "守夜人"


def test_allows_single_old_name_mention_before_threshold() -> None:
    report = check_cast_compliance("账页旧名写着裴镜渊, 但无人认识。", 2, _guardrails())

    assert report.passed


def test_blocks_forbidden_pattern_even_single_subject_mention() -> None:
    report = check_cast_compliance("裴镜渊忽然开口, 说否认者先入账。", 2, _guardrails())

    assert not report.passed
    assert any("开口" in v.pattern_matched for v in report.violations)


def test_ignores_state_rule_without_threshold_for_presence_count() -> None:
    guardrails = CanonGuardrails(
        state_rules=(
            CanonStateRule(
                subject="陈守正",
                forbidden_patterns=("陈守正",),
            ),
        )
    )

    report = check_cast_compliance("陈守正。陈守正。", 2, guardrails)

    assert report.passed


def test_invalid_regex_falls_back_to_literal_match() -> None:
    guardrails = CanonGuardrails(
        state_rules=(
            CanonStateRule(
                subject="裴镜渊",
                applies_after_chapter=16,
                forbidden_patterns=("裴镜渊[",),
            ),
        )
    )

    report = check_cast_compliance("裴镜渊[", 2, guardrails)

    assert not report.passed


def test_empty_text_passes() -> None:
    assert check_cast_compliance("", 2, _guardrails()).passed


def test_empty_guardrails_passes() -> None:
    assert check_cast_compliance("守夜人 裴镜渊 裴镜渊", 2, CanonGuardrails()).passed


def test_reports_chapter_position() -> None:
    report = check_cast_compliance("守夜人", 7, _guardrails())

    assert report.chapter_position == 7
    assert report.violations[0].chapter_position == 7
