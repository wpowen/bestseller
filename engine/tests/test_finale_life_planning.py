from copy import deepcopy

from bookengine.life_planning import _candidate, apply_life_constraints, validate_life_constraints
from bookengine.storage import load
from test_life_planning70 import BIBLE, OfflineClient, death


def test_dead_opponent_slots_can_be_cleared_only_in_a_volume(tmp_path):
    state = death(tmp_path)
    original = {
        "antagonist": "沈衡",
        "opponent_resolution": {"name": "沈衡"},
        "promise_payoffs": [{"target": "沈衡", "result": "沈衡把渡口关了。"}],
    }
    changes = [
        {"path": ["antagonist"], "value": None},
        {"path": ["opponent_resolution", "name"], "value": None},
        {"path": ["promise_payoffs", 0, "target"], "value": None},
        {"path": ["promise_payoffs", 0, "result"], "value": "渡口照沈衡遗留的旧令收船。"},
    ]
    broken = deepcopy(changes)
    broken[-1]["value"] = "沈衡又在渡口下令。"
    client = OfflineClient(tmp_path, [{"changes": broken}, {"changes": changes}])
    plan = apply_life_constraints(client, BIBLE, original, state, chapter=4, kind="volume")
    assert plan["antagonist"] == plan["opponent_resolution"]["name"] == plan["promise_payoffs"][0]["target"] == ""
    assert original["antagonist"] == "沈衡" and plan["promise_payoffs"][0]["result"] == changes[-1]["value"]
    assert len(client.calls) == 2 and "remaining_conflicts" in client.calls[1]["prompt"]
    assert "沈衡倒地身亡" in client.calls[1]["prompt"]
    report, _ = sorted((tmp_path / "life-planning").glob("*.json"))
    assert [r["path"] for r in load(report)["remaining"]] == [["promise_payoffs", 0, "result"]]
    assert apply_life_constraints(OfflineClient(tmp_path), BIBLE, original, state, chapter=4, kind="volume") == plan

    hits = validate_life_constraints(original, {"沈衡": {}})
    assert _candidate(original, {"changes": changes}, hits, BIBLE, "writer")[0] is None
    for invalid in ({"path": ["antagonist"]}, {"path": ["promise_payoffs", 0, "result"], "value": None}):
        assert _candidate(original, {"changes": [invalid]}, hits, BIBLE, "volume")[0] is None


def test_volume_retry_keeps_checked_fields_when_reply_only_fixes_last_conflict(tmp_path):
    state = death(tmp_path)
    source = {"antagonist": "沈衡", "goal": "沈衡派船把守渡口。"}
    clear_name = {"changes": [{"path": ["antagonist"], "value": None}]}
    goal = {"changes": [{"path": ["goal"], "value": "渡船今日可以载客。"}]}
    c = OfflineClient(tmp_path, [clear_name, goal])
    new = apply_life_constraints(c, BIBLE, source, state, chapter=4, kind="volume")
    assert new == {"antagonist": "", "goal": "渡船今日可以载客。"}
    first, last = sorted((tmp_path / "life-planning").glob("*.json"))
    assert load(first)["status"] == "rejected"
    record = load(last)
    assert record["continued_from"] == first.stem and record["raw_candidate"] == goal
    assert record["candidate"]["changes"] == clear_name["changes"] + goal["changes"]
    assert apply_life_constraints(OfflineClient(tmp_path), BIBLE, source, state, chapter=4, kind="volume") == new


def test_outline_incremental_retry_preserves_array_prefix_and_removed_speaker(tmp_path):
    state = death(tmp_path)
    source = [{"number": 4, "goal": "沈衡派船把守渡口。", "scenes": [{"speaker_intents": [{"name": "沈衡"}]}]}]
    name = {"path": [0, "scenes", 0, "speaker_intents", 0, "name"], "value": None}
    goal = {"path": [0, "goal"], "value": "渡口重新通船。"}
    client = OfflineClient(tmp_path, [{"changes": [name]}, {"changes": [goal]}])
    revised = apply_life_constraints(client, BIBLE, source, state, chapter=4)
    assert revised[0]["scenes"][0]["speaker_intents"] == [] and revised[0]["goal"] == goal["value"]
    last = load(sorted((tmp_path / "life-planning").glob("*.json"))[-1])
    assert last["candidate"]["changes"] == [name, goal] and last["raw_candidate"]["changes"] == [goal]


def test_battle_death_allows_survivors_to_face_aftermath_but_not_new_orders():
    for ending in ("战殁后", "身亡后", "战死后"):
        assert not validate_life_constraints({"text": f"林舟收拾沈衡{ending}未散的余火。"}, {"沈衡": {}})
        assert validate_life_constraints({"text": f"沈衡{ending}仍下令封门。"}, {"沈衡": {}})
