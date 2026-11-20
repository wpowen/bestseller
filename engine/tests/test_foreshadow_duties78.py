"""Instruction 78: a reminded foreshadow does something in the chapter, and chapters stop piling up new devices.

In 《扫雪剑起》 (books/webui/24ddbcc8-…) 19 of 42 duties told the writer the thing should appear again and
“仍不解释”. The writer obeyed by having the object glimpsed and dropped, while each chapter added more named
boxes, seals and arrays (chapter 12: 封剑箱、牵丝、假封山令、阵眼、断阶剑纹、感应纹、旧轮值图……).
"""

from bookengine.story_design import normalize
from bookengine.story_duties import all_duties, planner_sections, writer_instruction
from test_story_design61 import make_bible, model_design


def _design_and_bible():
    bible = make_bible()
    design = normalize(model_design(), bible, 50)
    return design, dict(bible, story_design=design)


def test_reminders_ask_the_object_to_act_not_to_stay_unexplained():
    design, bible = _design_and_bible()
    duties = all_duties(design, bible)
    reminders = [d for d in duties if d["kind"] == "remind"]
    plants = [d for d in duties if d["kind"] == "plant"]
    assert reminders and plants
    for duty in reminders:
        assert "不解释" not in duty["text"]
        assert "起一次作用" in duty["text"]
        assert "不说破" in duty["text"]
    for duty in plants:
        assert "不解释" not in duty["text"]


def test_writer_and_planner_cap_new_named_devices():
    design, bible = _design_and_bible()
    outline = dict(number=12, scenes=[], story_duties=[])
    assert "最多两样" in writer_instruction(outline, bible)
    sections = planner_sections(bible, None, 11, 15, 2)
    assert any("最多两样" in str(heading) + str(body) for heading, body in sections)
    assert planner_sections(dict(bible, story_design={}), None, 11, 15, 2) == []
