"""Coercion tests for Pydantic inputs that backstop LLM-shaped story-bible payloads.

LLMs regularly return ``world_spec`` / ``cast_spec`` / ``volume_plan`` payloads with
nested dicts where the schema expects a flat string, or with dict values where the
schema expects a list. Before the ``coerce_to_narrative_string`` /
``coerce_to_string_list`` / ``coerce_to_int_list`` helpers and the ``mode='before'``
validators shipped in this patch, those payloads would reach the planner, fail
Pydantic validation, and burn retry tokens without ever succeeding. These tests
lock in the recovery behaviour against real failure payloads captured in the
planner artifact store.
"""

from __future__ import annotations

import pytest

from bestseller.domain.story_bible import (
    CastSpecInput,
    CharacterIPAnchorInput,
    CharacterInput,
    CharacterVoiceProfileInput,
    ConflictForceInput,
    HistoryEventInput,
    PowerSystemInput,
    VolumePlanEntryInput,
    WorldRuleInput,
    WorldSpecInput,
    coerce_to_int_list,
    coerce_to_narrative_string,
    coerce_to_string_list,
)

pytestmark = pytest.mark.unit


class TestCoerceToNarrativeString:
    def test_passes_string_through(self) -> None:
        assert coerce_to_narrative_string("hello") == "hello"

    def test_returns_none_for_none(self) -> None:
        assert coerce_to_narrative_string(None) is None

    def test_preferred_description_key_wins(self) -> None:
        result = coerce_to_narrative_string(
            {"description": "这是故事的核心", "extra": "忽略"}
        )
        assert result == "这是故事的核心"

    def test_dict_without_preferred_key_flattens_all_entries(self) -> None:
        result = coerce_to_narrative_string(
            {"层级1": "描述A", "层级2": "描述B"}
        )
        assert result is not None
        assert "层级1" in result
        assert "描述A" in result
        assert "描述B" in result

    def test_deeply_nested_dict_flattens(self) -> None:
        payload = {
            "overview": {"summary": "顶层概述"},
            "details": {
                "sub": {"content": "次级细节"},
                "more": ["item1", "item2"],
            },
        }
        result = coerce_to_narrative_string(payload)
        assert result is not None
        assert "顶层概述" in result

    def test_list_of_dicts_flattens_to_bulleted_text(self) -> None:
        payload = [
            {"name": "事件A", "description": "第一件大事"},
            {"name": "事件B", "description": "第二件大事"},
        ]
        result = coerce_to_narrative_string(payload)
        assert result is not None
        assert "第一件大事" in result
        assert "第二件大事" in result

    def test_depth_cap_prevents_infinite_recursion(self) -> None:
        payload: dict = {"level": 0}
        cursor = payload
        for i in range(1, 10):
            cursor["next"] = {"level": i}
            cursor = cursor["next"]
        result = coerce_to_narrative_string(payload)
        assert isinstance(result, str)

    def test_empty_dict_returns_none(self) -> None:
        assert coerce_to_narrative_string({}) is None

    def test_whitespace_string_passes_through_unchanged(self) -> None:
        # Helper short-circuits on ``isinstance(value, str)`` — callers are
        # responsible for trimming whitespace on a bare-string path, so only
        # nested dict/list payloads get re-flattened.
        assert coerce_to_narrative_string("   ") == "   "


class TestCoerceToStringList:
    def test_none_becomes_empty(self) -> None:
        assert coerce_to_string_list(None) == []

    def test_string_wraps_into_single_element(self) -> None:
        assert coerce_to_string_list("单条目") == ["单条目"]

    def test_list_of_strings_passes_through(self) -> None:
        assert coerce_to_string_list(["a", "b", "c"]) == ["a", "b", "c"]

    def test_dict_flattens_into_key_value_entries(self) -> None:
        result = coerce_to_string_list({"条目1": "细节A", "条目2": "细节B"})
        assert len(result) == 2
        assert "细节A" in result[0] or "条目1" in result[0]

    def test_list_of_mixed_scalars_and_dicts(self) -> None:
        payload = [
            "简单字符串",
            {"name": "复杂对象"},
            42,
        ]
        result = coerce_to_string_list(payload)
        assert "简单字符串" in result
        assert len(result) == 3

    def test_tuple_treated_as_list(self) -> None:
        assert coerce_to_string_list(("x", "y")) == ["x", "y"]

    def test_scalar_int_becomes_str_list(self) -> None:
        assert coerce_to_string_list(42) == ["42"]


