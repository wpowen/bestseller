"""Instruction 61: the designed story layers survive, get scheduled, reach the writer and are tracked."""

import json

import yaml

from bookengine.design_gate import design_issues
from bookengine.selected_card import differences, enforce
from bookengine.story_design import design_gaps, ensure_story_design, load_story_design, normalize
from bookengine.story_duties import (
    all_duties,
    due_duties,
    load_ledger,
    planner_sections,
    record_story_duties,
    with_story_duties,
    writer_instruction,
    writer_sections,
)
from bookengine.story_report import story_report
from bookengine.structure_planning import reschedule_threads

KINDS = ("main", "dark", "hidden", "romance", "growth", "support")


def make_bible(total=50, stages=5):
    size = total // stages
    names = ["顾长风", "沈展尧", "陆希砚", "裴晏竹", "韩朔言", "崔林霏", "薛欣妍", "裴沐弦"]
    villains = names[3 : 3 + stages]
    cards = [
        dict(name="顾长风", role="protagonist", voice="短促", desire="夺回自己的手", growth_end="敢自己选路"),
        dict(name="沈展尧", role="romance", voice="清冷", desire="查清旧案", exit_volume=stages),
        dict(name="陆希砚", role="support", voice="圆滑", desire="保住器坊", exit_volume=min(2, stages)),
    ] + [
        dict(name=name, role="antagonist", voice="冷", desire="掌权", ladder_volume=i)
        for i, name in enumerate(villains, 1)
    ]
    return dict(
        story_layer_version=1,
        title="木偶师",
        premise="外门弟子被首席操控多年。",
        characters=cards,
        power=dict(object="木手", rule="断一线接一线", cost="右腕旧伤裂开", limit="一次只能接一条"),
        world=dict(
            setting="演武场",
            rules=["线不能离手三丈"],
            factions=[dict(name=f"势力{i}", interest=f"利益{i}") for i in range(3)],
            truth_layers=[
                dict(layer=1, truth="首席也是木偶", reveal_not_before=8),
                dict(layer=2, truth="禁库里关着历代首席", reveal_not_before=25),
                dict(layer=3, truth="祖师像才是操线人", reveal_not_before=41),
            ],
        ),
        main_foreshadows=[
            dict(id="f01", description="木手断口的双弦纹", close_volume=min(3, stages)),
            dict(id="f02", description="药奴名单上的妹妹", close_volume=min(2, stages)),
            dict(id="f03", description="被借走一夜的执法令", close_volume=stages),
        ],
        antagonists=[
            dict(name=name, tier=i, stage=i, exit=i * size if i < stages else total - 1)
            for i, name in enumerate(villains, 1)
        ],
        volumes=[
            dict(number=i, start=(i - 1) * size + 1, end=i * size if i < stages else total, antagonist=villains[i - 1])
            for i in range(1, stages + 1)
        ],
    )


def make_threads(stages=5):
    return dict(
        threads=[
            dict(
                id=kind,
                kind=kind,
                beats=[
                    dict(id=f"{kind}-v{n}-1", volume=n, description=f"{kind}线第{n}阶段的具体事件", reveal_not_before=0)
                    for n in range(1, stages + 1)
                ],
            )
            for kind in KINDS
        ]
    )


