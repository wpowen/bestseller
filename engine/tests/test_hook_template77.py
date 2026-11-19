"""Instruction 77: the planner's own chapter ending reaches the writer.

In 《扫雪剑起》 (books/webui/24ddbcc8-…) 10 of 23 writer outlines carried a filled-in
template such as “顾寒刚要顾寒选择……，动作停在结果发生前” instead of the planner's
concrete ending (“焚册房侧门多出一枚新换的门锁。”). The scheduler overwrote the
planner's ending_type, the keyword detector did not recognise the planner's sentence
as the scheduled type, and the contract replaced the sentence with the template. The
writer then printed “动作停在结果发生前” into the prose.
"""

from bookengine.hook_momentum import ACTIVE_TYPES, apply_ending_contract, planned_ending_type

META_PHRASES = (
    "背后还有未说明的事实",
    "动作停在结果发生前",
    "尚未查明原因",
    "关系留下裂痕",
    "之间当场选一项",
    "平静收束",
    "主角",
)


def _chapter(number, ending_type, content, **scene):
    base = dict(where="焚册房", characters=["顾寒"], goal="夺回旧册", obstacle="周厉锁上铁门", choice="顾寒撞门")
    return dict(
        number=number,
        title=f"第{number}章",
        ending_type=ending_type,
        ending_content=content,
        hook=content,
        scenes=[dict(base, **scene)],
    )


def test_planner_ending_survives_when_detector_disagrees_with_type():
    chapters = [
        _chapter(2, "刚揭事实", "沈孟遥用剑鞘挑开扫帚上的冰碴，留下子时换岗的提醒。"),
        _chapter(3, "悬停动作", "焚册房侧门多出一枚新换的门锁。"),
    ]
    result = apply_ending_contract(chapters, stage_end=10)
    assert result[0]["ending_content"] == "沈孟遥用剑鞘挑开扫帚上的冰碴，留下子时换岗的提醒"
    assert result[1]["ending_content"] == "焚册房侧门多出一枚新换的门锁"
    assert all(row["ending_type"] in ACTIVE_TYPES for row in result)


def test_repeated_type_is_relabelled_without_rewriting_the_ending():
    chapters = [
        _chapter(5, "两难选择", "沈孟遥问顾寒先取名册还是先追寒纹。"),
        _chapter(6, "两难选择", "陆小花站在岔路口，等顾寒决定她留下还是同行。"),
    ]
    result = apply_ending_contract(chapters, stage_end=10)
    assert result[0]["ending_type"] != result[1]["ending_type"]
    assert result[1]["ending_content"] == "陆小花站在岔路口，等顾寒决定她留下还是同行"


def test_writer_never_receives_meta_template_text():
    legacy = "顾寒刚要顾寒选择以碎冰式扰乱对方落脚处，动作停在结果发生前"
    chapters = [
        _chapter(7, "悬停动作", legacy, result="周厉的短剑从中段裂开，落在雪里。"),
        _chapter(8, "刚揭事实", "", characters=[], result=""),
        _chapter(9, "反常物件", "主角这才发现，旧册背面只有残印背后还有未说明的事实"),
    ]
    result = apply_ending_contract(chapters, stage_end=10)
    for row in result:
        for phrase in META_PHRASES:
            assert phrase not in row["ending_content"], (row["number"], phrase)
            assert phrase not in row["hook"], (row["number"], phrase)
        assert "顾寒刚要顾寒" not in row["hook"]
    assert result[0]["ending_content"] == "周厉的短剑从中段裂开，落在雪里"


def test_scheduler_keeps_planner_type_and_only_fills_a_missing_one():
    assert planned_ending_type({"ending_type": "关系裂痕"}, "刚揭事实") == "关系裂痕"
    assert planned_ending_type({"ending_type": "留下悬念"}, "刚揭事实") == "刚揭事实"
    assert planned_ending_type({}, "迫近危险") == "迫近危险"
