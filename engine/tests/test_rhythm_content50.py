"""Content-first rhythm regressions for INBOX item 50."""

from copy import deepcopy
import json

from bookengine import outline_diversity
from bookengine.chapter_rhythm import align_label_to_content, content_type


def eval49_chapter3():
    """Reduced from the saved eval-49 writing outline; no real-library dependency."""
    return {
        "number": 3,
        "title": "矿洞焚血",
        "type": "情感",
        "rhythm_type": "情感",
        "opening_conflict": "霜牙猛然扑向矿洞口，顾昕冉只能抢下唯一一条活路。",
        "conflict": "顾昕冉必须在狭窄矿洞中借蛮玉筠之力反杀霜牙。",
        "scenes": [
            {
                "where": "矿洞口",
                "characters": ["顾昕冉", "蛮玉筠", "霜牙"],
                "goal": "引霜牙进入狭窄矿洞",
                "obstacle": "霜牙察觉黑石是诱饵",
                "choice": "借蛮玉筠冲力引霜牙进矿洞",
                "action": "引霜牙进矿洞完成反杀",
            },
            {
                "where": "旧矿井底",
                "characters": ["顾昕冉", "蛮玉筠", "霜牙"],
                "goal": "反杀霜牙并救出矿民",
                "obstacle": "左手冻伤，石镐数次脱手",
                "choice": "贴壁引兽并割开霜牙咽喉",
            },
        ],
    }


def test_eval49_saved_outline_is_classified_by_content_and_relabelled_without_scene_changes():
    source = eval49_chapter3()

    assert content_type(source) == "对抗"
    aligned = align_label_to_content(source)

    assert aligned["type"] == aligned["rhythm_type"] == "对抗"
    assert aligned["scenes"] == source["scenes"]
    assert source["type"] == "情感"


def test_exhausted_replanning_keeps_original_content_but_restores_its_honest_label(tmp_path):
    original = [eval49_chapter3()]
    before = deepcopy(original)
    attempts = []

    def relabel_only(current, issues, targets, used, attempt):
        attempts.append(attempt)
        return {"chapters": [dict(eval49_chapter3(), type="情感", rhythm_type="情感")]}

    result = outline_diversity.repair_batch(
        tmp_path,
        "003-003",
        [],
        original,
        relabel_only,
        bible={"volumes": [{"number": 1, "start": 1, "end": 10}]},
    )

    assert attempts == [1, 2]
    assert result[0]["scenes"] == before[0]["scenes"]
    assert result[0]["type"] == result[0]["rhythm_type"] == "对抗"
    report = json.loads((tmp_path / "outline-diversity/003-003.json").read_text())
    assert report["rhythm_replans"][0]["action"] == "kept_original_relabelled"
    assert report["rhythm_replans"][0]["content_type"] == "对抗"


def test_stage_content_is_planned_once_for_the_full_stage_before_window_expansion(tmp_path):
    from bookengine.rhythm_skeleton import stage_content_skeleton

    class Client:
        model = "fixture-model"
        provider = "fixture-provider"
        prefix = ""
        plan_suffix = ""
        relaxed = True

        def __init__(self):
            self.out = tmp_path
            self.calls = []

        def stage(self, name, prompt, schema=None, **kwargs):
            self.calls.append((name, prompt, schema, kwargs))
            chapters = []
            for number, kind in ((1, "对抗"), (2, "谋划"), (3, "情感"), (4, "揭秘"), (5, "收获")):
                chapters.append(
                    {
                        "number": number,
                        "type": kind,
                        "conflict": f"第{number}章的具体矛盾",
                        "result": f"第{number}章改变下一步选择",
                        "scenes": [
                            {"goal": "处理眼前问题", "obstacle": "旧选择受阻", "action": "作出新选择"},
                            {"goal": "承担选择后果", "obstacle": "同伴不接受", "action": "调整关系或分工"},
                        ],
                    }
                )
            return kwargs["parser"](json.dumps({"chapters": chapters}, ensure_ascii=False))

    client = Client()
    preferred = [{"number": number, "rhythm_type": kind} for number, kind in ((1, "对抗"), (2, "谋划"), (3, "情感"))]
    skeleton, window_slots = stage_content_skeleton(
        client,
        {"volumes": [{"number": 1, "start": 1, "end": 5}]},
        {"number": 1, "start": 1, "end": 5},
        [],
        preferred,
    )

    assert [row["number"] for row in skeleton] == [1, 2, 3, 4, 5]
    assert [row["number"] for row in window_slots] == [1, 2, 3]
    assert skeleton[2]["type"] == "情感" and skeleton[2]["conflict"] == "第3章的具体矛盾"
    assert len(client.calls) == 1
    name, prompt, schema, kwargs = client.calls[0]
    assert name == "plan-q1-rhythm-skeleton-1-5"
    assert all(text in prompt for text in ("整阶段", "哪些章打", "不打的章具体发生什么"))
    assert all(field in schema for field in ("conflict", "result", "scenes"))
    assert kwargs["max_attempts"] == 6