def model_design():
    return dict(
        characters=[
            dict(
                name="顾长风",
                wound="十二岁那年冬天在演武场被首席接上第一根线",
                cares_for="器坊里教他削木的陆希砚",
                contrast="看着顺从，其实记仇",
                habits=["说话前先按右腕", "数别人的脚步", "不直视首席"],
            ),
            dict(
                name="沈展尧",
                wound="师兄在她值夜那晚失踪",
                cares_for="师兄留下的短剑",
                contrast="嘴上只认证据，心里早信了",
                habits=["问话只问一遍", "剑不离左手", "生气时声音更轻"],
            ),
        ],
        foreshadows=[
            dict(
                id="f01",
                anchor="双弦纹",
                surface="木手上的旧刻痕",
                truth="祖师留下的反接口",
                plant=2,
                reminders=[9, 17],
                payoff=27,
            ),
            dict(
                id="f02",
                anchor="药奴名单",
                surface="一张废纸",
                truth="妹妹还活着的凭据",
                plant=3,
                reminders=[7, 12],
                payoff=19,
            ),
            dict(
                id="f03",
                anchor="这个锚点词实在太长了不合格",
                surface="s",
                truth="t",
                plant=99,
                reminders=[1, 2],
                payoff=-4,
            ),
        ],
        truth_reveals=[
            dict(layer=1, chapter=2, overturns="首席是唯一的操线人", anchor="白玉串珠"),
            dict(layer=2, chapter=29, overturns="禁库只存器物", anchor="首席名册"),
            dict(layer=3, chapter=48, overturns="祖师早已死去", anchor="祖师像"),
        ],
        arc=[
            dict(kind="flaw_cost", chapter=44, event="他独自潜入，陆希砚的器坊因此被封"),
            dict(kind="fear_faced", chapter=45, event="他主动把线接回自己身上"),
            dict(kind="growth_end", chapter=50, event="他把木牌交给沈展尧保管"),
        ],
        relationships=[
            dict(
                people=["顾长风", "沈展尧"],
                steps=[
                    dict(stage=1, event="她替他包扎右腕", before="互相提防", after="肯背对对方"),
                    dict(stage=2, event="他把木手交给她保管一夜", before="肯背对对方", after="敢说真话"),
                    dict(stage=2, event="重复阶段应丢弃", before="a", after="b"),
                    dict(stage=3, event="没有变化应丢弃", before="同", after="同"),
                ],
            ),
            dict(people=["顾长风", "不存在的人"], steps=[dict(stage=1, event="e", before="a", after="b")]),
        ],
        appearances=[dict(name="陆希砚", first_chapter=40), dict(name="韩朔言", first_chapter=1)],
        stage_progress=[dict(stage=1, goal_line="斩断第一条线", marker="五条线还剩几条")],
    )


def test_identity_keyword_miss_no_longer_wipes_the_designed_story():
    bible, threads = make_bible(), make_threads()
    card = dict(
        title="木偶师", premise="外门弟子被首席操控多年。", golden_finger_rule="断一线接一线", engine="进度条：4/50"
    )
    locked = dict(
        name="顾长风",
        identity="外门弟子",
        setting="演武场",
        object="木手",
        keywords=dict(identity=["外门弟子", "木偶术"], setting=["演武场"], object=["木手"], rule=["断一线接一线"]),
    )
    bible["summary"] = "模型写的五百字梗概"
    assert differences(bible | dict(title="木偶师"), card, locked) == ["identity"]

    fixed, kept = enforce(bible, threads, card, locked, ["identity", "object"])

    assert differences(fixed, card, locked) == []
    assert len(fixed["world"]["truth_layers"]) == 3 and len(fixed["world"]["factions"]) == 3
    assert fixed["power"]["cost"] == "右腕旧伤裂开"
    hero = fixed["characters"][0]
    assert hero["desire"] == "夺回自己的手"
    assert "模型写的五百字梗概" in fixed["summary"]
    descriptions = [b["description"] for line in kept["threads"] for b in line["beats"]]
    assert len(set(descriptions)) == len(descriptions) and "进度条：4/50" not in descriptions


def test_structure_repair_cannot_replace_designed_beats():
    threads = make_threads()
    invented = [dict(thread_id=1, promise="顾长风必须夺回木手", payoff_chapters=[5, 9])]
    assert reschedule_threads(threads, invented) == threads
    assert reschedule_threads(threads, None) == threads

    moved = reschedule_threads(
        threads,
        dict(
            threads=[
                dict(id="hidden", beats=[dict(id="hidden-v1-1", volume=2, reveal_not_before=41, description="改写")])
            ]
        ),
    )
    beat = next(b for line in moved["threads"] for b in line["beats"] if b["id"] == "hidden-v1-1")
    assert (beat["volume"], beat["reveal_not_before"]) == (2, 41)
    assert beat["description"] == "hidden线第1阶段的具体事件"
    assert threads["threads"][2]["beats"][0]["volume"] == 1


def test_design_gate_names_each_hollow_layer():
    assert design_issues(make_bible(), make_threads()) == []

    hollow = make_bible()
    hollow["world"] = dict(rules=[hollow["premise"]])
    hollow["power"] = dict(object="木手", rule="断一线接一线")
    hollow["main_foreshadows"] = []
    hollow["characters"][0]["desire"] = hollow["premise"]
    copied = make_threads()
    for line in copied["threads"]:
        for beat in line["beats"]:
            beat["description"] = "进度条：4/50"
    copied["threads"][1]["beats"] = [dict(id="dark-v1-1", volume=1, description="")]
    issues = "\n".join(design_issues(hollow, copied))

    for expected in (
        "truth_layers",
        "factions",
        "power.cost",
        "power.limit",
        "main_foreshadows",
        "desire",
        "threads.dark",
        "相同",
    ):
        assert expected in issues


