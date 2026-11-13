"""Instruction 64: a repair may not bring English in, and English in the last paragraph can be cleaned."""

import json

from bookengine.story_payoffs import repair_story_payoffs
from bookengine.text_hygiene import repair_text
from test_story_payoffs57 import outline

ENGLISH_TAIL = (
    "赫林霏终于闭上嘴，再 unable to claim the old warehouse water route belonged to him. "
    "Old warehouse outside the north wind rolled past the collapsed entrance, and could no longer move that row "
    "of halted mine carts."
)
CHINESE_TAIL = (
    "赫林霏终于闭上嘴，再也无法声称旧仓的水道归他所有。旧仓外，北风掠过坍塌的入口，再也推不动那排停住的矿车。"
)


def test_english_in_the_final_paragraph_is_translated_instead_of_rolled_back():
    text = "沈以宁扶住箱沿，手却很稳。\n\n白宥谦领人抬走伤兵。" + ENGLISH_TAIL

    def ask(*args, **kwargs):
        targets = json.loads(dict(args[3])["卫生命中"])
        assert len(targets) == 1
        return {"edits": [{"id": targets[0]["id"], "text": targets[0]["text"].replace(ENGLISH_TAIL, CHINESE_TAIL)}]}

    result, _, failed, note = repair_text(
        None,
        "r2-ch30",
        text,
        {"events": []},
        False,
        {},
        {"characters": [{"name": "沈以宁"}, {"name": "白宥谦"}, {"name": "赫林霏"}]},
        {"number": 30},
        lambda *args, **kwargs: ({"events": []}, False, 0),
        ask,
    )

    assert note["status"] == "repaired" and failed is False
    assert CHINESE_TAIL in result and "warehouse" not in result
    assert result.startswith("沈以宁扶住箱沿，手却很稳。")


def test_clean_ending_is_still_protected_when_the_english_is_elsewhere():
    text = "沈以宁 looked at the old warehouse door and said nothing at all.\n\n白宥谦领人抬走伤兵。"

    def ask(*args, **kwargs):
        targets = json.loads(dict(args[3])["卫生命中"])
        return {"edits": [{"id": targets[0]["id"], "text": "沈以宁看着旧仓的门，一言不发。白宥谦转身走了。"}]}

    result, _, _, note = repair_text(
        None,
        "r2-ch30",
        text,
        {"events": []},
        False,
        {},
        {"characters": [{"name": "沈以宁"}, {"name": "白宥谦"}]},
        {"number": 30},
        lambda *args, **kwargs: ({"events": []}, False, 0),
        ask,
    )

    assert result.endswith("白宥谦领人抬走伤兵。")
    assert note["status"] in ("repaired", "rolled_back")
    if note["status"] == "repaired":
        assert "looked" not in result


def test_conflict_rewrite_that_drifts_into_english_is_rolled_back():
    original = "矿民守住坡口。\n\n岑烈没有恋战，消失在风雪中。\n\n石门随即合拢。\n\n众人只能停步。"
    calls = []

    def ask(client, key, instruction, sections, schema, **kwargs):
        calls.append(key)
        if key.endswith("-conflict-repair"):
            allowed = json.loads(dict(sections)["可修改的冲突段落及其后果"])
            by_id = {row["id"]: row for row in allowed}
            return {
                "edits": [
                    {
                        "id": 2,
                        "original": by_id[2]["original"],
                        "text": "矿民合力截断退路，岑烈被猎索捆住押下，再 unable to claim the grain road belonged to him.",
                    }
                ]
            }
        items = json.loads(dict(sections)["本章必须发生的事"])
        return {"items": [{"id": item["id"], "status": "missing", "evidence": []} for item in items]}

    changed, _, failed, note = repair_story_payoffs(
        None, "ch8", original, {"events": []}, False, {}, {}, outline(), lambda *a, **k: ({"events": []}, False, 0), ask
    )

    assert changed == original and failed is False
    assert note["status"] == "rolled_back" and note["reason"] == "repair_introduced_english"
    assert not any(key.endswith("-recheck-prose-review") for key in calls)
