"""Instruction 72: a relationship chapter with a contract must not stop the book.

《扫雪剑起》 (books/webui/c517201b-…) stopped before chapter 14: the outline already had a
contract for 顾寒/陆青禾, only scene 1's action missed the check. The repair prompt still said
the contract was empty, the model reworded it, the reworded copy voided the scene fixes, and
the writer stage raised “关系合同缺失：already_attempted”. 陆青禾 was also named in the scene
text but missing from the scenes' cast lists, so no repair could ever pass.
"""

from copy import deepcopy

import pytest

from bookengine.chapter_rhythm import relationship_contract
from bookengine.rhythm_contract_repair import apply_rhythm_contracts
from test_rhythm_completion70 import OfflineClient, bible as base_bible

CONTRACT = dict(
    people=["顾寒", "陆青禾"],
    cause="两人在药峰外谷互相拿出药瓶与名册残页",
    before="陆青禾只把顾寒当作扫雪杂役",
    after="陆青禾答应与顾寒共同追查旧案",
)


def outline():
    scenes = [
        dict(
            where="药峰外谷石棚",
            characters=["顾寒", "韩小满"],
            goal="让陆青禾交出药瓶来处",
            obstacle="陆青禾怀疑残页是偷来的",
            action="顾寒把残页摊在药瓶旁，指出纹路断口相合",
            choice="顾寒承认自己隐藏了剑意来历",
            result="陆青禾确认两件物证同源",
        ),
        dict(
            where="药峰外谷旧药道",
            characters=["顾寒", "韩小满"],
            goal="确定彼此查案的边界",
            obstacle="陆青禾必须承认药渣来自师姐旧案",
            action="陆青禾坦白药渣来历，顾寒把木牌交给她作约定",
            choice="陆青禾选择信任顾寒，答应同行",
            result="两人约定互相校验",
        ),
    ]
    return dict(number=14, type="情感", relationship_change=dict(CONTRACT), title="伤剑分路", scenes=scenes)


def bible():
    value = base_bible()
    value["volumes"] = [dict(number=2, start=11, end=20)]
    value["characters"] = [dict(name=n) for n in ("顾寒", "陆青禾", "韩小满")]
    value["story_design"] = dict(relationships=[])
    return value


def fixed_scenes():
    scenes = deepcopy(outline()["scenes"])
    scenes[0]["action"] = "顾寒向陆青禾坦白隐瞒剑意来历，请她信任自己"
    return scenes


def test_scene_cast_gains_a_contract_person_named_in_the_scene_text(tmp_path):
    source = outline()
    source["scenes"][0]["action"] = "顾寒向陆青禾坦白隐瞒，请她信任自己"
    result = apply_rhythm_contracts(OfflineClient(tmp_path), bible(), source, {})
    assert [s["characters"] for s in result["scenes"]] == [["顾寒", "韩小满", "陆青禾"]] * 2
    assert result["rhythm_contract_repair"]["writer_ready"] is True
    assert source["scenes"][0]["characters"] == ["顾寒", "韩小满"]


def test_a_reworded_contract_keeps_the_outline_contract_and_the_scene_fix(tmp_path):
    reworded = dict(CONTRACT, cause="顾寒以旧剑纹为证，陆青禾发现他因反噬发抖")
    client = OfflineClient(tmp_path, [dict(relationship_change=reworded, scenes=fixed_scenes())])
    result = apply_rhythm_contracts(client, bible(), outline(), {})
    assert relationship_contract(result) == CONTRACT
    assert result["scenes"][0]["action"] == fixed_scenes()[0]["action"]
    assert result["rhythm_contract_repair"]["status"] == "repaired"
    assert "relationship_change已定" in client.calls[0]["prompt"]


@pytest.mark.parametrize("reply", [dict(relationship_change={}), ValueError("bad model output")])
def test_writer_continues_with_the_contract_when_only_the_scene_check_fails(tmp_path, reply):
    client = OfflineClient(tmp_path, [reply])
    result = apply_rhythm_contracts(client, bible(), outline(), {}, chapter=14, kind="writer")
    note = result["rhythm_contract_repair"]
    assert relationship_contract(result) == CONTRACT
    assert note["writer_ready"] is False and note["writer_proceeded_with_contract"] is True
    changed = dict(outline(), payoff="新增字段")  # the outline gains fields between planning and drafting
    again = apply_rhythm_contracts(client, bible(), changed, {}, chapter=14, kind="writer")
    assert again["rhythm_contract_repair"]["reason"] == "already_attempted"
    assert relationship_contract(again) == CONTRACT and len(client.calls) == 1
