"""Instruction 68 (E4): relationship_change values copied from saved writing outlines of four books."""

from bookengine.chapter_rhythm import apply_contract, chapter_type, relationship_contract
from bookengine.relationship_shapes import canonical_relationship_change
from bookengine.rhythm_writer import writer_rhythm_instruction

SCENES = [{"characters": ["秦野", "韩和笙"], "action": "坦白", "choice": "接受"}] * 2


def _outline(change):
    return {"type": "情感", "rhythm_type": "情感", "scenes": SCENES, "relationship_change": change}


def test_pair_written_as_the_key_is_read_even_when_a_name_contains_a_separator():
    change = {
        "秦野与韩和笙": {"before": "追捕者与被追者互不信任", "after": "建立有限互信", "cause": "秦野坦白反冲风险"}
    }
    contract = relationship_contract(_outline(change))
    assert contract["people"] == ["秦野", "韩和笙"]
    assert (contract["before"], contract["after"]) == ("追捕者与被追者互不信任", "建立有限互信")


def test_from_to_trigger_and_chinese_field_names_are_read():
    english = {
        "from": "视为随时可能背叛的掌权者",
        "to": "视为可以有限信任的同行者",
        "trigger": "阿永琛承认以部分真相换取兵权",
    }
    contract = relationship_contract(_outline(english))
    assert contract["cause"] == "阿永琛承认以部分真相换取兵权" and contract["after"] == "视为可以有限信任的同行者"
    chinese = {
        "人物": ["石子翎", "师父"],
        "起因": "石子翎主动承受刑锤",
        "变化前": "把命令当许可",
        "变化后": "坚持自己选择",
    }
    assert relationship_contract(_outline(chinese))["people"] == ["石子翎", "师父"]


def test_canonical_shape_and_unreadable_values_pass_through():
    canonical = {"people": ["秦野", "苏以宁"], "cause": "伤势恶化", "before": "当成伤者", "after": "允许知情"}
    assert canonical_relationship_change(canonical) == canonical
    assert canonical_relationship_change({}) == {}
    assert canonical_relationship_change(None) is None
    assert relationship_contract(_outline({})) is None


def test_writer_receives_the_relationship_duty_for_a_variant_shape():
    change = {"秦野与韩和笙": {"before": "互不信任", "after": "有限互信", "cause": "秦野坦白反冲风险"}}
    instruction = writer_rhythm_instruction(_outline(change))
    assert "人物=秦野、韩和笙" in instruction and "变化后=有限互信" in instruction


def _side_note(kind, change):
    return {"type": kind, "rhythm_type": kind, "scenes": SCENES, "relationship_change": change}


def test_variant_side_note_on_a_non_emotion_chapter_does_not_turn_it_into_an_emotion_chapter():
    # 《摆摊真的能转运吗》 chapters 36 (日常, chase scenes) and 8 (收获): the side note was read as a full
    # contract, apply_contract relabelled both as 情感 without touching the scenes, and both were written as 谋划.
    pair = {"pair": ["陆青", "林小夏"], "cause": "陆青拒绝接管纸条", "before": "等待指令", "after": "相信自己"}
    keyed = {"陆青与林小夏": {"before": "林小夏只替陆夏修旧物", "after": "开始参与纸条校验", "cause": "共同承担"}}
    for kind, change in (("日常", pair), ("收获", keyed), ("对抗", pair)):
        row = _side_note(kind, change)
        assert relationship_contract(row) is None
        assert chapter_type(row) == kind
        assert apply_contract(dict(row))["type"] == kind


def test_canonical_contract_still_makes_an_emotion_chapter_as_before_instruction_68():
    canonical = {"people": ["秦野", "苏以宁"], "cause": "伤势恶化", "before": "当成伤者", "after": "允许知情"}
    assert chapter_type(_side_note("对抗", canonical)) == "情感"


def test_short_chinese_before_after_keys_are_read_instruction_77b():
    # 《扫雪剑起》 24ddbcc8 stopped before chapter 24: the planner wrote 起因/前/后 under a "甲与乙" key.
    from bookengine.rhythm_contract_repair import relationship_contract

    row = {
        "type": "情感",
        "relationship_change": {
            "顾寒与裴煦阳": {
                "起因": "后廊剑鸣失控，两人必须共同压阵",
                "前": "顾寒追斩，裴煦阳只顾夺回残片",
                "后": "两人暂以旧阵止鸣为条件合作",
            }
        },
        "scenes": [],
    }
    contract = relationship_contract(row)
    assert contract and contract["people"] == ["顾寒", "裴煦阳"]
    assert contract["before"] == "顾寒追斩，裴煦阳只顾夺回残片"
    assert contract["after"] == "两人暂以旧阵止鸣为条件合作"


def test_pair_key_with_a_from_to_sentence_is_read_instruction_77c():
    # Re-planned chapter 24 of 24ddbcc8 wrote the change as one sentence under the pair key.
    from bookengine.rhythm_contract_repair import relationship_contract

    row = {
        "type": "情感",
        "opening_conflict": "后廊剑鸣失控，两人必须共同压阵",
        "relationship_change": {"顾寒与裴煦阳": "由互相防备、各自打算，转为承认伤势与剑招限制后的有限同行。"},
        "scenes": [],
    }
    contract = relationship_contract(row)
    assert contract and contract["people"] == ["顾寒", "裴煦阳"]
    assert contract["before"] == "互相防备、各自打算"
    assert contract["after"] == "承认伤势与剑招限制后的有限同行"
    assert contract["cause"]
