import pytest
from bookengine.stage_transition import complete_transitions


@pytest.mark.parametrize("observed_scenes", [2, None, {}, "2"])
def test_completed_scene_count_is_not_a_plan_scene_list(observed_scenes):
    previous = dict(number=10, location="渡口", scenes=observed_scenes, fact_source="final_body")
    batch = [dict(number=11, location="山城", scenes=[dict(where="山城", action="查看城门")])]
    result = complete_transitions(batch, [previous])
    assert "离开渡口" in result[0]["transition"]
    assert "抵达山城" in result[0]["transition"]
    assert "transition" not in batch[0]


def test_native_where_fields_participate_in_planned_transition():
    history = [dict(number=1, scenes=[dict(where="石桥")])]
    result = complete_transitions([dict(number=2, scenes=[dict(where="驿站")])], history)
    assert "离开石桥" in result[0]["transition"]