class TestCoerceToIntList:
    def test_none_becomes_empty(self) -> None:
        assert coerce_to_int_list(None) == []

    def test_list_of_ints_passes(self) -> None:
        assert coerce_to_int_list([1, 3, 5]) == [1, 3, 5]

    def test_chinese_range_expands(self) -> None:
        result = coerce_to_int_list("1-10章")
        assert result == list(range(1, 11))

    def test_em_dash_range(self) -> None:
        assert coerce_to_int_list("5—8") == [5, 6, 7, 8]

    def test_comma_separated_numbers(self) -> None:
        assert coerce_to_int_list("1,3,5") == [1, 3, 5]

    def test_unparseable_string_returns_empty(self) -> None:
        assert coerce_to_int_list("贯穿全书") == []

    def test_list_with_string_numbers(self) -> None:
        assert coerce_to_int_list(["1", "vol 3", "第5卷"]) == [1, 3, 5]

    def test_runaway_range_cap(self) -> None:
        result = coerce_to_int_list("1-999")
        assert len(result) <= 200


class TestWorldSpecInputCoercion:
    def test_power_structure_accepts_nested_dict(self) -> None:
        payload = {
            "world_name": "青萝镇",
            "power_structure": {
                "overview": "以王李两家为核心的百年联盟",
                "factions": [{"name": "王家", "goal": "守护祖地"}],
            },
            "forbidden_zones": [
                {"name": "封印禁地", "location": "镇东古井之下"}
            ],
        }
        spec = WorldSpecInput.model_validate(payload)
        assert isinstance(spec.power_structure, str)
        assert "王李" in spec.power_structure or "百年联盟" in spec.power_structure
        assert isinstance(spec.forbidden_zones, str)
        assert "封印禁地" in spec.forbidden_zones

    def test_history_events_accepts_name_alias(self) -> None:
        payload = {
            "world_name": "测试世界",
            "history_key_events": [
                {"name": "器灵初现", "description": "关键节点"},
                {"event": "妖族之战", "relevance": "奠定格局"},
            ],
        }
        spec = WorldSpecInput.model_validate(payload)
        assert len(spec.history_key_events) == 2
        assert spec.history_key_events[0].event == "器灵初现"
        assert spec.history_key_events[1].event == "妖族之战"

    def test_power_system_accepts_bare_string(self) -> None:
        payload = {"power_system": "灵气体系,共九阶"}
        spec = WorldSpecInput.model_validate(payload)
        assert isinstance(spec.power_system, PowerSystemInput)
        assert spec.power_system.name is not None
        assert "灵气" in spec.power_system.name

    def test_world_premise_accepts_dict(self) -> None:
        payload = {
            "world_premise": {
                "summary": "灵力复苏的现代都市",
                "rules": "只有特定血脉能觉醒",
            }
        }
        spec = WorldSpecInput.model_validate(payload)
        assert spec.world_premise is not None
        assert "灵力" in spec.world_premise


class TestWorldRuleInputCoercion:
    def test_rule_name_alias(self) -> None:
        payload = {
            "rule_id": "R01",
            "rule_name": "血脉觉醒",
            "description": "只有王李血脉能觉醒器灵",
        }
        rule = WorldRuleInput.model_validate(payload)
        assert rule.name == "血脉觉醒"

    def test_description_accepts_nested_dict(self) -> None:
        payload = {
            "name": "器灵契约",
            "description": {"summary": "与器灵订立三年之约", "detail": "细节内容"},
        }
        rule = WorldRuleInput.model_validate(payload)
        assert "三年之约" in rule.description


