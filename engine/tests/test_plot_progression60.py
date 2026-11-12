"""Instruction 60 C4: completed events cannot restart in a later opening scene."""

import json

from bookengine.cross_chapter_facts import merge_cross_chapter_delta, writer_critical_events
from bookengine.plot_progression import plot_progression_checkup, plot_replay_findings, repair_plot_replay


CH25_DONE = "药奴脱险了。"
CH26_REPLAY = (
    "药奴撤离口连着旧魂灯室外环。两人沿暗口下行时，脚下先传来铁链拖地的回声。"
    "沈展尧在最前方停住，借灯照向一条倾斜的排水槽。槽底有一排药奴留下的旧脚印，方向竟朝他们来路折返。"
)
CH49_DONE = (
    "裴沐弦的手指从像脚滑落，身体被塌陷的断口吞到腰际，随后重重坠入阵底。"
    "核心阵最后一圈青光绕过他，像确认最后一项献祭已经完成，随后彻底熄灭。"
)
CH50_REPLAY = (
    "顾长风从断口下方抬起头时，裴沐弦已倒在碎裂的祖像前。"
    "那具替裴晏竹维系禁库控制的身体再没有动过，黑木印留下的焦痕沿石面扩散。"
    "顾长风拖着失力的右臂，从北侧铜槽旁转身，沿祖阵断口外的石阶向上爬去。席位台正一寸寸下滑。"
)


def _state():
    delta = {"critical_events": [{"summary": "药奴已经脱险并离开危险区域", "quote": CH25_DONE}]}
    return merge_cross_chapter_delta({}, delta, CH25_DONE, 25)


def test_c4_writer_receives_completed_event_category_without_copyable_example_sentence():
    facts = writer_critical_events(_state(), {"number": 26})

    assert facts == ["已完成事件，不得重新当作进行中的任务：第25章 药奴已经脱险并离开危险区域"]
    assert CH25_DONE not in facts[0]


def test_c4_original_ch25_26_and_ch49_50_replays_are_detected_with_quotes_and_outlines():
    chapters = [
        {"number": 25, "text": CH25_DONE},
        {"number": 26, "text": CH26_REPLAY},
        {"number": 49, "text": CH49_DONE},
        {"number": 50, "text": CH50_REPLAY},
    ]
    outlines = {
        25: {"scenes": [{"result": "药奴脱险，一段禁库钥匙到手"}]},
        26: {"scenes": [{"goal": "完成药奴撤离", "result": "药奴安全离开"}]},
        49: {"scenes": [{"result": "裴沐弦死亡，核心控制阵崩毁"}]},
        50: {"opening_conflict": "核心阵崩毁牵动禁制", "scenes": [{"result": "核心阵彻底毁灭"}]},
    }

    report = plot_progression_checkup(chapters, outlines=outlines)

    assert report["counts"]["plot_replays"] == 2
    assert [row["chapters"] for row in report["plot_replays"]] == [[25, 26], [49, 50]]
    assert report["plot_replays"][0]["previous_quote"] == CH25_DONE
    assert report["plot_replays"][1]["previous_quote"] == CH49_DONE


def test_c4_runtime_repair_targets_replay_then_rechecks_or_rolls_back():
    replacement = "顾长风与沈展尧沿旧魂灯室下层继续追踪崔林霏留下的核心线。"

    def run(recheck):
        stages = []

        def ask(_client, key, _instruction, sections, _schema, **_kwargs):
            stages.append(key)
            if key.endswith("-plot-replay-review"):
                return {
                    "findings": [
                        {
                            "id": 1,
                            "previous_chapter": 25,
                            "previous_quote": CH25_DONE,
                            "current_quote": CH26_REPLAY,
                            "reason": "已脱险事件被重新写成撤离中",
                        }
                    ]
                }
            if key.endswith("-plot-replay-repair"):
                target = json.loads(dict(sections)["允许修改的回卷段"])[0]
                return {"edits": [{"id": target["id"], "text": replacement}]}
            return {"findings": recheck}

        result = repair_plot_replay(
            None,
            "r2-ch26",
            CH26_REPLAY,
            {"events": []},
            False,
            _state(),
            {},
            {"number": 26},
            lambda *_args, **_kwargs: ({"events": [], "continuity_delta": {}}, False, 0),
            ask,
        )
        return result, stages

    repaired, stages = run([])
    assert repaired[0] == replacement
    assert plot_replay_findings(repaired[0], _state(), 26) == []
    assert repaired[-1]["status"] == "repaired"
    assert stages == [
        "r2-ch26-plot-replay-review",
        "r2-ch26-plot-replay-repair",
        "r2-ch26-plot-replay-recheck",
    ]

    rejected, _ = run(
        [
            {
                "id": 1,
                "previous_chapter": 25,
                "previous_quote": CH25_DONE,
                "current_quote": replacement,
                "reason": "改写后仍回卷",
            }
        ]
    )
    assert rejected[0] == CH26_REPLAY
    assert rejected[-1]["status"] == "rolled_back" and rejected[-1]["reason"] == "plot_replay_recheck_failed"
