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
    assert not _hits("裴照夜败亡后，顾寒把剑令交给沈嘉屿。")
    assert not _hits("顾寒终结闻人照引发的仙盟余波。", {"闻人照": DEATH["裴照夜"]})


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


def test_outline_rewrite_for_a_dead_final_opponent_is_adopted():
    """5d560fa1 chapter 50: two valid rewrites were rejected (null opponent, object kept the same ending)."""
    deaths = {"闻人照": {"chapter": 49, "quote": "闻人照倒下。", "status": "Evidence"}}
    batch = [
        {
            "number": 50,
            "opponent": "闻人照",
            "background_only": ["闻人照"],
            "scenes": [{"speaker_intents": [{"name": "闻人照", "goal": "逼顾寒认输"}, {"name": "沈青梧"}]}],
            "after": "两人的共同作战成为击败闻人照的必要条件。",
        }
    ]
    hits = validate_life_constraints(batch, deaths)
    reply = {
        "changes": [
            {"path": [0, "opponent"], "value": None},
            {"path": [0, "background_only", 0], "value": None},
            {"path": [0, "scenes", 0, "speaker_intents", 0, "name"], "value": None},
            {"path": [0, "after"], "value": "两人的共同作战成为斩断仙盟残余剑势的必要条件。"},
        ]
    }
    adopted, reason = _candidate(batch, reply, [h for h in hits if h["path"][-1] != "goal"], {})
    assert reason == ""
    assert adopted[0]["opponent"] == "" and adopted[0]["background_only"] == []
    assert adopted[0]["scenes"][0]["speaker_intents"] == [{"name": "沈青梧"}]
    swap = {"changes": [{"path": [0, "after"], "value": "两人的共同作战成为击败韩某的必要条件。"}]}
    assert _candidate(batch, swap, hits, {"characters": [{"name": "韩某"}]})[1] == "hard_role_swap"
    subject = [{"number": 50, "plan": "闻人照率剑使封住山门。"}]
    moved = {"changes": [{"path": [0, "plan"], "value": "仙盟长老率剑使封住山门。"}]}
    assert _candidate(subject, moved, validate_life_constraints(subject, deaths), {})[1] == "hard_role_swap"


def test_paths_without_the_chapter_index_and_off_target_edits():
    """5d560fa1 chapter 50 attempts 8–9 wrote ["payoff"] for [0, "payoff"] and edited the deleted speaker."""
    deaths = {"闻人照": {"chapter": 49, "quote": "闻人照倒下。", "status": "Evidence"}}
    batch = [{"number": 50, "opponent": "闻人照", "payoff": "顾寒击败闻人照。", "hook": "旗在风里响。"}]
    hits = validate_life_constraints(batch, deaths)
    reply = {
        "changes": [
            {"path": ["opponent"], "value": None},
            {"path": ["payoff"], "value": "顾寒斩断仙盟残余剑势。"},
            {"path": ["hook"], "value": "改掉不该改的钩子。"},
        ]
    }
    adopted, reason = _candidate(batch, reply, hits, {})
    assert reason == "" and adopted[0]["payoff"] == "顾寒斩断仙盟残余剑势。"
    assert adopted[0]["hook"] == "旗在风里响。" and adopted[0]["opponent"] == ""