class TestCharacterInputCoercion:
    def test_voice_profile_tic_list_accepts_dict(self) -> None:
        voice = CharacterVoiceProfileInput.model_validate(
            {"verbal_tics": {"常用1": "说一不二", "常用2": "有何不可"}}
        )
        assert len(voice.verbal_tics) == 2

    def test_ip_anchor_quirks_accepts_scalar_string(self) -> None:
        anchor = CharacterIPAnchorInput.model_validate(
            {"quirks": "总是左手握剑", "core_wound": {"summary": "丧父之痛"}}
        )
        assert anchor.quirks == ["总是左手握剑"]
        assert anchor.core_wound is not None
        assert "丧父" in anchor.core_wound

    def test_character_age_accepts_english_decade(self) -> None:
        character = CharacterInput.model_validate(
            {"name": "赵五", "role": "ally", "age": "late 40s"}
        )
        assert character.age == 48

    def test_character_age_unknown_degrades_to_none(self) -> None:
        character = CharacterInput.model_validate(
            {"name": "不详角色", "role": "ally", "age": "indeterminate (fae)"}
        )
        assert character.age is None

    def test_character_role_truncates_arc_sentence(self) -> None:
        character = CharacterInput.model_validate(
            {
                "name": "王六",
                "role": "from lost son becomes heir through trial",
            }
        )
        assert len(character.role) <= 64

    def test_social_network_family_accepts_relation_keyed_dict(self) -> None:
        character = CharacterInput.model_validate(
            {
                "name": "王六",
                "role": "ally",
                "social_network": {
                    "family": [
                        {
                            "father（已故）": {
                                "emotional_weight": "愧疚",
                                "influence": "让他无法放弃承诺",
                            }
                        }
                    ]
                },
            }
        )

        assert character.social_network.family[0].name == "father（已故）"
        assert character.social_network.family[0].bond == "father（已故）"
        assert character.social_network.family[0].influence == "让他无法放弃承诺"


class TestConflictForceInputCoercion:
    def test_active_volumes_accepts_range_string(self) -> None:
        force = ConflictForceInput.model_validate(
            {
                "name": "邻镇豪强",
                "force_type": "faction",
                "active_volumes": "1-3",
            }
        )
        assert force.active_volumes == [1, 2, 3]

    def test_force_type_alias_coercion(self) -> None:
        force = ConflictForceInput.model_validate(
            {
                "name": "心魔",
                "force_type": "psychological",
            }
        )
        assert force.force_type == "internal"

    def test_threat_description_accepts_nested_dict(self) -> None:
        force = ConflictForceInput.model_validate(
            {
                "name": "外患",
                "force_type": "systemic",
                "threat_description": {
                    "summary": "帝国压境",
                    "details": "大军十万",
                },
            }
        )
        assert force.threat_description is not None
        assert "帝国" in force.threat_description


