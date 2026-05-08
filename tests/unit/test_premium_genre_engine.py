from __future__ import annotations

import pytest

from bestseller.services.premium_genre_engine import build_premium_genre_engine_blocks

pytestmark = pytest.mark.unit


def _xianxia_metadata() -> dict[str, object]:
    return {
        "sub_genre": "凡人流修仙",
        "world_spec": {
            "world_name": "青岚界",
            "power_system": {
                "name": "灵根修行",
                "tiers": ["炼气", "筑基", "金丹"],
                "protagonist_starting_tier": "炼气十层",
                "bottlenecks": [
                    {
                        "key": "foundation_trial",
                        "at_realm": "炼气",
                        "target_realm": "筑基",
                        "description": "筑基必须有筑基丹、闭关和生死压力。",
                        "required_cause_kinds": ["resource", "trial"],
                    }
                ],
            },
            "power_structure": "宗门垄断筑基资源。",
        },
        "cast_spec": {
            "protagonist": {
                "name": "韩立式主角",
                "power_tier": "炼气十层",
                "resources": [
                    {
                        "resource_key": "筑基丹",
                        "amount": 1,
                        "source": "秘境所得",
                    }
                ],
                "techniques": [
                    {
                        "key": "changchun",
                        "name": "长春功",
                        "required_realm": "炼气",
                    }
                ],
            }
        },
        "volume_plan": [
            {
                "volume_number": 1,
                "volume_title": "入宗夺丹",
                "opening_state": {"protagonist_power_tier": "炼气十层"},
                "volume_resolution": {"protagonist_power_tier": "筑基初期"},
            }
        ],
    }


def test_builds_progression_and_default_cautious_policy_for_xianxia() -> None:
    blocks = build_premium_genre_engine_blocks(
        project_metadata=_xianxia_metadata(),
        genre="xianxia",
        sub_genre="凡人流修仙",
        current_volume=1,
    )

    assert "【进阶体系约束】" in blocks.progression_context_block
    assert "炼气 → 筑基 → 金丹" in blocks.progression_context_block
    assert "筑基丹=1" in blocks.progression_context_block
    assert "不得空升级" in blocks.progression_context_block
    assert "【主角决策策略】" in blocks.decision_policy_block
    assert "韩立式主角" in blocks.decision_policy_block
    assert "public_vanity_duel" in blocks.decision_policy_block
    assert blocks.warnings == ()


def test_explicit_decision_policy_overrides_progression_default() -> None:
    metadata = _xianxia_metadata()
    metadata["decision_policy"] = {
        "character_name": "韩立式主角",
        "archetype": "reckless_hero",
        "risk_tolerance": "high",
        "preferred_tactics": [
            {"key": "protect", "description": "先保护同伴。"},
        ],
    }

    blocks = build_premium_genre_engine_blocks(
        project_metadata=metadata,
        genre="xianxia",
        sub_genre="凡人流修仙",
    )

    assert "reckless_hero" in blocks.decision_policy_block
    assert "risk tolerance" not in blocks.decision_policy_block.lower()
    assert "public_vanity_duel" not in blocks.decision_policy_block


def test_non_progression_metadata_does_not_invent_cautious_policy() -> None:
    blocks = build_premium_genre_engine_blocks(
        project_metadata={
            "sub_genre": "cozy mystery",
            "cast_spec": {"protagonist": {"name": "周宁"}},
        },
        genre="mystery",
        sub_genre="cozy mystery",
    )

    assert blocks.progression_context_block == ""
    assert blocks.decision_policy_block == ""
    assert blocks.warnings == ()


def test_uses_power_system_metadata_fallback_when_world_spec_missing() -> None:
    blocks = build_premium_genre_engine_blocks(
        project_metadata={
            "sub_genre": "urban cultivation",
            "power_system": {
                "name": "都市灵气",
                "tiers": ["淬体", "开脉"],
                "protagonist_starting_tier": "淬体",
            },
            "cast_spec": {"protagonist": {"name": "林澈", "power_tier": "淬体"}},
        },
        genre="urban-cultivation",
        sub_genre="urban cultivation",
    )

    assert "都市灵气" in blocks.progression_context_block
    assert "淬体 → 开脉" in blocks.progression_context_block
    assert "林澈" in blocks.decision_policy_block


def test_builds_rule_system_block_from_story_bible_context() -> None:
    blocks = build_premium_genre_engine_blocks(
        project_metadata={"sub_genre": "民俗悬疑"},
        story_bible_context={
            "world_rules": [
                {
                    "rule_code": "R-001",
                    "name": "亥时不看宅",
                    "description": "凶宅在亥时会放大否认者的执念。",
                    "story_consequence": "入局者不能靠逃跑破局。",
                    "exploitation_potential": "逼当事人承认旧罪才能开门。",
                    "future_backlash": "承认真相会把债转到见证者身上。",
                }
            ]
        },
        genre="suspense-mystery",
        sub_genre="民俗悬疑",
    )

    assert "【规则系统约束】" in blocks.rule_system_context_block
    assert "R-001/亥时不看宅" in blocks.rule_system_context_block
    assert "破局路径: 逼当事人承认旧罪才能开门" in blocks.rule_system_context_block
    assert "代价/反噬: 承认真相会把债转到见证者身上" in blocks.rule_system_context_block
    assert "不得只当氛围描写" in blocks.rule_system_context_block
    assert blocks.warnings == ()


def test_rule_genre_without_rules_surfaces_warning() -> None:
    blocks = build_premium_genre_engine_blocks(
        project_metadata={"sub_genre": "rule horror"},
        genre="horror",
        sub_genre="rule horror",
    )

    assert blocks.rule_system_context_block == ""
    assert blocks.warnings == ("rule_system_missing",)
