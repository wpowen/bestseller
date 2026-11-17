"""Instruction 68 (E2): 《散修丹途》 chapter 48 “苏元铮”; 《武馆新客》 chapter 3 “苏子翎”."""

from bookengine.name_slips import fix_name_slips, name_slip_checkup, name_slip_findings

BIBLE = {
    "characters": [{"name": "秦野"}, {"name": "苏以宁"}, {"name": "石元铮"}, {"name": "顾以宁", "aliases": ["阿宁"]}],
    "antagonists": [{"name": "顾夏珩"}],
}
SLIP = "石元铮收起炉锤。苏元铮在后面咂了咂嘴：“这赌注够重。”苏以宁没有接话。"


def test_spliced_name_is_found_with_both_sources():
    (row,) = name_slip_findings(SLIP, BIBLE, 48)
    assert (row["kind"], row["name"], row["candidates"]) == ("blended_name", "苏元铮", ["苏以宁", "石元铮"])
    assert row["quote"].startswith("苏元铮在后面咂了咂嘴")


def test_slip_is_replaced_by_the_owner_of_the_given_name():
    text, note = fix_name_slips(SLIP, BIBLE, 48)
    assert "苏元铮" not in text and text.count("石元铮") == 2
    assert note["status"] == "repaired" and note["remaining"] == []


def test_registered_names_sharing_a_given_name_are_not_slips():
    clean = "苏以宁看向顾以宁，秦野和石元铮已经先走了。顾夏珩在高台上冷笑。"
    assert name_slip_findings(clean, BIBLE) == []
    assert fix_name_slips(clean, BIBLE)[0] == clean


def test_ordinary_unregistered_walk_on_is_not_a_slip():
    assert name_slip_findings("店里的伙计赵大有端上茶来。赵大有说：“客官慢用。”", BIBLE) == []


def test_one_character_off_a_registered_name_is_replaced():
    text, note = fix_name_slips("顾夏衍说：“封火。”秦野没有回头。", BIBLE)
    assert note["findings"][0]["kind"] == "near_name" and "顾夏珩说" in text


def test_checkup_counts_slips_per_chapter():
    report = name_slip_checkup(BIBLE, [{"number": 48, "text": SLIP}, {"number": 49, "text": "秦野走了。"}])
    assert report["count"] == 1 and report["issues"][0]["chapter"] == 48
