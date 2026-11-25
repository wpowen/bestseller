from copy import deepcopy
from types import SimpleNamespace

import pytest

from bookengine.relationship_revisit import GUIDANCE, relationship_revisit
from bookengine.storage import save
from tools.fiction_trial_inputs import prepare_candidate


def encounter(tmp_path):
    anchor = "陶黎指着纸背道：“仓门上只有蓝封条。”"
    question, answer = "“那道印是谁盖的？”", "“没看清。”"
    event = "林岑把交班的时辰告诉陶黎。"
    body = "\n\n".join(("林岑放下纸包。", anchor, question, answer, event))
    save(tmp_path / "chapter-002.body.md", body)
    pair = ["陶黎", "林岑"]
    old = dict(chapter=2, summary="林岑把交班时辰告诉陶黎", quote=event)
    state = dict(characters={n: {} for n in [*pair, "韩韫"]}, continuity_ledger=dict(critical_events=[old]))
    rows = [
        dict(number=2, status="complete", fact_source="final_body", summary="陶黎和林岑核对了蓝封条，尚未查出盖印的人")
    ]
    scene = dict(
        characters=pair,
        choice="带证人核印",
        goal="决定是否公开新收条",
        obstacle="新收条上多了掌柜的指印",
        action="把两张收条并在灯下",
        result="掌柜自愿押下印簿",
    )
    outline = dict(
        number=4,
        type="情感",
        opening_conflict="两人发现仓房的蓝封条被换过。",
        relationship_change=dict(
            people=pair, cause="旧封条再次被换", before="两人都知道蓝封条的样式", after="愿意出面作证"
        ),
        scenes=[scene],
    )
    return outline, state, rows, (anchor, question, answer, event)


def test_partial_answer_and_genuine_new_evidence(tmp_path):
    outline, state, rows, (anchor, question, answer, _) = encounter(tmp_path)
    before = deepcopy((outline, state, rows))
    focus = relationship_revisit(outline, state, rows, tmp_path)
    assert focus["prior_exchange"] == [anchor, question, answer]
    assert focus["chapter"] == 2 and focus["scene"]["obstacle"] == "新收条上多了掌柜的指印"
    assert "仍然悬着" in GUIDANCE and "可以再问" in GUIDANCE
    assert before == (outline, state, rows)
    assert relationship_revisit(outline, state, [], tmp_path) is None
    other = deepcopy(outline)
    other.update(opening_conflict="小孩病得更重了")
    other["relationship_change"]["before"] = "她从未信过病历"
    assert relationship_revisit(other, state, rows, tmp_path) is None


@pytest.mark.parametrize(
    "break_text",
    [
        "韩韫走进来问：“还有谁要作证？”",
        "次日清晨，两人已经回到城外。",
        "天色暗了。灯灭了。街上的客人也都散尽。",
        "众人散去。\n\n鼓漏数声。",
    ],
)
def test_recall_does_not_attach_dialogue_across_an_interruption(tmp_path, break_text):
    outline, state, rows, (anchor, question, answer, event) = encounter(tmp_path)
    save(tmp_path / "chapter-002.body.md", "\n\n".join(("林岑放下纸包。", anchor, break_text, question, answer, event)))
    assert relationship_revisit(outline, state, rows, tmp_path)["prior_exchange"] == [anchor]


def test_requires_matching_completed_text_and_grounded_event(tmp_path):
    outline, state, rows, _ = encounter(tmp_path)
    rows[0]["fact_source"] = "plan"
    assert relationship_revisit(outline, state, rows, tmp_path) is None
    rows[0]["fact_source"] = "final_body"
    for item in (
        dict(chapter=[], summary="", quote="不存在"),
        dict(chapter=2, summary=[], quote="印"),
        dict(chapter=2, summary="陶黎告诉林岑", quote=["印"]),
        dict(chapter=2, summary="陶黎告诉林岑", quote="错误的引句"),
    ):
        state["continuity_ledger"]["critical_events"] = [item]
        assert relationship_revisit(outline, state, rows, tmp_path) is None
    state["continuity_ledger"]["critical_events"] = 3
    assert relationship_revisit(outline, state, rows, tmp_path) is None


def test_writer_brief_keeps_the_new_evidence_and_previously_unanswered_question(tmp_path):
    outline, state, rows, _ = encounter(tmp_path)
    card = dict(title="纸背", premise="印簿失窃", golden_finger_rule="无", opening="林岑带来旧物")
    bible = dict(selected_card=card, characters=[dict(name="陶黎", role="protagonist"), dict(name="林岑")])
    book = SimpleNamespace(chapters=50, words_per_chapter=2200)
    old, _, _ = prepare_candidate(book, bible, dict(outline, title="质证"), state, rows, "")
    brief, message, sections = prepare_candidate(
        book, bible, dict(outline, title="质证"), state, rows, "", out=tmp_path
    )
    assert old["local_brief"]["眼下的冲突"] == outline["opening_conflict"]
    assert next(iter(brief["local_brief"].values())) == "决定是否公开新收条"
    assert "掌柜的指印" in sections[0][1]
    assert "没看清" in sections[1][1] and "可以再问" in message