class TestCastSpecInputCoercion:
    def test_conflict_map_dict_to_list(self) -> None:
        spec = CastSpecInput.model_validate(
            {
                "protagonist": {"name": "王青峰", "role": "protagonist"},
                "conflict_map": {
                    "王青峰 vs 李墨白": {
                        "conflict_type": "血脉之争",
                        "trigger_condition": "初遇之际",
                    },
                    "王青峰 vs 祖庭": {
                        "conflict_type": "理念之争",
                        "trigger_condition": "中期揭露",
                    },
                },
            }
        )
        assert len(spec.conflict_map) == 2
        assert spec.conflict_map[0].character_a == "王青峰"
        assert spec.conflict_map[0].character_b in {"李墨白", "祖庭"}

    def test_name_keyed_protagonist_unwraps(self) -> None:
        spec = CastSpecInput.model_validate(
            {
                "protagonist": {
                    "王青峰": {
                        "role": "protagonist",
                        "age": 20,
                        "goal": "守护青萝",
                    }
                }
            }
        )
        assert spec.protagonist is not None
        assert spec.protagonist.name == "王青峰"
        assert spec.protagonist.age == 20

    def test_name_keyed_supporting_cast_dict_unwraps(self) -> None:
        spec = CastSpecInput.model_validate(
            {
                "supporting_cast": {
                    "师父": {"role": "mentor", "age": 50},
                    "青儿": {"role": "sister", "age": 15},
                }
            }
        )
        assert len(spec.supporting_cast) == 2
        names = sorted(c.name for c in spec.supporting_cast)
        assert names == ["师父", "青儿"]

    def test_mixed_supporting_cast_with_name_keyed_entries(self) -> None:
        spec = CastSpecInput.model_validate(
            {
                "supporting_cast": [
                    {"name": "直接角色", "role": "ally"},
                    {"包装角色": {"role": "mentor", "age": 60}},
                ]
            }
        )
        assert len(spec.supporting_cast) == 2
        names = {c.name for c in spec.supporting_cast}
        assert "直接角色" in names
        assert "包装角色" in names

    def test_conflict_map_empty_passthrough(self) -> None:
        spec = CastSpecInput.model_validate({})
        assert spec.conflict_map == []

    def test_protagonist_role_normalized(self) -> None:
        spec = CastSpecInput.model_validate(
            {"protagonist": {"name": "主角", "role": "some-other-role"}}
        )
        assert spec.protagonist is not None
        assert spec.protagonist.role == "protagonist"

    def test_character_beliefs_accept_legacy_string(self) -> None:
        spec = CastSpecInput.model_validate(
            {
                "supporting_cast": [
                    {
                        "name": "秩序官",
                        "role": "official",
                        "beliefs": "个人意志在秩序稳定面前不值一提",
                    }
                ]
            }
        )
        assert spec.supporting_cast[0].beliefs.ideology == "个人意志在秩序稳定面前不值一提"

    def test_primary_character_lists_use_first_and_preserve_extras(self) -> None:
        spec = CastSpecInput.model_validate(
            {
                "protagonist": [
                    {"name": "主角甲", "role": "protagonist"},
                    {"name": "主角乙", "role": "protagonist"},
                ],
                "antagonist": [
                    {"name": "反派甲", "role": "antagonist"},
                    {"name": "反派乙", "role": "antagonist"},
                ],
            }
        )

        assert spec.protagonist is not None
        assert spec.protagonist.name == "主角甲"
        assert spec.antagonist is not None
        assert spec.antagonist.name == "反派甲"
        assert {character.name for character in spec.supporting_cast} == {"主角乙", "反派乙"}


class TestVolumePlanEntryInputCoercion:
    def test_word_count_target_parses_约_prefix(self) -> None:
        entry = VolumePlanEntryInput.model_validate(
            {
                "volume_number": 1,
                "title": "启始卷",
                "word_count_target": "约 12000 字",
            }
        )
        assert entry.word_count_target == 12000

    def test_word_count_target_parses_comma_thousands(self) -> None:
        entry = VolumePlanEntryInput.model_validate(
            {
                "volume_number": 2,
                "title": "中盘",
                "word_count_target": "约 12,000 字",
            }
        )
        assert entry.word_count_target == 12000

    def test_word_count_target_numeric_passes(self) -> None:
        entry = VolumePlanEntryInput.model_validate(
            {"volume_number": 3, "title": "终局", "word_count_target": 15000}
        )
        assert entry.word_count_target == 15000

    def test_word_count_target_unparseable_degrades_to_none(self) -> None:
        entry = VolumePlanEntryInput.model_validate(
            {
                "volume_number": 4,
                "title": "外传",
                "word_count_target": "未定",
            }
        )
        assert entry.word_count_target is None

    def test_key_reveals_dict_to_list(self) -> None:
        entry = VolumePlanEntryInput.model_validate(
            {
                "volume_number": 1,
                "title": "V1",
                "key_reveals": {
                    "真相1": "师父的真实身份",
                    "真相2": "祖庭的阴谋",
                },
            }
        )
        assert len(entry.key_reveals) == 2
        assert any("师父" in r for r in entry.key_reveals)

    def test_foreshadowing_list_of_dicts(self) -> None:
        entry = VolumePlanEntryInput.model_validate(
            {
                "volume_number": 1,
                "title": "V1",
                "foreshadowing_planted": [
                    {"name": "伏笔A", "description": "剑鞘暗纹"},
                    "直接字符串伏笔",
                ],
            }
        )
        assert len(entry.foreshadowing_planted) == 2

    def test_narrative_field_accepts_dict(self) -> None:
        entry = VolumePlanEntryInput.model_validate(
            {
                "volume_number": 1,
                "title": "V1",
                "volume_theme": {"summary": "成长与背叛"},
            }
        )
        assert entry.volume_theme is not None
        assert "成长" in entry.volume_theme


