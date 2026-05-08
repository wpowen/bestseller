from __future__ import annotations

import pytest

from bestseller.domain.decision_policy import DecisionEvent
from bestseller.services.decision_policy import (
    build_decision_policy_block,
    cautious_survival_policy,
    validate_decision,
)

pytestmark = pytest.mark.unit


def test_cautious_survivalist_rejects_public_vanity_duel() -> None:
    policy = cautious_survival_policy("韩立")

    audit = validate_decision(
        policy,
        DecisionEvent(
            character_name="韩立",
            chapter_no=12,
            situation="同门当众挑衅, 要他立刻上台决斗。",
            action="为保面子接受公开决斗。",
            risk_level="high",
            public_vanity=True,
            behavior_tags=("public_vanity_duel",),
        ),
    )

    assert not audit.passed
    assert {
        "FORBIDDEN_BEHAVIOR",
        "HIGH_RISK_WITHOUT_CAUSE",
        "VANITY_RISK",
    }.issubset({finding.code for finding in audit.findings})


def test_cautious_survivalist_allows_high_risk_for_rare_resource_with_escape() -> None:
    policy = cautious_survival_policy("韩立")

    audit = validate_decision(
        policy,
        DecisionEvent(
            character_name="韩立",
            chapter_no=18,
            situation="禁地深处出现筑基丹, 敌人正在靠近。",
            action="布置退路后潜入取丹。",
            risk_level="high",
            motive_tags=("rare_resource_upside",),
            tactic_tags=("prepare", "conceal"),
            has_credible_escape_route=True,
        ),
    )

    assert audit.passed
    assert audit.findings == ()


def test_moral_boundary_violation_blocks_decision() -> None:
    policy = cautious_survival_policy("韩立")

    audit = validate_decision(
        policy,
        DecisionEvent(
            character_name="韩立",
            chapter_no=22,
            situation="路人挡住逃路。",
            action="为节省时间伤害无关弱者。",
            risk_level="medium",
            violated_boundary_keys=("do_not_harm_unrelated_weak",),
        ),
    )

    assert not audit.passed
    assert {finding.code for finding in audit.findings} == {"MORAL_BOUNDARY_VIOLATED"}


def test_character_mismatch_blocks_audit() -> None:
    audit = validate_decision(
        cautious_survival_policy("韩立"),
        DecisionEvent(
            character_name="厉飞雨",
            chapter_no=3,
            situation="替朋友出头。",
            action="冲上擂台。",
            risk_level="high",
        ),
    )

    assert not audit.passed
    assert {finding.code for finding in audit.findings} == {"CHARACTER_MISMATCH"}


def test_high_risk_without_preferred_tactic_is_warning_when_cause_exists() -> None:
    policy = cautious_survival_policy("韩立")

    audit = validate_decision(
        policy,
        DecisionEvent(
            character_name="韩立",
            chapter_no=30,
            situation="敌人追杀至山谷。",
            action="立刻反击。",
            risk_level="high",
            is_life_threat=True,
        ),
    )

    assert audit.passed
    assert {finding.code for finding in audit.findings} == {"PREFERRED_TACTIC_MISSING"}
    assert audit.findings[0].blocking is False


def test_decision_policy_block_renders_prompt_contract() -> None:
    block = build_decision_policy_block(cautious_survival_policy("韩立"))

    assert "主角决策策略" in block
    assert "cautious_survivalist" in block
    assert "public_vanity_duel" in block
    assert "重大冒险必须有生死威胁" in block
