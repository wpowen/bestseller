"""Instruction 83: two books stopped on planning after an antagonist died exactly as planned.

- 5d560fa1 stopped before chapter 50: 闻人照 died in chapter 49 (his exit), the stage summary still
  described the final duel, and a valid “生前布下的…” rewrite was rejected for nulling “antagonist”.
- 3f3950ba stopped before chapter 31: rewrites “裴照夜遗留的缺页” and “裴照夜夺册时留下的刻痕” were
  rejected although nothing had him act.
"""

from bookengine.life_planning import _candidate, _died_on_schedule, _future
from bookengine.life_planning_facts import validate_life_constraints

DEATH = {"裴照夜": {"chapter": 30, "quote": "裴照夜被乱剑绞碎。", "status": "Evidence"}}


def _hits(text, deaths=DEATH):
    return validate_life_constraints({"beats": [{"description": text}]}, deaths)


def test_things_the_dead_left_behind_are_not_actions():
    assert not _hits("顾寒从裴照夜遗弃的藏册暗格中寻得缺页。")
    assert not _hits("裴照夜遗留的血染缺页被顾寒寻获。")
    assert not _hits("顾寒循裴照夜夺册时留下的断剑刻痕追回名册。")
    assert not _hits("裴照夜死亡后，线索指向戒律堂。")


def test_the_dead_still_cannot_act_or_return():
    assert _hits("裴照夜率剑使封住山门。")
    assert _hits("顾寒追逐裴照夜夺回弟子名册。")
    assert _hits("裴照夜留下的人说裴照夜并未死。")
    assert _hits("裴照夜的尸体仍然开口说话。")


def test_role_slot_of_the_dead_may_be_emptied_but_names_in_text_may_not():
    source = {"antagonist": "裴照夜", "goal": "顾寒在问剑台击败裴照夜。"}
    hits = validate_life_constraints(source, DEATH)
    reply = {"changes": [{"path": ["antagonist"], "value": None}, {"path": ["goal"], "value": "顾寒守住问剑台。"}]}
    adopted, reason = _candidate(source, reply, hits, {})
    assert reason == "" and adopted == {"antagonist": "", "goal": "顾寒守住问剑台。"}
    bad = {"changes": [{"path": ["goal"], "value": None}]}
    assert _candidate(source, bad, hits, {})[1] == "invalid_replacement"


def test_written_promise_payoffs_are_history_for_a_stage():
    stage = {"promise_payoffs": [{"chapter": 45, "target": "裴照夜"}, {"chapter": 50, "target": "裴照夜"}]}
    future = _future(stage, None, 50, "volume")
    assert not future(["promise_payoffs", 0, "target"]) and future(["promise_payoffs", 1, "target"])


def test_antagonist_dying_at_planned_exit_leaves_the_stage_summary_alone():
    bible = {"antagonists": [{"name": "裴照夜", "exit": 20}, {"name": "裴照夜", "exit": 30}]}
    assert _died_on_schedule(bible, "裴照夜", {"chapter": 30})
    assert not _died_on_schedule(bible, "裴照夜", {"chapter": 22})
    assert not _died_on_schedule({"antagonists": []}, "沈嘉屿", {"chapter": 30})
