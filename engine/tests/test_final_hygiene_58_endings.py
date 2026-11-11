"""INBOX 58 item 2: the final chapter must close instead of opening a new mystery."""

import json

import yaml

from bookengine.endings import final_closure_hits, repair_final_closure
from tools.book_checkup import check_book


EVAL57_ENDING = """裂缝深处浮出一行此前从未显现的小字。

祖使缺位，受命者补位。

白苓的笑意僵住。陆铜生骂声卡在喉中。沈照霜盯着那行字，眼底第一次有了明显寒意。

顾立谦低头看向掌心。裂缝里的黑印没有消失。它认下了一个新的顾立谦。"""


def test_eval57_final_paragraphs_detect_new_mystery_and_identity():
    hits = final_closure_hits(EVAL57_ENDING)

    assert {row["kind"] for row in hits} >= {"new_mystery", "new_identity"}
    assert all(row["text"] in EVAL57_ENDING for row in hits)


def test_final_closure_repair_replaces_only_tail_and_rechecks():
    prefix = "顾立谦与霜团合力毁去役兽印，矿民们终于走出雪谷。"
    text = prefix + "\n\n" + EVAL57_ENDING
    requests = []

    def ask(*args, **kwargs):
        requests.append(args[1])
        return {
            "text": "顾立谦摊开掌心，黑印在晨光里一点点褪尽。\n\n"
            "霜团用鼻尖碰了碰他的手。他不再是任人役使的弃卒，而是与灵兽并肩守住归路的人。"
        }

    result, raw, failed, note = repair_final_closure(
        None,
        "r2-ch50",
        text,
        {"events": []},
        False,
        {},
        {"title": "《我拿回忆喂兽，兽力替我杀人》", "characters": [{"name": "顾立谦"}]},
        {
            "number": 50,
            "ending": True,
            "ending_promise_response": {
                "promise_response": "人与灵兽共同终结役使",
                "growth_dimension": "身份",
                "before": "顾家弃卒",
                "after": "被雪原承认的守路人",
            },
        },
        lambda *_args, **_kwargs: ({"events": [], "state": {}}, False, 0),
        ask,
    )

    assert requests == ["r2-ch50-final-closure"]
    assert result.startswith(prefix + "\n\n")
    assert not final_closure_hits(result)
    assert "黑印在晨光里一点点褪尽" in result
    assert raw == {"events": [], "state": {}} and failed is False
    assert note["status"] == "repaired"


def test_final_closure_repair_rolls_back_when_new_task_remains():
    result, raw, failed, note = repair_final_closure(
        None,
        "r2-ch50",
        EVAL57_ENDING,
        {"events": []},
        False,
        {},
        {},
        {"number": 50, "ending": True},
        lambda *_args, **_kwargs: ({"events": [], "state": {}}, False, 0),
        lambda *_args, **_kwargs: {"text": "新的主使仍待追查，顾立谦明日就要出发。"},
    )

    assert result == EVAL57_ENDING and raw == {"events": []} and failed is False
    assert note["status"] == "rolled_back" and note["reason"] == "closure_recheck_failed"


def test_final_closure_accepts_short_web_paragraphs_within_the_bounded_tail():
    replacement = "\n\n".join(
        [
            "控制石的旧字彻底熄灭。",
            "顾立谦没有借那股催逼众人的兽力。",
            "他只借一缕风辨出出口。",
            "白苓护着顾梨先走。",
            "陆铜生和陶七各自扶起伤员。",
            "霜团用鼻尖碰了碰他的掌心。",
        ]
    )

    result, _, _, note = repair_final_closure(
        None,
        "r2-ch50",
        EVAL57_ENDING,
        {"events": []},
        False,
        {},
        {},
        {"number": 50, "ending": True},
        lambda *_args, **_kwargs: ({"events": [], "state": {}}, False, 0),
        lambda *_args, **_kwargs: {"text": replacement},
    )

    assert result == replacement
    assert note["status"] == "repaired"


def test_checkup_registers_final_chapter_new_suspense(tmp_path):
    bible = {"title": "终局", "characters": []}
    (tmp_path / "story_bible.yaml").write_text(yaml.safe_dump(bible, allow_unicode=True))
    (tmp_path / "run.json").write_text(json.dumps({"status": "complete", "book": {"chapters": 50}}))
    (tmp_path / "final").mkdir()
    (tmp_path / "final/chapter-050.md").write_text(EVAL57_ENDING)

    result = check_book(tmp_path)["final_chapter_new_suspense"]

    assert result["count"] >= 2
    assert result["chapter"] == 50
    assert {row["kind"] for row in result["issues"]} >= {"new_mystery", "new_identity"}
