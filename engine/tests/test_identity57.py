"""Instruction 57: identity prose is a check contract, never copyable writer text."""

import json

from bookengine.cast_consistency import cast_findings, repair_cast_consistency
from bookengine.identity import writer_identity_bible, writer_identity_cards, writer_identity_outline


def bible():
    return {
        "characters": [
            {"name": "顾立谦", "role": "protagonist", "identity": "被逐出驭兽宗的矿镇少年", "gender": "男"},
            {"name": "岑烈", "role": "antagonist", "identity": "暴雪矿镇雪匪头目", "gender": "男"},
            {"name": "白苓", "role": "support", "identity": "游走雪地的女药师", "gender": "女"},
        ]
    }


def test_writer_views_remove_every_copyable_identity_literal_without_mutating_sources():
    value = bible()
    outline = {
        "number": 1,
        "introductions": [{"name": "顾立谦", "origin": "被逐出驭兽宗的矿镇少年"}],
        "scenes": [{"characters": ["顾立谦"], "choice": "被逐出驭兽宗的矿镇少年决定救人"}],
    }
    before = json.dumps([value, outline], ensure_ascii=False)

    views = [writer_identity_bible(value), writer_identity_cards(value), writer_identity_outline(outline, value)]
    rendered = json.dumps(views, ensure_ascii=False)

    for card in value["characters"]:
        identity = card["identity"]
        assert not any(identity[index : index + 8] in rendered for index in range(len(identity) - 7))
    assert json.dumps([value, outline], ensure_ascii=False) == before


def test_eval57_identity_literals_are_repair_targets_and_natural_rewrites_are_adopted():
    value = bible()
    text = (
        "顾立谦是被逐出驭兽宗的矿镇少年，他用肩顶住木梁。"
        "暴雪矿镇雪匪头目岑烈的人正驱赶雪兽。"
        "白苓来了，她是游走雪地的女药师，衣角还沾着药汁。"
    )
    replacements = {
        1: "顾立谦用肩顶住木梁，旧宗门烙印从破袖下露出一角。",
        2: "岑烈的人正驱赶雪兽，黑牙令在车侧撞得发响。",
        3: "白苓踩着积雪赶来，衣角还沾着刚煎过药的汁水。",
    }

    def ask(client, stage, instruction, sections, schema, **kwargs):
        cards = json.loads(dict(sections)["固定人物卡"])
        assert all("identity" not in card and card.get("identity_keywords") for card in cards)
        targets = json.loads(dict(sections)["待修句"])
        assert {row["kind"] for row in targets} == {"identity_literal"}
        return {"edits": [{"id": row["id"], "text": replacements[row["id"]]} for row in targets]}

    changed, _, failed, note = repair_cast_consistency(
        None,
        "ch1",
        text,
        {"events": []},
        False,
        {},
        value,
        {"number": 1, "introductions": [{"name": "顾立谦", "origin": value["characters"][0]["identity"]}]},
        lambda *args, **kwargs: ({"events": []}, False, 0),
        ask,
    )

    assert note["status"] == "repaired" and failed is False
    assert not [row for row in cast_findings(changed, value, chapter=1) if row["kind"] == "identity_literal"]


def test_partial_identity_repair_rolls_back_with_remaining_findings_recorded():
    value = bible()
    text = "顾立谦是被逐出驭兽宗的矿镇少年。暴雪矿镇雪匪头目岑烈的人正驱赶雪兽。"

    def ask(client, stage, instruction, sections, schema, **kwargs):
        targets = json.loads(dict(sections)["待修句"])
        first = targets[0]
        return {"edits": [{"id": first["id"], "text": "顾立谦用旧宗门留下的身法避开落石。"}]}

    changed, raw, failed, note = repair_cast_consistency(
        None,
        "ch1",
        text,
        {"events": []},
        False,
        {},
        value,
        {"number": 1},
        lambda *args, **kwargs: ({"events": []}, False, 0),
        ask,
    )

    assert (changed, raw, failed) == (text, {"events": []}, False)
    assert note["status"] == "rolled_back" and any(row["kind"] == "identity_literal" for row in note["after"])
