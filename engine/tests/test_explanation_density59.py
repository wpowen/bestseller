"""Instruction 59 B3: chapter-level explanatory density and bounded repair."""

import json

from bookengine.explanation_density import analyze_explanation_density, repair_explanation_density


EVAL57_DENSE = {
    4: (
        "顾立谦盯住北边那道窄痕。救生橇若还能拖动，车上多半载着不能受潮的东西。\n\n"
        "岑烈的话只能证明他知道车队装载什么，不能证明北边是不是废弃救生橇；霜团闻到的气味也只说明方向，不说明车上藏着什么。\n\n"
        "车辙若从北道折转，碎芒会朝东；若仍沿窄痕向西，芒尖便会压在车辙里。他必须决定先查车辙还是逼岑烈说出办法。"
    ),
    11: (
        "他不能同时借感知。若再开冷鼻，这具借来的身法便会分不清哪一步属于自己。\n\n"
        "他原以为薛骨把霜团锁在阵心，是因为霜团能开启骨窟；直到冷鼻贴上黑钉，他才发现阵纹不是朝向薛骨，而是朝向谷底。\n\n"
        "他知道先前判断错了：薛骨要的不是霜团替他开路，而是要借他们破阵。"
    ),
    35: (
        "同一回借力，只能用一项感官。既然两只契兽都被地下阵纹牵着，他不必借两种感官，只需借它们相同的听觉。\n\n"
        "混乱顿时退远，蹄声、车轮声和喊声层层剥开，露出地底空腔。兽群正被数枚契兽阵钉同时驱赶，每一枚都朝封街阵口发力。\n\n"
        "两份证词都说第二次蓄满发生在旧井街时段，却一份称在血字之前，一份称供状是在封蜡后补录；先后关系彼此矛盾。"
    ),
    46: (
        "最底层足印不是供人跪拜，而是五个人分别面向五处岔口。足印之间没有主从标记，只有同样深浅的刻槽。\n\n"
        "暗红不是顾立谦的血，而是从牌心凹槽渗出。细字写着五路各择一人，持引者不得替旁人定路。\n\n"
        "他们方才判断木牌留下的是顾梨归路标记，因此让五人共同守位开门。可木牌在化灰前先把那枚字引到了门内。"
    ),
}

RELATIONSHIP_CONTROL = (
    "顾立谦把引路骨片推到霜团面前。\n\n"
    "“这次你先选。”他说。\n\n"
    "霜团没有碰骨片，只把爪子压在他的手背上。顾立谦松开手，退到它身后半步。"
)

ACTION_CONTROL = (
    "矿车终于被撬离轨道，两根轨闩接连弹起。封住的检修口露出足以让伤者通过的空隙，众人合力将车推成斜坡。"
    "顾砚没有松手，直到最后一名矿工拖着幼兽箱越过出口，才翻身滚开。"
)


def test_eval57_four_reviewed_chapters_are_in_dense_tier_but_relationship_scene_is_not():
    for number, text in EVAL57_DENSE.items():
        result = analyze_explanation_density(text)
        assert result["tier"] == "dense", (number, result)
        assert result["issues"]
    assert analyze_explanation_density(RELATIONSHIP_CONTROL)["tier"] == "normal"


def test_temporal_action_paragraph_is_inference_and_never_a_repair_target():
    result = analyze_explanation_density(ACTION_CONTROL)

    assert result["tier"] == "normal"
    assert result["issues"] == []
    assert result["inferences"][0]["quote"] == ACTION_CONTROL
    assert result["inferences"][0]["reason"] == "temporal_action_not_explanation"


def test_fake_repair_turns_explanation_into_action_and_dialogue_without_losing_terms():
    original = EVAL57_DENSE[35]
    replacement = (
        "顾立谦按住左耳，朝霜团和黑背獾各敲一下铜铃。“都听地下，不借第二种感官。”他喝道。"
        "兽群同时偏头。两份证词摊在雪地上，陆铜生把互相冲突的时辰圈了出来。"
    )

    def ask(client, key, instruction, sections, schema, **kwargs):
        targets = dict(sections)["只可修改的解释段"]
        return {
            "edits": [
                {
                    "id": row["id"],
                    "original": row["original"],
                    "text": replacement if index == 0 else "“够了，先动手。”",
                }
                for index, row in enumerate(targets)
            ]
        }

    changed, raw, failed, note = repair_explanation_density(
        None,
        "r2-ch35",
        original,
        {"state": {}},
        False,
        {},
        {"characters": [{"name": "顾立谦"}, {"name": "陆铜生"}], "world": {"levels": []}},
        {"number": 35},
        lambda *args, **kwargs: ({"state": {}, "events": []}, False, 0),
        ask,
    )

    assert changed != original and raw["events"] == [] and failed is False
    assert note["status"] == "checked" and note["after"]["tier"] == "normal"


def test_checkup_lists_dense_chapters_and_threshold(tmp_path):
    from tools.book_checkup import check_book

    (tmp_path / "story_bible.yaml").write_text("characters: []\n", encoding="utf-8")
    for number, text in ((4, EVAL57_DENSE[4]), (15, RELATIONSHIP_CONTROL)):
        (tmp_path / f"chapter-{number:03d}.body.md").write_text(text, encoding="utf-8")
    (tmp_path / "chapters").mkdir()
    for number in (4, 15):
        (tmp_path / f"chapters/{number:03d}.json").write_text(json.dumps({"number": number}), encoding="utf-8")

    report = check_book(tmp_path, source="draft")["explanation_density"]

    assert report["dense_chapters"] == [4]
    assert report["ratio_threshold"] > 0 and report["minimum_explanation_chars"] > 0
