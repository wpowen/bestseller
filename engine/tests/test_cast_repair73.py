"""Instruction 73: gender repairs were rolled back in every chapter of 《扫雪剑起》.

In books/webui/c517201b-… chapters 3, 7, 10 and 47 kept “陆师兄” for the female 陆青禾 (and
“沈师姐” for the male 沈和笙). The repair prompt packed 人物卡 and 章纲 first; with a 10000-char
budget the 待修句 section shrank to 31 characters and the text was dropped, so the model answered
“no sentences provided” with empty edits and the recheck rolled back even the pronoun fix.
"""

from bookengine.cast_consistency import cast_findings, repair_cast_consistency
from bookengine.longutil import ask

QUOTE = "韩小满从柴垛后探出头：“陆师兄，你不是去药峰取药吗？”"
TEXT = "陆青禾踩着冻柴走来。" + QUOTE + "陆青禾没有答。他说着，抬脚踢开挡路的冻柴。"


def bible():
    filler = "长" * 600
    cards = [dict(name="陆青禾", gender="女", identity="药峰弟子"), dict(name="韩小满", gender="男", identity="杂役")]
    cards += [dict(name=f"路人{n}", gender="男", identity=filler) for n in range(8)]
    return dict(characters=cards)


class Client:
    prefix = ""

    def __init__(self):
        self.prompts = []

    def stage(self, _stage, prompt, _schema, **_kwargs):
        self.prompts.append(prompt)
        return {"edits": []}


def repair(client, value):
    outline = dict(number=3, summary="大" * 5000)
    return repair_cast_consistency(
        client, "ch3", TEXT, {"events": []}, False, {}, value, outline,
        lambda *_a, **_k: ({"events": []}, False, None), ask,
    )  # fmt: skip


def test_the_sentences_to_fix_reach_the_model_whole_despite_large_cards_and_outline():
    client = Client()
    repair(client, bible())
    prompt = client.prompts[0]
    assert QUOTE in prompt and "他说着，抬脚踢开挡路的冻柴。" in prompt
    assert prompt.index("## 待修句") < prompt.index("## 固定人物卡")
    assert "路人0" not in prompt  # only the cards of people in the sentences to fix


def test_gendered_address_is_fixed_without_the_model_and_the_pronoun_fix_is_kept():
    value = bible()
    repaired, _, failed, note = repair(Client(), value)
    assert "陆师姐，你不是去药峰取药吗" in repaired and "陆师兄" not in repaired
    assert "她说着，抬脚踢开挡路的冻柴。" in repaired
    assert [row for row in cast_findings(repaired, value, chapter=3) if row["status"] != "Unknown"] == []
    assert failed is False and note["status"] == "repaired"