def test_normalize_keeps_model_text_and_owns_every_chapter_number():
    bible = make_bible()
    design = normalize(model_design(), bible, 50)

    by_id = {row["id"]: row for row in design["foreshadows"]}
    assert (by_id["f01"]["plant"], by_id["f01"]["reminders"], by_id["f01"]["payoff"]) == (2, [9, 17], 27)
    assert by_id["f03"]["anchor"] == ""
    for row in design["foreshadows"]:
        assert 1 <= row["plant"] < row["payoff"] <= 49
        assert all(row["plant"] < n < row["payoff"] for n in row["reminders"]) and len(row["reminders"]) == 2
    assert 41 <= by_id["f03"]["payoff"] <= 49 and 11 <= by_id["f02"]["payoff"] <= 20

    chapters = [row["chapter"] for row in design["truth_reveals"]]
    assert chapters == sorted(chapters) and chapters[0] >= 8 and chapters[1] == 29 and chapters[2] == 48
    arc = {row["kind"]: row["chapter"] for row in design["arc"]}
    assert 3 <= arc["flaw_cost"] <= 20 and 41 <= arc["fear_faced"] <= 49 and arc["growth_end"] == 50
    assert [len(row["steps"]) for row in design["relationships"]] == [2]
    first = {row["name"]: row["first_chapter"] for row in design["appearances"]}
    assert first["陆希砚"] <= 20 and 11 <= first["韩朔言"] <= 13 and first["裴晏竹"] == 1
    assert design["characters"][0]["wound"].startswith("十二岁") and len(design["characters"][0]["habits"]) == 3
    gaps = "\n".join(design_gaps(design))
    assert "foreshadows.f03" in gaps and "characters.陆希砚" in gaps and "stage_progress.2" in gaps


def test_normalize_handles_short_books_and_an_empty_model_answer():
    bible = make_bible(total=5, stages=1)
    design = normalize({}, bible, 5)
    for row in design["foreshadows"]:
        assert 1 <= row["plant"] < row["payoff"] <= 4
        assert all(row["plant"] < n < row["payoff"] for n in row["reminders"])
    assert all(1 <= row["chapter"] <= 5 for row in design["arc"] + design["truth_reveals"])
    assert all(not row["wound"] for row in design["characters"])
    assert all_duties(dict(design, total=5), bible)


def test_duties_reach_the_writer_then_close_only_on_what_the_text_contains(tmp_path):
    bible = make_bible()
    bible["story_design"] = normalize(model_design(), bible, 50)
    outline = dict(
        number=2, type="对抗", scenes=[dict(characters=["顾长风", "沈展尧"])], promise_payoff=dict(action="断线")
    )

    planned = with_story_duties(outline, bible, tmp_path)
    kinds = [duty["kind"] for duty in planned["story_duties"]]
    assert "plant" in kinds and len(kinds) <= 3
    plant = next(duty for duty in planned["story_duties"] if duty["kind"] == "plant")
    assert plant["anchors"] == ["双弦纹"] and "木手上的旧刻痕" in plant["text"]

    sections = dict(writer_sections(planned, bible))
    assert any("十二岁那年" in body for body in sections.values())
    assert any("双弦纹" in body for body in sections.values())
    instruction = writer_instruction(planned, bible)
    assert "开篇三章" in instruction and "兑现章" in instruction and "裁决" in instruction
    assert "开篇三章" not in writer_instruction(dict(outline, number=9, promise_payoff={}), bible)
    assert writer_instruction(planned, dict(bible, story_design={})) == ""

    results = record_story_duties(tmp_path, 2, "他低头，看见断口上那道细细的双弦纹。沈展尧没有说话。", planned)
    status = {row["id"]: row["status"] for row in results}
    assert status["f01-plant"] == "done"
    ledger = load_ledger(tmp_path)
    assert ledger["f01-plant"]["quote"] == "他低头，看见断口上那道细细的双弦纹。"
    missing = [key for key, value in status.items() if value == "missing"]
    assert missing

    later = due_duties(bible["story_design"], bible, 3, "对抗", ledger)
    assert "f01-plant" not in [d["id"] for d in later]
    assert set(missing) & {d["id"] for d in later}
    again = due_duties(bible["story_design"], bible, 2, "对抗", ledger)
    assert "f01-plant" in [d["id"] for d in again]


