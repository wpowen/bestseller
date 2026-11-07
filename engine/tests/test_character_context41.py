"""Instruction 41 regressions for the bounded writer character context."""

import json
from pathlib import Path

import yaml

from bookengine.character_context import catchphrase_due
from bookengine.q1_extract import context


FIXTURES = Path(__file__).parent / "fixtures"


def _card(chapter_number):
    bible = {
        "characters": [
            {
                "name": "顾冬岚",
                "role": "protagonist",
                "identity": "被逐出驭兽宗的矿镇少年，雪耳的契主",
                "voice": "紧张时拿冷话遮掩关心",
                "desire": "救出妹妹",
                "fear": "再次失去家人",
                "flaw": "逞强会让他拒绝求援",
                "bottom_line": "不牺牲无辜者换力量",
                "catchphrase": "先救，再算账。",
                "growth_end": "学会接受别人帮助",
            }
        ]
    }
    chapter = {"number": chapter_number, "scenes": [{"characters": ["顾冬岚"]}]}
    sections = dict(context(bible, {}, {"number": 1}, chapter, {}, [], [], ""))
    return json.loads(sections["出场人物档案、口吻卡与当前状态"])["characters"][0]


def test_writer_receives_flaw_and_bottom_line_but_not_growth_end():
    card = _card(2)

    assert card["flaw"] == "逞强会让他拒绝求援"
    assert card["bottom_line"] == "不牺牲无辜者换力量"
    assert "growth_end" not in card


def test_writer_receives_identity_keywords_not_a_copyable_appositive_sentence():
    identity = "被逐出驭兽宗的矿镇少年，雪耳的契主"
    card = _card(2)

    assert "identity" not in card
    assert card["identity_keywords"] == ["矿镇少年", "雪耳契主"]
    assert not any(
        identity[index : index + 8] in json.dumps(card, ensure_ascii=False) for index in range(len(identity) - 7)
    )


def test_catchphrase_is_planned_once_per_five_chapters_with_optional_guidance():
    due = [number for number in range(1, 12) if catchphrase_due("顾冬岚", number)]

    assert len(due) >= 2
    assert all(later - earlier >= 5 for earlier, later in zip(due, due[1:]))
    for number in range(1, 12):
        card = _card(number)
        if number in due:
            assert card["catchphrase"] == "先救，再算账。"
            assert card["catchphrase_guidance"] == "本章可在合适情境用一次，不强求。"
        else:
            assert "catchphrase" not in card
            assert "catchphrase_guidance" not in card


def test_trial50_chapter20_drops_system_context_before_scene_character_desire_and_fear():
    bible = yaml.safe_load((FIXTURES / "address40/trial-50-story_bible.yaml").read_text())
    saved = json.loads((FIXTURES / "character41/trial50-ch20-context.json").read_text())
    dropped = []

    sections = dict(
        context(
            bible,
            {},
            {"number": 2},
            saved["chapter"],
            saved["state"],
            [],
            [],
            "",
            dropped,
        )
    )
    raw = sections["出场人物档案、口吻卡与当前状态"]
    cards = {card["name"]: card for card in json.loads(raw)["characters"]}

    assert len(raw) <= 1500
    assert cards["顾冬岚"]["desire"] == next(c for c in bible["characters"] if c["name"] == "顾冬岚")["desire"]
    assert cards["顾冬岚"]["fear"] == next(c for c in bible["characters"] if c["name"] == "顾冬岚")["fear"]
    assert all("voice" in cards[name] and "current" in cards[name] for name in ("顾冬岚", "沈夏珩", "陆岳霖"))
    assert any(field.startswith("顾冬岚.") and field.endswith(":compressed") for field in dropped)
