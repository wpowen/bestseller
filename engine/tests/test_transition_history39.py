import pytest
from bookengine.stage_transition import complete_transitions


@pytest.mark.parametrize("observed_scenes", [2, None, {}, "2"])
def test_completed_scene_count_is_not_a_plan_scene_list(observed_scenes):
    previous = dict(number=10, location="渡口", scenes=observed_scenes, fact_source="final_body")
    batch = [dict(number=11, location="山城", scenes=[dict(where="山城", action="查看城门")])]
    result = complete_transitions(batch, [previous])
    transition = result[0]["transition"]
    assert "移动衔接" in transition and "参考地点：渡口" in transition and "本章地点：山城" in transition
    assert "离开渡口" not in transition and "抵达山城" not in transition
    assert "transition" not in batch[0]


def test_native_where_fields_participate_in_planned_transition():
    history = [dict(number=1, scenes=[dict(where="石桥")])]
    result = complete_transitions([dict(number=2, scenes=[dict(where="驿站")])], history)
    transition = result[0]["transition"]
    assert "移动衔接" in transition and "参考地点：石桥" in transition and "本章地点：驿站" in transition
    assert "离开石桥" not in transition and "抵达驿站" not in transition
