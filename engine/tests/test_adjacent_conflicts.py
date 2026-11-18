"""Regression coverage for reused neighboring scene action."""

import json

from bookengine.adjacent_conflicts import adjacent_conflict_issues, previous_conflict_context
from bookengine.outline_diversity import historical_outlines, repair_batch
from bookengine.planning_policy import replan_targets
from bookengine.storage import save


def plan(number, choice):
    return dict(
        number=number,
        title=f"夜{number}",
        type="对抗",
        scenes=[
            dict(
                where="山门",
                characters=["顾寒", "魏横"],
                choice=choice,
                action=choice,
                ability_use="从石阶上记起失传剑式",
            )
        ],
        required_event=f"第{number}章的已定收获",
    )


OPERATION = "顾寒先借魏横杂役队踩乱石阶，挤压狭窄地形，再以受伤、失衡和短暂握剑失控逼出剑式轮廓"
COST = "前世剑意刺痛并使身体失控，尚不能持续施展剑式，不能压制境界更高的对手"


def test_detects_reused_operation_even_when_intervening_goal_changes():
    old = [plan(n, OPERATION + f"。他扫净第{level}级青石。" + COST) for n, level in enumerate(("一", "二", "三"), 1)]
    assert [row["chapters"] for row in adjacent_conflict_issues([], old)] == [[1, 2], [2, 3]]
    assert replan_targets(adjacent_conflict_issues([], old)) == [2, 3]
    different = [
        old[0],
        plan(2, "魏横拦路问起旧名册，顾寒让韩小满用开锅分粮的条件换杂役作证"),
        plan(3, "魏横疑心自己的手下藏了失踪的药瓶，顾寒从瓶底的泥印找到入口"),
    ]
    assert not adjacent_conflict_issues([], different)


def test_bounded_repair_only_accepts_changed_scene_and_preserves_required_event(tmp_path):
    original = [plan(n, OPERATION) for n in range(1, 4)]
    replacement = plan(2, "顾寒要问出旧名册去向，韩小满用开锅分粮的条件换杂役作证")
    replacement["required_event"] = "偷偷换掉不可变的收获"
    calls = []

    def rewrite(current, issues, targets, vocabulary, attempt):
        calls.append((targets, attempt))
        return {"chapters": [replacement, dict(plan(3, replacement["scenes"][0]["action"]), conflict_type="揭秘")]}

    updated = repair_batch(tmp_path, "001-003", [], original, rewrite)
    assert calls == [([2, 3], 1)]
    assert updated[0] == original[0]
    assert updated[1]["scenes"] == replacement["scenes"]
    assert updated[1]["required_event"] == original[1]["required_event"]
    assert updated[2] == original[2]
    assert not adjacent_conflict_issues([], updated)


def test_previous_chapter_preserves_planned_scene_when_record_has_count(tmp_path):
    save(tmp_path / "writing_outlines/001.json", plan(1, OPERATION))
    record = tmp_path / "chapters/001.json"
    save(record, dict(number=1, status="degraded", fact_source="final_body", scenes=1, summary="保住了名额"))
    assert previous_conflict_context(tmp_path, 2) == []
    save(record, dict(json.loads(record.read_text()), status="complete"))

    history = historical_outlines(tmp_path, 2)
    assert history[0]["scenes"] == 1
    assert replan_targets(adjacent_conflict_issues(history, [plan(2, OPERATION)])) == [2]
    [(heading, context)] = previous_conflict_context(tmp_path, 2)
    assert "避重" in heading
    assert "第1章实际结果：保住了名额" in context
    assert OPERATION in context
