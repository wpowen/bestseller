from __future__ import annotations

import pytest

from bestseller.services.planning_context import (
    summarize_book_spec,
    summarize_cast_spec,
    summarize_world_spec,
)

pytestmark = pytest.mark.unit


def test_world_summary_preserves_rules_locations_and_factions() -> None:
    summary = summarize_world_spec(
        {
            "world_name": "雾港",
            "world_premise": "记忆可以抵押",
            "power_system": {
                "name": "债印",
                "tiers": ["灰印", "赤印"],
                "hard_limits": "每次使用都会遗忘一个名字",
            },
            "rules": [
                {
                    "name": "等价遗忘",
                    "description": "力量必须支付记忆",
                    "story_consequence": "主角不能无限使用",
                }
            ],
            "locations": [{"name": "旧码头", "type": "禁区", "story_role": "第一证据点"}],
            "factions": [
                {
                    "name": "记账局",
                    "goal": "垄断债印",
                    "relationship_to_protagonist": "追捕",
                }
            ],
        }
    )

    for required in ("雾港", "等价遗忘", "旧码头", "记账局", "每次使用都会遗忘一个名字"):
        assert required in summary


def test_book_and_cast_summaries_preserve_identity_and_character_contracts() -> None:
    book = summarize_book_spec(
        {
            "title": "雾港债书",
            "logline": "失忆账房追查自己的债印",
            "protagonist": {"name": "沈砚", "external_goal": "找回被抵押的妹妹记忆"},
            "key_characters": [
                {"name": "林灯", "role": "搭档", "relationship_to_protagonist": "互不信任"}
            ],
        }
    )
    cast = summarize_cast_spec(
        {
            "protagonist": {
                "name": "沈砚",
                "role": "账房",
                "goal": "找回记忆",
                "flaw": "不肯求助",
                "arc_trajectory": "从独行到结盟",
            },
            "antagonist": {"name": "闻衡", "role": "记账局长", "goal": "封存真相"},
        }
    )

    assert "林灯" in book
    assert "不得另行编造替代角色" in book
    for required in ("沈砚", "不肯求助", "从独行到结盟", "闻衡"):
        assert required in cast
