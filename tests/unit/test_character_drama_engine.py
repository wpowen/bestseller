# ruff: noqa: RUF001
from __future__ import annotations

import pytest

from bestseller.services.character_drama_engine import (
    build_character_drama_map,
    render_character_drama_prompt_block,
)

pytestmark = pytest.mark.unit


def _cast_spec() -> dict[str, object]:
    return {
        "protagonist": {
            "name": "林澈",
            "role": "protagonist",
            "goal": "夺回被宗门冻结的灵田经营权",
            "fear": "再次把盟友拖进无法偿还的债务",
            "flaw": "习惯把所有风险独自扛下",
            "secret": "他私下替旧同门承担了一笔灵契坏账",
            "arc_trajectory": "从独自控制到公开分权",
            "psych_profile": {
                "mbti": "INTJ",
                "big_five": {
                    "openness": "high",
                    "conscientiousness": "high",
                    "neuroticism": "medium",
                },
                "cognitive_biases": ["责任过度归因"],
                "attachment_style": "avoidant-secure",
            },
            "moral_framework": {
                "core_values": ["守约", "不把弱者当筹码"],
                "lines_never_crossed": ["不伪造盟友意愿"],
                "willing_to_sacrifice": ["个人名声", "短期收益"],
            },
            "ip_anchor": {
                "quirks": ["清账前会把账珠按颜色排成三列"],
                "tag_memory": "雨夜灵田里碎掉的账珠",
                "core_wound": "曾因替人隐瞒坏账失去一次公开信任",
                "independent_life": "夜里替散修修补小型契约",
            },
            "beliefs": {
                "ideology": "契约必须保护合作关系，而不是吞掉人",
                "crisis_of_faith": "发现守约也可能变成压迫工具",
            },
            "family_imprint": {
                "inherited_values": ["账要清，人要活"],
                "breaking_points": ["被要求牺牲一个无辜合作者换取大局"],
            },
            "relationships": [
                {
                    "character": "苏绾",
                    "type": "ally",
                    "tension": "她需要授权，他害怕授权后再次欠债",
                }
            ],
        },
        "antagonist": {
            "name": "陆衡",
            "role": "antagonist",
            "goal": "把灵田契约改成只服务宗门收益",
            "fear": "失控的情义账毁掉宗门秩序",
            "moral_framework": {
                "core_values": ["秩序", "效率"],
                "lines_never_crossed": ["公开承认制度会伤人"],
            },
            "villain_charisma": {
                "noble_motivation": "让宗门不再被私人情义拖垮",
                "philosophical_appeal": "牺牲少数关系，换取更多弟子的生路",
                "protagonist_mirror": "同样重视契约，却把契约当成筛选人的刀",
                "personal_code": "不许账目失控",
            },
        },
        "supporting_cast": [
            {
                "name": "苏绾",
                "role": "ally",
                "goal": "拿到独立管理灵田的授权",
                "fear": "永远只能替别人背书",
                "flaw": "一旦被质疑就主动切断合作",
                "moral_framework": {"core_values": ["自主", "清白"]},
                "ip_anchor": {"quirks": ["谈判时只看对方手里的契纸"]},
            }
        ],
        "conflict_map": [
            {
                "character_a": "林澈",
                "character_b": "陆衡",
                "conflict_type": "契约价值观冲突",
                "trigger_condition": "灵田试运营失败时谁承担坏账",
            }
        ],
    }


def test_character_drama_map_turns_personality_into_choice_pressure() -> None:
    drama_map = build_character_drama_map(_cast_spec(), language="zh-CN")

    protagonist = drama_map.protagonist
    assert protagonist.name == "林澈"
    assert "夺回被宗门冻结的灵田经营权" in protagonist.choice_axis
    assert "不伪造盟友意愿" in protagonist.choice_axis
    assert "习惯把所有风险独自扛下" in protagonist.false_belief
    assert "被要求牺牲一个无辜合作者换取大局" in protagonist.pressure_trigger
    assert "场景测试" not in protagonist.scene_test

    antagonist = drama_map.antagonists[0]
    assert "牺牲少数关系" in antagonist.temptation
    assert "同样重视契约" in antagonist.dramatic_function

    assert drama_map.relationship_tensions
    assert any(
        "灵田试运营失败" in item.conflict_trigger
        for item in drama_map.relationship_tensions
    )


def test_character_drama_prompt_block_renders_dynamic_axes_not_static_type_labels() -> None:
    block = render_character_drama_prompt_block(build_character_drama_map(_cast_spec()))

    assert "Character Drama Engine" in block
    assert "Choice axis" in block
    assert "Scene test" in block
    assert "Personality facts are not decoration" in block
    assert "INTJ" not in block
    assert "Big Five" not in block
    assert "夺回被宗门冻结的灵田经营权" in block
