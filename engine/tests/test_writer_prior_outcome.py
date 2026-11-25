"""Adjacent consequences must reach the bounded production writer without copyable evidence."""

import json
from types import SimpleNamespace

from bookengine.q1_draft_prompt import production_prompt
from bookengine.q1_extract import ask
from bookengine.writer_context import previous_outcome_facts


def test_previous_outcome_leaves_empty_fields_empty_and_excludes_prior_volumes_and_other_chapters():
    volume = {"number": 2, "end": 20, "summary": "整卷旧结果"}
    old = {"number": 22, "summary": "旧摘要", "gains": [{"chapter": 22, "content": "取得血布"}]}
    new = {"number": 23, "summary": "证人脱身", "gains": [], "events": [{"quote": "证据原段"}]}
    value = previous_outcome_facts({}, [volume], [old, new], 24, "")
    assert "证人脱身" in value
    assert all(text not in value for text in ("整卷旧结果", "旧摘要", "取得血布", "证据原段"))
    absent = previous_outcome_facts({}, [volume], [old], 24, "")
    assert all(text not in absent for text in ("整卷旧结果", "旧摘要", "取得血布"))


def test_production_writer_gets_confirmed_result_before_context_is_cut_and_never_reprints_source(tmp_path):
    earlier = "他把带着蜡封的纸卷递到受伤的证人手里，转身把门关上。"
    (tmp_path / "chapter-002.body.md").write_text(earlier, encoding="utf-8")
    outlines = tmp_path / "writing_outlines"
    outlines.mkdir()
    (outlines / "002.json").write_text(
        json.dumps(
            dict(
                ending_type="迫近危险",
                ending_content="旧规划仍让人抢纸卷",
                scenes=[{"action": "原定证人再次被围"}],
            )
        )
    )
    rows = [
        {"number": 1, "summary": "十年前的旧审讯"},
        dict(
            number=2,
            status="complete",
            fact_source="final_body",
            summary=earlier,  # Extraction can fall back to copying the beginning of the chapter.
            gains=[{"chapter": 2, "content": "证人已获救并拿到纸卷"}, {"chapter": 1, "content": "旧物不在上一章"}],
            payoff={"type": "收获", "quote": earlier},
        ),
    ]
    (tmp_path / "chapters").mkdir()
    (tmp_path / "chapters/002.json").write_text(json.dumps(rows[-1]))
    instruction, sections = production_prompt(
        SimpleNamespace(words_per_chapter=2300, style_reference=None),
        {},
        {},
        {"number": 1},
        {"number": 3},
        {},
        [],
        rows,
        earlier,
        [],
        tmp_path,
        "b",
        {"b": ""},
    )
    client = SimpleNamespace(prefix="", stage=lambda stage, prompt, *args, **kwargs: prompt)
    body_header = "## 上章结尾\n"
    request = ask(client, "r2-ch3-draft", instruction, sections, input_limit=10000)
    assert "证人已获救并拿到纸卷" in request.split(body_header, 1)[1].split("\n\n## ", 1)[0]
    assert request.index(body_header) < request.index("## 本章章纲")
    assert all(
        text not in request
        for text in (earlier, "旧物不在上一章", "十年前的旧审讯", "旧规划仍让人抢纸卷", "原定证人再次被围")
    )


def test_saved_boundary_adds_only_recent_supported_holders_and_new_open_threads():
    at_house = {"owner": "来喜", "location": "王家桥"}
    objects = {name: at_house for name in ("油壶", "门票", "提篮", "铁钉", "鱼钩")}
    objects["油壶"] = {"owner": "阿亮", "location": "王家桥"}
    snapshot = dict(
        end_location="王家桥",
        present=["阿亮", "来喜"],
        objects=objects,
        foreshadows={"夜渡的六声钟": "open", "旧猜测": "closed", "上卷谜团": "open"},
    )
    rows = [
        dict(number=3, fact_source="final_body", state={"foreshadows": {"上卷谜团": "open"}}),
        dict(number=4, fact_source="final_body", state=snapshot),
        dict(number=5, fact_source="final_body", state={"end_location": "后章渡口"}, summary="未来计划成真"),
    ]
    passage = "夜渡的六声钟响罢，阿亮喊了他一声。\n\n来喜握着门票。阿亮将油壶挂在腰间。来喜看着铁钉。来喜没拿着鱼钩。阿亮提着提篮。"
    outcome = json.loads(previous_outcome_facts(rows[-1]["state"], [], rows, 5, passage))
    at_end = outcome["收场时"]
    assert at_end["end_location"] == "王家桥"
    assert at_end["持物"] == {"油壶": "阿亮", "门票": "来喜"}
    assert at_end["新增未解线索"] == ["夜渡的六声钟"]
    assert "未来计划成真" not in str(outcome)


def test_closing_details_budget_drops_whole_items_and_traces_the_omissions():
    names = ["铜印", "铁尺", "团扇", "木契", "纸人"]
    snapshot = dict(
        end_location="厨房",
        present=["阿亮"],
        objects={name: {"owner": "阿亮", "location": "厨房"} for name in names},
        foreshadows={name: "open" for name in names},
    )
    drops = []
    body = "。".join(f"阿亮拿着{name}" for name in names)
    data = previous_outcome_facts({}, [], [dict(number=1, fact_source="final_body", state=snapshot)], 2, body, drops)
    facts = json.loads(data)["收场时"]
    assert list(facts["持物"]) == names[:0:-1]
    assert facts["新增未解线索"] == names[:4]
    assert drops == ["上章结尾.持物:omitted=1", "上章结尾.新增未解线索:omitted=1"]
