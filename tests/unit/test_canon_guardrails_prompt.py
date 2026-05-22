from __future__ import annotations

import pytest

from bestseller.services.canon_guardrails import (
    CanonForbiddenTerm,
    CanonGuardrails,
    CanonStateRule,
    render_canon_guardrails_block,
)

pytestmark = pytest.mark.unit


def _guardrails() -> CanonGuardrails:
    return CanonGuardrails(
        forbidden_terms=(
            CanonForbiddenTerm(
                term="守夜人",
                reason="旧版世界观",
                suggestion="三族契约",
            ),
            CanonForbiddenTerm(term="北马", reason="未引入"),
        ),
        state_rules=(
            CanonStateRule(
                subject="裴镜渊",
                status="第一卷只作旧账名",
                applies_after_chapter=16,
                forbidden_patterns=(
                    "裴镜渊.{0,20}(走进|出现|开口|抬头|冷笑|亲自)",
                    "裴家势力",
                ),
                reason="不得提前抢主线",
                allowed_next="可作为旧账名被提及",
            ),
            CanonStateRule(
                subject="陈守正",
                status="旧版陈家线人物",
                applies_after_chapter=None,
                forbidden_patterns=("陈守正",),
                reason="正典已替换为陈默",
            ),
        ),
    )


def test_render_empty_guardrails() -> None:
    assert render_canon_guardrails_block(None) == ""
    assert render_canon_guardrails_block(CanonGuardrails()) == ""


def test_render_chinese_includes_term_and_subject() -> None:
    block = render_canon_guardrails_block(_guardrails(), chapter_number=2)

    assert "正典守护" in block
    assert "严禁出现" in block
    assert "守夜人" in block
    assert "裴镜渊" in block
    assert "三族契约" in block  # suggestion appears


def test_render_drops_chapter_aware_rule_after_threshold() -> None:
    """裴镜渊 has applies_after_chapter=16 → drop when current chapter > 16."""

    block = render_canon_guardrails_block(_guardrails(), chapter_number=20)

    assert "裴镜渊" not in block
    # but always-on rule still appears
    assert "陈守正" in block


def test_render_keeps_chapter_aware_rule_before_threshold() -> None:
    """裴镜渊 stays in block when chapter <= threshold."""

    block = render_canon_guardrails_block(_guardrails(), chapter_number=16)

    assert "裴镜渊" in block
    assert "陈守正" in block


def test_render_treats_no_chapter_as_always_active() -> None:
    """When chapter_number is None, every rule is shown."""

    block = render_canon_guardrails_block(_guardrails(), chapter_number=None)

    assert "裴镜渊" in block
    assert "陈守正" in block


def test_render_english_skeleton() -> None:
    block = render_canon_guardrails_block(
        _guardrails(), chapter_number=2, language="en"
    )

    assert "Canon Guardrails" in block
    assert "守夜人" in block  # term content stays Chinese


def test_render_block_warns_about_rewrite_penalty() -> None:
    block = render_canon_guardrails_block(_guardrails(), chapter_number=1)

    # The "violating triggers rewrite" warning should appear
    assert "重写" in block


def test_state_rule_forbidden_patterns_appear() -> None:
    block = render_canon_guardrails_block(_guardrails(), chapter_number=2)

    assert "裴家势力" in block
