"""Instruction 68 (E4): relationship_change values copied from saved writing outlines of four books."""

from bookengine.chapter_rhythm import relationship_contract
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
