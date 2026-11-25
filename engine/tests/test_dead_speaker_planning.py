from copy import deepcopy

import pytest

from bookengine.life_planning import _candidate, validate_life_constraints


def candidate(source, changes, kind="outlines"):
    deaths = {"沈衡": {}, "陆远": {}}
    hits = validate_life_constraints(source, deaths)
    bible = {"characters": [{"name": name} for name in ("沈衡", "陆远", "林舟")]}
    return _candidate(source, {"changes": changes}, hits, bible, kind)


def test_outline_removes_dead_speakers_before_prompt_without_shifting_other_rows():
    scene = {
        "speaker_intents": [
            {"name": "沈衡", "goal": "他要夺旗"},
            {"name": "林舟", "goal": "应付沈衡抢旗"},
            {"name": "陆远", "goal": "他要烧粮"},
        ]
    }
    original = [{"scenes": [scene], "background_only": ["沈衡", "林舟"], "opponent": "沈衡"}]
    untouched = deepcopy(original)
    changes = [
        {"path": [0, "scenes", 0, "speaker_intents", 2, "name"], "value": None},
        {"path": [0, "scenes", 0, "speaker_intents", 0, "name"], "value": None},
        {"path": [0, "scenes", 0, "speaker_intents", 1, "goal"], "value": "找人看好旗杆"},
        {"path": [0, "background_only", 0], "value": None},
        {"path": [0, "opponent"], "value": None},
    ]
    revised, reason = candidate(original, changes)
    assert not reason
    assert revised == [
        {
            "scenes": [{"speaker_intents": [{"name": "林舟", "goal": "找人看好旗杆"}]}],
            "background_only": ["林舟"],
            "opponent": "",
        }
    ]
    assert original == untouched


def test_outline_does_not_substitute_another_speaker_or_delete_an_unrelated_name():
    scene = {"speaker_intents": [{"name": "沈衡", "goal": "逼人表态"}]}
    _, reason = candidate({"scenes": [scene]}, [{"path": ["scenes", 0, "speaker_intents", 0, "name"], "value": "林舟"}])
    assert reason == "participant_must_be_deleted"
    _, reason = candidate({"author": scene}, [{"path": ["author", "speaker_intents", 0, "name"], "value": None}])
    assert reason == "invalid_replacement"


def test_mixed_solution_target_requires_a_real_remaining_objective():
    source = {"solution_target": "沈衡与仙盟剑势"}
    target = ["solution_target"]
    assert candidate(source, [{"path": target, "value": None}])[1] == "solution_target_requires_text"
    assert candidate(source, [{"path": target, "value": "仙盟剑势"}]) == ({"solution_target": "仙盟剑势"}, "")


@pytest.mark.parametrize("name", ["林舟", "钱凤", "闻人世昌"])
def test_actual_role_swap_is_rejected_but_new_action_with_shared_result_can_pass(name):
    source = {"goal": "渡口前，沈衡下令扣船。"}
    assert candidate(source, [{"path": ["goal"], "value": f"渡口前，{name}下令扣船。"}])[1] == "hard_role_swap"
    source = {"goal": "林舟击败沈衡，保住渡口与仓房。"}
    replacement = "林舟击溃残余剑势，保住渡口与仓房。"
    assert candidate(source, [{"path": ["goal"], "value": replacement}]) == ({"goal": replacement}, "")
