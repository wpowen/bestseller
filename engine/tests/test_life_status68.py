"""Instruction 68 (E1): quotes are from 《散修丹途：逆命登顶》, where 顾夏珩 (planned exit 40) died in chapter 31."""

from bookengine.continuity_ledger import continuity_findings, merge_continuity_delta
from bookengine.life_status import (
    dead_person_acts,
    must_survive,
    premature_death_hits,
    repair_premature_death,
    writer_survival_instruction,
)

BIBLE = {
    "characters": [{"name": "秦野", "role": "protagonist"}, {"name": "顾夏珩"}, {"name": "苏以宁"}],
    "antagonists": [{"name": "沈冬岚", "exit": 10}, {"name": "顾夏珩", "exit": 40}],
}
CH31 = "秦野的火线贴着刀背滑了进去。顾夏珩喉间血线绽开，尸体倒进火里。\n\n苏以宁收剑：“顾夏珩死了，护山阵还没封山，我们进去。”"


def test_writer_is_told_who_must_survive_and_until_when():
    assert must_survive(BIBLE, 31) == {"顾夏珩": 40}
    assert "顾夏珩（第40章才退场）" in writer_survival_instruction(BIBLE, 31)
    assert writer_survival_instruction(BIBLE, 40) == ""


def test_early_death_is_found_and_the_planned_exit_chapter_is_not():
    hits = premature_death_hits(CH31, BIBLE, 31)
    assert [row["text"] for row in hits] == [
        "顾夏珩喉间血线绽开，尸体倒进火里。",
        "苏以宁收剑：“顾夏珩死了，护山阵还没封山，我们进去。”",
    ]
    assert premature_death_hits(CH31, BIBLE, 40) == []


def test_sealing_threats_and_other_peoples_deaths_are_not_deaths():
    for sentence in (
        "沈展尧没有抬头，“顾夏珩把外门封死了。”",
        "顾夏珩被火光逼退三步，袖口当场烧成焦黑。",
        "顾夏珩的投影缓缓展开：“你让每个人自己选，只会让他们在混乱里死去。”",
        "顾夏珩若是死了，护山阵便无人能开。",
    ):
        assert premature_death_hits(sentence, BIBLE, 31) == [], sentence


def test_ledger_quote_marks_a_death_the_surface_words_miss():
    text = "顾夏珩倒在禁火台上，焦黑的手再也握不住任何一缕火。"
    delta = {"characters": [{"name": "顾夏珩", "field": "life_status", "value": "dead", "quote": text}]}
    assert premature_death_hits(text, BIBLE, 31) == []
    assert premature_death_hits(text, BIBLE, 31, delta)[0]["source"] == "ledger_quote"


class FakeClient:
    def __init__(self, answers):
        self.answers, self.names = list(answers), []


def fake_ask(client, name, prompt, sections, schema=None, **kwargs):
    client.names.append(name)
    return client.answers.pop(0)


def fake_extract(client, name, text, state, bible, outline, rows, final=False):
    return {"state": {}, "continuity_delta": {}}, False, 0


def _repair(answers):
    client = FakeClient(answers)
    raw = {"state": {}, "continuity_delta": {}}
    result = repair_premature_death(
        client, "r2-ch31", CH31, raw, False, {}, BIBLE, {"number": 31}, fake_extract, fake_ask
    )
    return result, client


def test_death_rewritten_into_a_setback_is_adopted():
    edits = [
        {"id": 2, "text": "顾夏珩喉间血线绽开，被亲卫拖进了火雾深处。"},
        {"id": 3, "text": "苏以宁收剑：“顾夏珩退了，护山阵还没封山，我们进去。”"},
    ]
    (text, _, failed, note), client = _repair([{"edits": edits}])
    assert note["status"] == "repaired" and not failed
    assert "尸体" not in text and "被亲卫拖进了火雾深处" in text
    assert client.names == ["r2-ch31-premature-death-repair"]


def test_rewrite_that_still_kills_is_rolled_back():
    (text, _, _, note), _ = _repair([{"edits": [{"id": 2, "text": "顾夏珩喉间血线绽开，当场毙命。"}]}])
    assert text == CH31
    assert note["status"] == "rolled_back" and note["reason"] == "death_remains"


def test_first_death_is_sticky_and_death_recorded_as_travel_status_counts():
    quote = "秦野抬脚，先踏过顾夏珩的尸体，随后踏入山门火雾。"
    row = {"name": "顾夏珩", "field": "location", "value": "禁火山门", "travel_status": "死亡", "quote": quote}
    state = merge_continuity_delta({}, {"characters": [row]}, quote, BIBLE, 31)
    later = "顾夏珩当场被焚成一团焦灰。"
    again = {"name": "顾夏珩", "field": "life_status", "value": "dead", "quote": later}
    state = merge_continuity_delta(state, {"characters": [again]}, later, BIBLE, 35)
    life = state["continuity_ledger"]["characters"]["顾夏珩"]["life_status"]
    assert (life["value"], life["chapter"]) == ("dead", 31)


def test_dead_character_acting_or_heard_is_found_but_being_talked_about_is_not():
    for sentence in (
        "正说着，外廊尽头传来顾夏珩的笑声。",
        "顾夏珩见状厉喝：“封火！”",
        "顾夏珩猛地挣扎，秦野扣住他的腕骨。",
    ):
        assert dead_person_acts("顾夏珩", sentence), sentence
    for sentence in (
        "苏以宁低声道：“顾夏珩拿我兄长的封魂火做了阵眼。”",
        "秦野想起顾夏珩说过的话。",
        "他踏过顾夏珩的尸体。",
    ):
        assert not dead_person_acts("顾夏珩", sentence), sentence
    state = {
        "continuity_ledger": {
            "version": 1,
            "characters": {"顾夏珩": {"life_status": {"value": "dead", "chapter": 31, "quote": "x"}}},
            "consumed_memories": [],
        }
    }
    found = continuity_findings("正说着，外廊尽头传来顾夏珩的笑声。", state, BIBLE, 33)
    assert [row["kind"] for row in found] == ["life_status"]
