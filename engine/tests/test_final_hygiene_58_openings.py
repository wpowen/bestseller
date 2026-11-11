"""INBOX 58 item 3: do not replay completed actions or re-arrive without a reason."""

import json

import yaml

from bookengine.prose_similarity import (
    adjacent_opening_hits,
    repeated_arrival_hits,
    repair_cross_chapter_opening,
    with_completed_action_context,
)
from tools.book_checkup import check_book


CH18 = """半刻钟后，顾立谦从阵口退了下来。

右臂垂在身侧，骨匣已由陆铜生用旧索捆在背后，霜团仍守在匣边，不许旁人靠近。

众人最终先分粮。白苓领走伤药，把三份中的两份给重伤者。"""

CH19 = """半刻钟后，顾立谦从阵口退下，带着霜团沿上层风沟折返，避开仍在墙腹内拖拽的细骨链，抵达石门右侧。

“它进去多久了？”"""

CH11 = """顾立谦伏低身体继续前冲。窄轨在前方陡降，前方横着一座悬骨桥，桥下白骨累累。

跑到桥中段，他脑中忽然空了一瞬。

悬骨桥从薛骨脚下裂开。"""

CH13 = """绕过转轮室后，通道骤然向下。风从狭缝中灌进来，带着湿骨和旧血的气味。

悬骨桥到了。

霜团没有上桥。"""


def test_eval57_adjacent_opening_replays_completed_action():
    hits = adjacent_opening_hits(CH18, CH19)

    assert hits
    assert hits[0]["kind"] in {"repeated_action", "repeated_time_opening"}
    assert "半刻钟后" in hits[0]["current_text"]


def test_eval57_repeated_arrival_requires_an_explicit_return_reason():
    hits = repeated_arrival_hits([(11, CH11)], 13, CH13)

    assert len(hits) == 1
    assert hits[0]["place"] == "悬骨桥"
    assert hits[0]["previous_chapter"] == 11
    assert repeated_arrival_hits([(11, CH11)], 13, "他们因救人原路折返，重回悬骨桥。") == []


def test_writer_context_marks_previous_scene_and_seen_places_as_completed(tmp_path):
    (tmp_path / "chapter-011.body.md").write_text(CH11)
    (tmp_path / "chapter-012.body.md").write_text("众人离开骨桥，进入转轮室。")

    sections = with_completed_action_context([("章纲", "继续追踪")], tmp_path, 13)

    assert sections[0] == ("章纲", "继续追踪")
    title, value = sections[-1]
    assert title == "已经发生，不要重写"
    assert "众人离开骨桥" in value
    assert "悬骨桥" in value


def test_cross_chapter_opening_repair_changes_only_opening_and_rechecks(tmp_path):
    (tmp_path / "chapter-011.body.md").write_text(CH11)
    (tmp_path / "chapter-012.body.md").write_text("众人穿过转轮室，发现阵眼在桥下。")
    tail = "\n\n顾立谦沿骨索滑向阵眼，霜团在下方低鸣。"
    text = CH13 + tail

    result, raw, failed, note = repair_cross_chapter_opening(
        None,
        "r2-ch13",
        text,
        {"events": []},
        False,
        {},
        {},
        {"number": 13},
        tmp_path,
        lambda *_args, **_kwargs: ({"events": [], "state": {}}, False, 0),
        lambda *_args, **_kwargs: {
            "text": "为救被拖回阵眼的霜团，顾立谦沿转轮室下方的断索折返旧桥。\n\n"
            "桥面已断，他没有停步，直接扣住垂向阵眼的骨索。"
        },
    )

    assert result.endswith(tail)
    assert "折返旧桥" in result
    assert not repeated_arrival_hits([(11, CH11)], 13, result)
    assert raw == {"events": [], "state": {}} and failed is False
    assert note["status"] == "repaired"


def test_checkup_registers_both_cross_chapter_opening_defects(tmp_path):
    (tmp_path / "story_bible.yaml").write_text(yaml.safe_dump({"characters": []}, allow_unicode=True))
    (tmp_path / "run.json").write_text(json.dumps({"status": "complete", "book": {"chapters": 19}}))
    (tmp_path / "final").mkdir()
    for number, text in ((11, CH11), (13, CH13), (18, CH18), (19, CH19)):
        (tmp_path / f"final/chapter-{number:03d}.md").write_text(text)

    result = check_book(tmp_path)

    assert [row["chapters"] for row in result["adjacent_opening_repetitions"]["issues"]] == [[18, 19]]
    assert any(row["chapter"] == 13 and row["place"] == "悬骨桥" for row in result["repeated_arrivals"]["issues"])
