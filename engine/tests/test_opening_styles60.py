"""Instruction 60 C5: opening categories have rolling limits without copyable examples."""

from bookengine.opening_styles import (
    opening_style_checkup,
    repair_opening_style,
    writer_opening_style_context,
)
from bookengine.q1_continuity import OPENING_RULE
from bookengine.stage_transition import complete_transitions


MOVEMENT = {
    1: "离开灯墙时，顾长风把木牌收入袖中。",
    3: "抵达戒律台偏道时，山下火把已经连成一线。",
    6: "顾长风从旧械架转入药奴坑时，铁闸正在下落。",
    10: "沈展尧沿水道折返军械库，雨已经停了。",
}


def test_c5_movement_opening_category_allows_three_but_flags_fourth_inside_ten_chapters():
    chapters = [
        {"number": number, "text": MOVEMENT.get(number, f"第{number}章的雨落在窗沿，顾长风抬起头。")}
        for number in range(1, 11)
    ]

    report = opening_style_checkup(chapters)

    assert report["counts"]["over_limit"] == 1
    assert report["issues"][0]["chapter"] == 10
    assert report["issues"][0]["category"] == "movement_transition"
    assert report["issues"][0]["window_chapters"] == [1, 3, 6, 10]


def test_c5_writer_and_transition_prompts_use_categories_and_facts_without_sentence_templates(tmp_path):
    for number, text in MOVEMENT.items():
        if number < 10:
            (tmp_path / f"chapter-{number:03d}.body.md").write_text(text)

    context = writer_opening_style_context(tmp_path, 10)
    transition = complete_transitions(
        [
            {"number": 1, "scenes": [{"where": "旧道"}]},
            {"number": 2, "scenes": [{"where": "城楼"}]},
        ]
    )[1]["transition"]

    assert context == [("章首句式类别约束", "最近10章移动衔接类已使用3次；达到上限，本章改用其他开场类别。")]
    assert "抵达本章地点时" not in OPENING_RULE
    assert "离开旧道" not in transition and "抵达城楼" not in transition
    assert "移动衔接" in transition and "旧道" in transition and "城楼" in transition


def test_c5_targeted_opening_repair_rechecks_style_and_handoff_then_preserves_suffix(tmp_path):
    for number, text in ((1, MOVEMENT[1]), (3, MOVEMENT[3]), (6, MOVEMENT[6])):
        (tmp_path / f"chapter-{number:03d}.body.md").write_text(text)
    original = "抵达城楼时，顾长风听见禁军换防。\n\n他把木纹钉压进掌心，沿墙根继续向上。"
    replacement = "半个时辰后，顾长风已从旧道赶到城楼，禁军正在换防。"

    def extract(_client, _key, candidate, *_args, **_kwargs):
        opening = candidate.split("\n\n", 1)[0]
        return (
            {
                "events": [],
                "opening_location": "城楼",
                "opening_quote": opening,
                "end_location": "城楼",
                "end_quote": opening,
            },
            False,
            0,
        )

    def run(value):
        return repair_opening_style(
            None,
            "r2-ch10",
            original,
            {"events": []},
            False,
            {"end_location": "旧道", "known_locations": ["旧道", "城楼"]},
            {},
            {"number": 10, "scenes": [{"where": "城楼"}]},
            tmp_path,
            extract,
            lambda *_args, **_kwargs: {"text": value},
        )

    repaired = run(replacement)
    assert repaired[0].startswith(replacement)
    assert repaired[0].endswith("他把木纹钉压进掌心，沿墙根继续向上。")
    assert repaired[-1]["status"] == "repaired"

    rejected = run("顾长风离开旧道，抵达城楼时正逢禁军换防。")
    assert rejected[0] == original
    assert rejected[-1]["status"] == "rolled_back" and rejected[-1]["reason"] == "opening_style_recheck_failed"
