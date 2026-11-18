"""Instruction 74: 《扫雪剑起》 chapters 1-3 told one event three times and never paid off the card.

Cases come from books/webui/c517201b-…: 26 chapters had scene 1's action overwritten by
“实际运用……；受……约束，并付出……”; “第1章展示……记起第一式” was only due at chapter 3; noun lists
were cut into fragments such as “同时见证杂役”; every scene's inner asked for 最想/最怕.
"""

from pathlib import Path

import yaml

from bookengine.core_ability import ability_issues, enforce_ability_plan
from bookengine.habit_phrases import habit_phrase_checkup
from bookengine.opening_promises import opening_contract
from bookengine.q1_extract import context

ROOT = Path(__file__).resolve().parents[1]
POWER = dict(
    rule="每扫净一级石阶，固定记起一招前世剑法",
    limit="未完成实战参悟的剑法只能形成模糊剑意",
    cost="每记起一招剑法都会承受前世剑意的刺痛",
)
DEVELOPMENT = dict(new_use="把扫净青石所得的剑招用于实战", new_cost=POWER["cost"], new_limit=POWER["limit"])


def bible():
    return dict(
        title="扫雪剑起",
        power=POWER,
        story_layer_version=1,
        volumes=[dict(number=1, start=1, end=10, core_development=DEVELOPMENT)],
        reader_expectation="同时见证杂役、同门和旧案之间的连续冲突",
        opening_promises=[
            "第1章展示顾寒扫净第一阶青石并记起第一式前世剑法。",
            "第2章展示他用第一式逼退魏横。",
            "前三章不提前揭开前世身份。",
        ],
    )


def test_opening_promises_are_due_in_the_chapter_they_name_and_stay_whole():
    readers = [row for row in opening_contract(bible()) if row["kind"] == "reader"]
    due = {row["requirement"]: row["chapter"] for row in readers}
    assert due["第1章展示顾寒扫净第一阶青石并记起第一式前世剑法"] == 1
    assert due["第2章展示他用第一式逼退魏横"] == 2
    assert due["前三章不提前揭开前世身份"] == 3
    assert "同时见证杂役、同门和旧案之间的连续冲突" in due and "同时见证杂役" not in due


def test_ability_plan_keeps_the_scene_and_leaves_cost_and_limit_out():
    scene = dict(goal="扫净第一级青石", action="顾寒一扫帚劈开冻雪，剑光逼退魏横", choice="当众出手")
    rows = [dict(number=n, scenes=[dict(scene), dict(goal="藏住剑招")]) for n in range(1, 4)]
    planned = enforce_ability_plan(bible(), rows)
    first = planned[0]["scenes"][0]
    assert first["action"] == scene["action"] and first["choice"] == scene["choice"]
    assert first["ability_use"] == DEVELOPMENT["new_use"]
    text = str([row["scenes"] for row in planned] + [row["core_application"] for row in planned])
    assert POWER["cost"] not in text and POWER["limit"] not in text and "实际运用" not in text
    assert not ability_issues(bible(), [], planned)
    writer = dict(context(bible(), {}, {"number": 1}, planned[0], {}, [], [], ""))["本章章纲"]
    assert DEVELOPMENT["new_use"] in writer and POWER["cost"] not in writer and POWER["limit"] not in writer


def test_negated_use_is_still_replaced():
    rows = [dict(number=1, scenes=[dict(goal="开门", choice="改用普通钥匙，不使用核心能力")])]
    planned = enforce_ability_plan(bible(), rows)
    assert "不使用" not in planned[0]["scenes"][0]["choice"]
    assert not ability_issues(bible(), [], planned)


def test_prompts_no_longer_name_the_fear_desire_template():
    schemas = (ROOT / "data/q1_schemas.yaml").read_text()
    assert "最想要和最怕" not in schemas
    style = yaml.safe_load((ROOT / "data/q1_style.yaml").read_text())
    assert "谋划不先讲给读者" in style["b"]
    plan = (ROOT / "bookengine/q1_plan.py").read_text()
    assert 'bible.get("opening_promises")' in plan
    prepare = (ROOT / "bookengine/q1_prepare.py").read_text()
    assert "第1章让选题卡开场画面当场发生" in prepare


def test_habit_phrases_are_counted_per_chapter():
    text = "他最怕扫帚被夺走。顾寒左手按住肩伤。那不是剑，而是扫帚。他更清楚，魏横已经起疑。"
    report = habit_phrase_checkup([dict(number=1, text=text), dict(number=2, text="雪停了。")])
    assert report["totals"] == dict(fear_desire_statement=1, injury_touch=1, not_but=1, knows_more=1)
    assert report["chapters_with_any"]["fear_desire_statement"] == 1
    assert report["reference_only"] is True
