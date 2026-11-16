"""Instruction 67: foreshadows by thing not label, no proof-and-confront endings, people-first openings."""

import yaml

from bookengine.endings import final_closure_hits
from bookengine.story_design import SPEC, normalize
from bookengine.story_duties import record_story_duties, with_story_duties, writer_instruction
from test_story_design61 import make_bible, model_design

ENDING_59_STYLE = (
    "众使者的目光渐渐聚过来。\n\n"
    "沈以宁接过裁决印：“旧案由我当众重述。战报、药瓶、药方分别核验，谁有异议，可以当场指出。”\n\n"
    "沈以宁落下第一笔。他将黑铜阵盘压在袖下，没有让任何人看见夹层中的旧称；"
    "至于那个藏在阵盘里的名号，他暂时还不敢说出口。"
)
HUMAN_ENDING = (
    "众使者的目光渐渐聚过来。\n\n"
    "顾千岳把那枚缺角族印推到他手边：“接着吧，记录员。”\n\n"
    "沈以宁没有接印，先握住了她冻得发红的手。“这一次，我替我们自己记一笔。”她笑了，没有抽回手。"
)


def test_withheld_secret_and_bookkeeping_tail_are_flagged_in_the_final_chapter():
    kinds = {hit["kind"] for hit in final_closure_hits(ENDING_59_STYLE)}
    assert {"withheld_secret", "procedural_ending"} <= kinds
    assert final_closure_hits(HUMAN_ENDING) == []


def test_bookkeeping_far_from_the_end_does_not_count_as_the_ending():
    text = "众人清点伤者，逐条核验药方。\n\n" + HUMAN_ENDING
    assert all(hit["kind"] != "procedural_ending" for hit in final_closure_hits(text))


def test_foreshadow_aliases_are_kept_unique_and_close_the_duty(tmp_path):
    bible = make_bible()
    raw = model_design()
    raw["foreshadows"][0]["aliases"] = ["那道刻痕", "双弦纹", "药奴名单", "这道刻痕实在太长了不行", "旧弦痕", "第四个"]
    raw["foreshadows"][1]["aliases"] = ["那道刻痕"]
    design = normalize(raw, bible, 50)
    first, second = design["foreshadows"][0], design["foreshadows"][1]
    assert first["aliases"] == ["那道刻痕", "旧弦痕", "第四个"]
    assert second["aliases"] == []

    bible["story_design"] = design
    outline = with_story_duties(dict(number=2, type="对抗", scenes=[]), bible, tmp_path)
    plant = next(duty for duty in outline["story_duties"] if duty["id"] == "f01-plant")
    assert plant["anchors"] == ["双弦纹", "那道刻痕", "旧弦痕", "第四个"]
    assert "原样" not in plant["text"] and "不要把它的名字刻在" in plant["text"]
    results = record_story_duties(tmp_path, 2, "他摸到断口内侧那道刻痕，指尖发凉。", outline)
    assert {row["id"]: row["status"] for row in results}["f01-plant"] == "done"


def test_writer_gets_people_first_rules_and_the_final_chapter_contract():
    bible = make_bible()
    bible["story_design"] = normalize(model_design(), bible, 50)
    middle = writer_instruction(dict(number=20, scenes=[]), bible)
    final = writer_instruction(dict(number=50, scenes=[], ending=True), bible)
    assert "对质" in middle and "全书末章" not in middle
    assert "全书末章" in final and "藏着不说的新秘密" in final
    appearance = SPEC["duty_text"]["appearance"]
    assert "自报来历" in appearance and "带出他想要的东西" not in appearance


def test_detector_words_never_reach_a_prompt():
    prompts = yaml.safe_dump({k: v for k, v in SPEC.items() if k != "metrics"}, allow_unicode=True)
    assert "凭牒" not in prompts and "造册" not in prompts
