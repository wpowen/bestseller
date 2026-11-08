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