class TestRealFailurePayloads:
    """Regression cases assembled from real planner-failure artifacts."""

    def test_world_spec_with_nested_power_structure(self) -> None:
        payload = {
            "world_name": "器灵大陆",
            "world_premise": "灵与人共生的上古大陆",
            "rules": [
                {
                    "rule_name": "血契",
                    "description": {"summary": "人与器灵须以血立约"},
                }
            ],
            "power_structure": {
                "overview": "九大门派以祖庭为尊",
                "factions": [
                    {"name": "祖庭", "goal": "维系秩序"},
                ],
            },
            "history_key_events": [
                {"name": "祖庭立约", "description": "三千年前"},
            ],
            "forbidden_zones": [
                {"name": "祖庭禁地", "rules": "外人不得入"},
                {"name": "镇魂井"},
            ],
        }
        spec = WorldSpecInput.model_validate(payload)
        assert spec.power_structure is not None
        assert "祖庭" in spec.power_structure
        assert spec.forbidden_zones is not None
        assert "祖庭禁地" in spec.forbidden_zones
        assert spec.history_key_events[0].event == "祖庭立约"

    def test_cast_spec_with_name_keyed_protagonist_and_dict_supporting(self) -> None:
        payload = {
            "protagonist": {
                "王青峰": {
                    "role": "protagonist",
                    "age": 20,
                    "background": "孤儿出身",
                    "goal": "复兴家族",
                    "power_tier": "阶1",
                    "ip_anchor": {
                        "quirks": ["左手握剑", "不食甜食", "见雨必伞"],
                        "core_wound": "十岁失母",
                    },
                }
            },
            "supporting_cast": {
                "师父": {
                    "role": "mentor",
                    "age": "late 50s",
                    "goal": "传承家学",
                }
            },
            "conflict_map": {
                "王青峰 vs 李墨白": {
                    "conflict_type": "血脉之争",
                    "trigger_condition": "初遇",
                }
            },
        }
        spec = CastSpecInput.model_validate(payload)
        assert spec.protagonist is not None
        assert spec.protagonist.name == "王青峰"
        assert spec.protagonist.age == 20
        assert spec.protagonist.role == "protagonist"
        assert len(spec.protagonist.ip_anchor.quirks) == 3
        assert len(spec.supporting_cast) == 1
        assert spec.supporting_cast[0].name == "师父"
        assert spec.supporting_cast[0].age == 58
        assert len(spec.conflict_map) == 1
        assert spec.conflict_map[0].character_a == "王青峰"
        assert spec.conflict_map[0].character_b == "李墨白"

    def test_volume_plan_entry_with_mixed_shapes(self) -> None:
        payload = {
            "volume_number": 1,
            "title": "启始·破晓",
            "volume_theme": {"summary": "从凡人到入道"},
            "word_count_target": "约 120,000 字",
            "chapter_count_target": 30,
            "key_reveals": {
                "身世": "孤儿并非孤儿",
                "血脉": "拥有稀有器灵亲和",
            },
            "foreshadowing_planted": [
                {"hint": "剑鞘暗纹", "detail": "会在关键时刻显现"},
                "邻居老人的神秘身份",
            ],
            "foreshadowing_paid_off": [],
            "volume_goal": "觉醒器灵并进入宗门",
            "reader_hook_to_next": {
                "cliffhanger": "祖庭来人",
                "promise": "下一卷揭露门派内部斗争",
            },
        }
        entry = VolumePlanEntryInput.model_validate(payload)
        assert entry.word_count_target == 120000
        assert entry.chapter_count_target == 30
        assert len(entry.key_reveals) == 2
        assert len(entry.foreshadowing_planted) == 2
        assert entry.reader_hook_to_next is not None
        assert "祖庭" in entry.reader_hook_to_next or "下一卷" in entry.reader_hook_to_next