def test_relationship_steps_wait_for_an_emotional_chapter_and_payoffs_never_expire():
    bible = make_bible()
    design = normalize(model_design(), bible, 50)
    ids = lambda number, kind, ledger=None: [d["id"] for d in due_duties(design, bible, number, kind, ledger or {})]

    assert not any(i.startswith("rel-") for i in ids(4, "对抗"))
    assert "rel-顾长风-沈展尧-s1" in ids(4, "情感")
    assert "rel-顾长风-沈展尧-s1" in ids(10, "对抗")
    assert "f02-payoff" in ids(40, "日常")
    assert "f02-plant" not in ids(40, "日常")
    villains = [d for d in all_duties(design, bible) if d["kind"] == "villain_move"]
    exits = {row["name"]: row["exit"] for row in bible["antagonists"]}
    assert len(villains) >= 5 and all(d["chapter"] < exits[d["names"][0]] for d in villains)


def test_planner_sees_window_duties_stage_goal_and_relationship_steps(tmp_path):
    bible = make_bible()
    bible["story_design"] = normalize(model_design(), bible, 50)
    sections = planner_sections(bible, tmp_path, 1, 10, 1)
    text = json.dumps(sections, ensure_ascii=False)
    assert "双弦纹" in text and "斩断第一条线" in text and "替他包扎右腕" in text
    assert all(item["chapter"] <= 10 for item in sections[0][1])
    assert planner_sections(dict(bible, story_design={}), tmp_path, 1, 10, 1) == []


def test_design_is_created_once_and_never_for_a_book_already_in_progress(tmp_path):
    bible, threads = make_bible(), make_threads()
    calls = []

    def ask(kind, key, sections, redo):
        calls.append((kind, key, redo))
        return model_design() if redo else {}

    design = ensure_story_design(ask, bible, threads, 50, tmp_path)
    assert [c[2] for c in calls] == [0, 1] and design["foreshadows"][0]["anchor"] == "双弦纹"
    assert design["generation"]["remaining_gaps"]
    assert load_story_design(tmp_path)["total"] == 50
    assert ensure_story_design(ask, bible, threads, 50, tmp_path) == load_story_design(tmp_path) and len(calls) == 2

    started = tmp_path / "started"
    (started / "chapters").mkdir(parents=True)
    (started / "chapters/001.json").write_text("{}")
    assert ensure_story_design(ask, bible, threads, 50, started) == {} and len(calls) == 2
    earlier = {k: v for k, v in bible.items() if k != "story_layer_version"}
    assert ensure_story_design(ask, earlier, threads, 50, tmp_path / "earlier") == {} and len(calls) == 2


def test_story_report_counts_threads_duties_and_reading_ratios(tmp_path):
    bible, threads = make_bible(), make_threads()
    design = normalize(model_design(), bible, 50)
    (tmp_path / "story_bible.yaml").write_text(yaml.safe_dump(bible, allow_unicode=True))
    (tmp_path / "threads.yaml").write_text(yaml.safe_dump(threads, allow_unicode=True))
    (tmp_path / "story_design.yaml").write_text(yaml.safe_dump(design, allow_unicode=True))
    text = "顾长风心想，这只手不该听他的。沈展尧道：“走。”他看见断口上的双弦纹。"
    (tmp_path / "chapter-002.body.md").write_text(text)
    planned = with_story_duties(dict(number=2, type="对抗", scenes=[]), dict(bible, story_design=design), tmp_path)
    record_story_duties(tmp_path, 2, text, planned)

    report = story_report(tmp_path, source="draft")

    assert report["reference_only"] and report["design_present"]
    assert report["threads"] == dict(beats=30, empty=0, distinct=30)
    assert report["duties"]["status"]["done"] >= 1 and report["duties"]["status"]["not_due"] > 0
    metrics = report["reader_metrics"]
    assert 0 < metrics["dialogue_share"] < 1 and metrics["interior_per_10k"] > 0
    assert "陆希砚" in metrics["never_on_page"] and "顾长风" not in metrics["never_on_page"]
