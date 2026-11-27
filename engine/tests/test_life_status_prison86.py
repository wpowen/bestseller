"""A quote that shows someone locked up is not evidence of death (100-chapter run, chapter 89 -> 93 stop)."""

from bookengine.continuity_ledger import merge_continuity_delta

BIBLE = {"characters": [{"name": "祁无咎", "role": "antagonist"}, {"name": "陆厌", "role": "protagonist"}]}
PRISON = "石牢铁门落下。失去祭力支撑的石壁变得灰暗，再无血纹流转。祁无咎被锁在里面，四肢受主契束缚，连抬手都做不到。"
TEXT = PRISON + "\n“带三房的人守石牢。名单上的人逐个拿下，别给祁无咎传讯的机会。”"


def life(state):
    return state["continuity_ledger"]["characters"]["祁无咎"].get("life_status")


def test_prison_quote_does_not_record_death():
    row = {"name": "祁无咎", "field": "life_status", "value": "dead", "quote": PRISON}
    assert life(merge_continuity_delta({}, {"characters": [row]}, TEXT, BIBLE, 89)) is None


def test_prison_quote_with_dead_travel_status_does_not_record_death():
    row = {"name": "祁无咎", "field": "location", "value": "石牢", "travel_status": "死亡", "quote": PRISON}
    state = merge_continuity_delta({}, {"characters": [row]}, TEXT, BIBLE, 89)
    assert life(state) is None
    assert state["continuity_ledger"]["characters"]["祁无咎"]["location"]["travel_status"] == "未知"


def test_real_death_quote_still_records_death():
    text = "祁无咎当场气绝，倒在石牢门口。"
    row = {"name": "祁无咎", "field": "life_status", "value": "dead", "quote": text}
    assert life(merge_continuity_delta({}, {"characters": [row]}, text, BIBLE, 89))["value"] == "dead"


def test_prison_quote_is_not_a_premature_death():
    from bookengine.life_status import premature_death_hits

    bible = {**BIBLE, "antagonists": [{"name": "祁无咎", "exit": 98}]}
    delta = {"characters": [{"name": "祁无咎", "field": "life_status", "value": "dead", "quote": PRISON}]}
    assert premature_death_hits(TEXT, bible, 89, delta) == []


def test_euphemistic_death_without_captivity_still_records_death():
    text = "顾夏珩倒在禁火台上，焦黑的手再也握不住任何一缕火。"
    bible = {"characters": [{"name": "顾夏珩"}]}
    row = {"name": "顾夏珩", "field": "life_status", "value": "dead", "quote": text}
    state = merge_continuity_delta({}, {"characters": [row]}, text, bible, 31)
    assert state["continuity_ledger"]["characters"]["顾夏珩"]["life_status"]["value"] == "dead"


def _saved_book(tmp_path):
    import json

    person = {
        "life_status": {"value": "dead", "chapter": 89, "quote": PRISON},
        "location": {"value": "石牢", "chapter": 89, "quote": PRISON, "travel_status": "死亡"},
    }
    state = {"continuity_ledger": {"characters": {"祁无咎": person}}}
    (tmp_path / "chapters").mkdir()
    row = {"number": 89, "status": "complete", "fact_source": "final_body", "state": state}
    (tmp_path / "chapters/089.json").write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "chapter-089.body.md").write_text(TEXT, encoding="utf-8")
    return state


def test_saved_captive_death_is_not_a_confirmed_death(tmp_path):
    from bookengine.life_planning_facts import confirmed_deaths

    state = _saved_book(tmp_path)
    assert confirmed_deaths(tmp_path, state, chapter=93) == {}


def test_saved_captive_death_reads_as_alive_on_the_next_chapter():
    state = {"continuity_ledger": {"characters": {"祁无咎": {"life_status": {"value": "dead", "quote": PRISON}}}}}
    person = merge_continuity_delta(state, {}, "", BIBLE, 93)["continuity_ledger"]["characters"]["祁无咎"]
    assert "life_status" not in person
