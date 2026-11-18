"""Instruction 75: defects in chapters 1-3 of the re-test book 《扫雪剑起》 (books/webui/380d3542-…).

ch2 pasted the planning sentence “顾寒再扫七阶，第二招剑痕在眼底成形” into a bridge fight and wrote the
female 沈照雪 as “他” beside her own line “由我沈照雪拿”; ch3 kept ASCII quotes and **bold**; ch2 never
said what happened to the two thieves blocking the lane at the end of ch1.
"""

import json

from bookengine.cast_consistency import cast_findings, repair_cast_consistency
from bookengine.prose_similarity import analyze_prose, with_completed_action_context
from tools.book_checkup import clean_prose

PROMISES = [
    "第1章顾寒扫净第一阶青石，识海剑光当场亮起。",
    "第2章顾寒再扫七阶，第二招剑痕在眼底成形；陆青禾遭散修抢药，他以第一式断草式切断飞索。",
]
BIBLE = dict(
    opening_promises=PROMISES,
    characters=[
        dict(name="顾寒", gender="男", role="protagonist"),
        dict(name="沈照雪", gender="女", role="romance"),
        dict(name="魏皓辰", gender="男", role="support"),
    ],
)


def test_a_pasted_opening_promise_is_marked_for_repair_and_natural_prose_is_not():
    text = "左边那人挥短刃直取他咽喉。顾寒再扫七阶，第二招剑痕在眼底成形。\n\n顾寒没有后退，横臂一格。"
    report = analyze_prose(text, BIBLE)
    assert [row["text"] for row in report["records"] if row["kind"] == "card_rule_copy"] == [
        "顾寒再扫七阶，第二招剑痕在眼底成形。"
    ]
    assert analyze_prose("扫帚落下，青石露出本色，剑影在识海里一闪。", BIBLE)["counts"]["card_rule_copies"] == 0


SELF_NAMED = "马蹄声近。\n\n“旧料都丢了？”他冷眼扫过众人，“首席试剑，原本就该由我沈照雪拿。”\n\n魏皓辰点头。"


def test_a_speaker_who_names_herself_binds_the_pronoun_beside_her_line():
    rows = [row for row in cast_findings(SELF_NAMED, BIBLE, chapter=2) if row["status"] != "Unknown"]
    assert [(row["name"], row["observed"], row["expected"]) for row in rows] == [("沈照雪", "他", "她")]
    quoted = "顾寒问：“他是谁？”\n\n“我沈照雪的剑，你也配看？”顾寒没有答。"
    assert not [row for row in cast_findings(quoted, BIBLE, chapter=2) if row["status"] != "Unknown"]


def test_the_self_named_pronoun_is_fixed_without_the_model():
    repaired, _, failed, note = repair_cast_consistency(
        None, "ch2", SELF_NAMED, {"events": []}, False, {}, BIBLE, {"number": 2},
        lambda *_a, **_k: ({"events": []}, False, None), lambda *_a, **_k: {"edits": []},
    )  # fmt: skip
    assert "她冷眼扫过众人" in repaired and failed is False and note["status"] == "repaired"


def test_ascii_quotes_and_bold_markers_are_cleaned():
    text = '"再撑一刻。"她说，"暖的地方就在下头。"\n上头小字：**改列药庐杂役**。\n他说"半句'
    cleaned, counts = clean_prose(text)
    assert cleaned == '“再撑一刻。”她说，“暖的地方就在下头。”\n上头小字：改列药庐杂役。\n他说"半句'
    assert counts["ascii_quotes"] == 4 and counts["bold_markers"] == 1
    assert clean_prose(cleaned)[0] == cleaned


def test_next_writer_gets_the_previous_hanging_ending(tmp_path):
    (tmp_path / "chapter-001.body.md").write_text("顾寒追到窄道。")
    (tmp_path / "writing_outlines").mkdir()
    ending = dict(ending_type="迫近危险", ending_content="两名盗修堵住窄路，一人准备放倒正在扫雪的杂役")
    (tmp_path / "writing_outlines/001.json").write_text(json.dumps(ending, ensure_ascii=False))
    sections = dict(with_completed_action_context([("上章结尾", "旧事实")], tmp_path, 2))
    assert "两名盗修堵住窄路" in sections["上章结尾"] and "怎样收场" in sections["上章结尾"]
    flat = dict(ending_type="平收", ending_content="众人散去")
    (tmp_path / "writing_outlines/001.json").write_text(json.dumps(flat, ensure_ascii=False))
    assert "众人散去" not in dict(with_completed_action_context([("上章结尾", "x")], tmp_path, 2))["上章结尾"]
