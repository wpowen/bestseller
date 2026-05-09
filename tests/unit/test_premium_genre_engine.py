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


def test_builds_relationship_agency_block_for_romantasy() -> None:
    blocks = build_premium_genre_engine_blocks(
        project_metadata={
            "sub_genre": "romantasy",
            "character": {
                "romance_mode": "slow-burn enemies-to-lovers",
                "relationship_tension": "信任与权力互相拉扯",
            },
            "cast_spec": {
                "protagonist": {
                    "name": "Elara",
                    "relationships": [
                        {
                            "character": "Kael",
                            "type": "enemy protector",
                            "tension": "血契逼他们合作, 但双方都保留底牌。",
                        }
                    ],
                },
                "supporting_cast": [
                    {
                        "name": "Kael",
                        "role": "love_interest",
                        "relationship_to_protagonist": "敌对保护者",
                        "evolution_arc": "从互相试探到愿意为对方承担政治代价",
                    }
                ],
            },
        },
        story_bible_context={
            "interpersonal_promises": [
                {
                    "promisor_label": "Kael",
                    "promisee_label": "Elara",
                    "content": "在月廷审判前不会暴露她的血脉。",
                    "kind": "oath",
                }
            ]
        },
        genre="fantasy romance",
        sub_genre="romantasy",
    )

    assert "【关系张力与主角能动性约束】" in blocks.relationship_agency_context_block
    assert "slow-burn enemies-to-lovers" in blocks.relationship_agency_context_block
    assert "Elara -> Kael" in blocks.relationship_agency_context_block
    assert "Kael -> Elara" in blocks.relationship_agency_context_block
    assert "信任/权力/误会/承诺" in blocks.relationship_agency_context_block
    assert "主角必须有主动选择和代价" in blocks.relationship_agency_context_block
    assert blocks.warnings == ()


def test_female_no_cp_without_relationship_network_surfaces_agency_warning() -> None:
    blocks = build_premium_genre_engine_blocks(
        project_metadata={
            "sub_genre": "female-growth-ncp",
            "cast_spec": {"protagonist": {"name": "沈照"}},
        },
        genre="female-growth",
        sub_genre="female-growth-ncp",
    )

    assert blocks.relationship_agency_context_block == ""
    assert blocks.warnings == ("relationship_agency_missing",)


def test_builds_faction_ecology_block_for_clan_cultivation() -> None:
    blocks = build_premium_genre_engine_blocks(
        project_metadata={
            "sub_genre": "家族修仙",
            "factions": [
                {
                    "name": "李氏",
                    "goal": "守住灵田并培养下一代筑基种子。",
                    "method": "联姻、坊市交易、暗中保护族中苗子。",
                    "relationship_to_protagonist": "主角的家族根基",
                    "internal_conflict": "老派长老重保守, 少壮派要冒险扩张。",
                    "current_pressure": "坊市灵米价格被王氏压低。",
                    "next_reaction": "若主角夺回水脉, 李氏会公开改换继承排序。",
                },
                {
                    "name": "王氏",
                    "goal": "垄断河谷水脉。",
                    "method": "压价、挑拨、控制坊市执事。",
                    "relationship_to_protagonist": "资源竞争者",
                    "internal_conflict": "嫡支和旁支争夺水脉收益分配。",
                },
            ],
        },
        genre="xianxia",
        sub_genre="家族修仙",
    )

    assert "【阵营生态与反应压力约束】" in blocks.faction_ecology_context_block
    assert "李氏" in blocks.faction_ecology_context_block
    assert "王氏" in blocks.faction_ecology_context_block
    assert "内部矛盾" in blocks.faction_ecology_context_block
    assert "下一步反应" in blocks.faction_ecology_context_block
    assert "不得只写“所有势力震惊”" in blocks.faction_ecology_context_block
    assert blocks.warnings == ()


def test_faction_heavy_genre_without_factions_surfaces_warning() -> None:
    blocks = build_premium_genre_engine_blocks(
        project_metadata={"sub_genre": "strategy-worldbuilding"},
        genre="strategy-worldbuilding",
        sub_genre="strategy-worldbuilding",
    )

    assert blocks.faction_ecology_context_block == ""
    assert blocks.warnings == ("faction_ecology_missing",)


def test_premium_state_ledger_feeds_next_scene_blocks() -> None:
    blocks = build_premium_genre_engine_blocks(
        project_metadata={
            "sub_genre": "家族修仙",
            "world_spec": {
                "world_name": "青岚界",
                "power_system": {
                    "name": "灵根修行",
                    "tiers": ["炼气", "筑基"],
                    "protagonist_starting_tier": "炼气十层",
                },
            },
            "cast_spec": {"protagonist": {"name": "沈砚", "power_tier": "炼气十层"}},
            "premium_state_ledger": {
                "progression_events": [
                    {
                        "chapter_number": 3,
                        "event_type": "resource_spent",
                        "subject": "沈砚",
                        "resource_key": "筑基丹",
                        "delta": -1,
                        "cause": "换取港务官放行",
                    }
                ],
                "rule_events": [
                    {
                        "chapter_number": 3,
                        "rule_code": "R-001",
                        "name": "试炼禁令",
                        "visible_effect": "执法堂封港",
                        "cost": "散修身份暴露",
                    }
                ],
                "faction_reactions": [
                    {
                        "chapter_number": 3,
                        "faction": "执法堂",
                        "trigger": "筑基丹消失",
                        "reaction": "封锁码头并先查散修",
                    }
                ],
                "relationship_events": [
                    {
                        "chapter_number": 3,
                        "character_a": "沈砚",
                        "character_b": "港务官",
                        "axis": "trust",
                        "after": "有限合作",
                        "active_choice": "主动交出丹药",
                        "cost": "失去突破资源",
                    }
                ],
            },
        },
        genre="xianxia",
        sub_genre="家族修仙",
    )

    assert "【近期进阶状态变更】" in blocks.progression_context_block
    assert "筑基丹" in blocks.progression_context_block
    assert "R-001/试炼禁令" in blocks.rule_system_context_block
    assert "执法堂" in blocks.faction_ecology_context_block
    assert "沈砚 -> 港务官" in blocks.relationship_agency_context_block


def test_premium_state_ledger_report_surfaces_warnings() -> None:
    blocks = build_premium_genre_engine_blocks(
        project_metadata={
            "premium_state_ledger_report": {
                "passed": False,
                "findings": [
                    {
                        "code": "generic_faction_reaction",
                        "severity": "critical",
                        "path": "faction_reactions[0]",
                    }
                ],
            }
        },
        genre="xianxia",
        sub_genre="凡人流修仙",
    )

    assert "premium_state_ledger:generic_faction_reaction" in blocks.warnings
