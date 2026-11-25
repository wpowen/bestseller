from copy import deepcopy
from types import SimpleNamespace

from bookengine.q1_draft_prompt import production_prompt
from bookengine.q1_extract import ask
from bookengine.rhythm_writer import remaining_outline, writer_hook_type, writer_main_scene_contract


def test_working_detail_and_scene_geography_reach_actual_writer_request(tmp_path):
    scene = {
        "where": "渡口旧秤旁",
        "goal": "交清粮袋",
        "obstacle": "门闩压住最后一袋粮",
        "choice": "先让扛袋的孩子歇肩",
        "action": "脚夫把粮袋放低",
        "result": "孩子腾出了两只手",
    }
    outline = {
        "number": 2,
        "type": "对抗",
        "scenes": [scene],
        "solution_steps": [{"role": "感知", "detail": "脚夫从秤绳的松紧看出粮袋挂住了"}],
        "opponent_resolution": {"outcome": "仓官交还当月工钱"},
        "ending_content": "粮袋后露出了原本不该在场的秤砣",
        "hook_type": "人物当场隐瞒",
        "core_application": scene["action"] + "；" + scene["result"],
    }
    original = deepcopy(outline)
    instruction, sections = production_prompt(
        SimpleNamespace(words_per_chapter=2300, style_reference=None),
        {},
        {},
        {"number": 1},
        outline,
        {},
        [],
        [],
        "",
        [],
        tmp_path,
        "b",
        {"b": ""},
    )
    writer = SimpleNamespace(prefix="", stage=lambda stage, prompt, *args, **kwargs: prompt)
    request = ask(writer, "r2-ch2-draft", instruction, sections, input_limit=10000)
    first_contract = request.split("\n## ", 2)[1]
    for value in (scene["obstacle"], scene["where"], outline["solution_steps"][0]["detail"]):
        assert value in first_contract
        assert request.count(value) == 1
    assert "旁人的待遇" in instruction
    assert "两场戏，各约" not in request
    for text in (*scene.values(), outline["ending_content"]):
        assert request.count(text) == 1
    assert outline["hook_type"] not in request
    assert outline == original


def test_quiet_scene_and_historical_outline_do_not_get_invented_business():
    scene = dict(goal="还钱", action="留下零钱", choice="多坐一会", result="听完旧事")
    outline = {"type": "情感", "scenes": [dict(scene, where="阵口", obstacle="追兵赶到")]}
    assert writer_main_scene_contract(outline)["scenes"] == [scene]
    old = writer_main_scene_contract({"type": "对抗", "scenes": [scene]})
    assert old["scenes"] == [scene]
    assert "人物据以判断的现场细节" not in old


def test_keep_distinct_ability_cost_and_secondary_scene_detail():
    scene = dict(action="扶正砝码", result="米袋放平", subtext="不肯白拿这袋米")
    outline = dict(type="日常", scenes=[scene], core_application="扶正砝码；烧伤右手")
    remaining = remaining_outline(outline, writer_main_scene_contract(outline))
    assert remaining["core_application"] == outline["core_application"]
    assert remaining["scenes"] == [{"subtext": scene["subtext"]}]


def test_planned_ending_legacy_fallback_and_final_closure():
    canon = {"hook_types": ["隐瞒", "疑问"]}
    planned = {"ending_content": "旧桥当众塌了", "ending_type": "迫近危险", "hook_type": "疑问"}
    assert writer_hook_type(planned, "隐瞒", canon, False) is None
    assert writer_hook_type({"hook": "他问秤砣归谁", "hook_type": "疑问"}, "隐瞒", canon, False) == "疑问"
    assert writer_hook_type({}, "隐瞒", canon, False) == "隐瞒"
    assert writer_hook_type(planned, "隐瞒", canon, True) == "完结收束"
    same_ending = dict(planned, scenes=[{"result": "旧桥当众塌了。"}])
    planned_scene = writer_main_scene_contract(same_ending)
    assert planned_scene["hook"] == "以末场结果收束" and planned_scene["scenes"][0]["result"] == "旧桥当众塌了。"
    closing = dict(planned, ending=True, hook="兄弟把最后一袋米送回了家")
    result = writer_main_scene_contract(closing)
    assert result["hook"] == closing["hook"] and "hook_type" not in result
