"""Instruction 71: no motif family the card never had; no cast-list openings.

Cases come from 《摆摊真的能转运吗》 (books/webui/c353d1f9-…): its prepare retries grew from
0 to 22 debt words, and chapter 1 introduced five people with one identity sentence each.
"""

import json

from bookengine.card_motifs import best_attempt, foreign_motifs, retry_note
from bookengine.config import Book
from bookengine.first_appearances import writer_first_appearance_instruction
from bookengine.identity_statements import book_identity_statements, identity_statements
from bookengine.q1_prepare import prepare
from ideation import read
from test_deliverable import core_development_response
from test_identity import ScriptedClient, bible, ROOT
from test_ideation import card

STALL = dict(
    title="摆摊真的能转运吗", premise="陆青在夜市摆摊时，能看见熟客身上剩余的好运。", hook="他给每个熟客发一张好运单"
)


def test_debt_motif_the_card_never_had_is_found_with_its_sentence():
    raw = dict(bible=dict(antagonists=[dict(name="韩胜", identity="夜市赊账摊主", desire="用欠条压住熟客")]))
    found = foreign_motifs(raw, STALL)
    assert found == {"账债": ["夜市赊账摊主", "用欠条压住熟客"]}
    assert "夜市赊账摊主" in retry_note(found)
    assert foreign_motifs(raw, dict(STALL, hook="他替熟客记一本好运账")) == {}


def test_a_retry_never_leaves_the_book_with_more_foreign_motifs():
    rows = [
        dict(redo=0, foreign={"账债": ["a", "b"]}, mechanism=["rule"]),
        dict(redo=1, foreign={"账债": ["a"]}, mechanism=["rule"]),
        dict(redo=2, foreign={"账债": ["a", "b", "c"]}, mechanism=["rule"]),
        dict(redo=3, foreign={"账债": list("abcdefgh")}, mechanism=["rule"]),
    ]
    assert best_attempt(rows)["redo"] == 1
    same = [dict(redo=n, foreign={}, mechanism=["rule"]) for n in range(4)]
    assert best_attempt(same)["redo"] == 3  # unchanged behaviour when motifs do not differ


def test_prepare_keeps_the_attempt_without_the_debt_motif(tmp_path):
    selected = card(title="雨停之前", premise="码头修伞匠苏小满想救姐姐。", golden_finger_rule="旧伞只能挡一次雨。")
    facts = dict(
        name="苏小满",
        identity="修伞匠",
        setting="码头",
        object="旧伞",
        keywords=dict(identity=["修伞匠"], setting=["码头"], object=["旧伞"], rule=["只能", "一次雨"]),
    )
    clean = bible()
    debt = bible()
    debt["characters"][1]["identity"] = "码头赊账铺老板"
    attempts = [debt, clean, debt, debt]
    responses = [json.dumps(facts, ensure_ascii=False)]
    responses += [json.dumps(dict(bible=b, threads=[]), ensure_ascii=False) for b in attempts]
    responses += [json.dumps(dict(bible=clean, threads=[]), ensure_ascii=False)] * 3
    responses += [
        json.dumps(dict(cost="损失：失去一次闻花香的记忆；心理：不敢认花；等级：1；升级：首次"), ensure_ascii=False)
    ]
    responses.append(core_development_response())
    client = ScriptedClient(tmp_path, responses)
    book = Book("xuanhuan-power-fantasy", 5, 2200, json.dumps(selected, ensure_ascii=False))
    value, _, _ = prepare(client, book, tmp_path, ROOT / "config", design=True)
    assert "赊账" not in json.dumps(value["characters"], ensure_ascii=False)
    report = read(tmp_path / "card-alignment.json")
    assert [row["foreign_motifs"] for row in report["attempts"]] == [["账债"], [], ["账债"], ["账债"]]
    assert report["attempts"][-1]["kept_regeneration"] == 1


CHAPTER_ONE = (
    "纸盒被人一脚踢翻。他今晚没带够押金，若再让韩胜堵住这条巷子，林小夏修钟的旧摊就得往后挪。\n\n"
    "“先等等！”林小夏从旧钟摊后探出身，手里捏着细铜钳。她是夜市里修旧物的摊主，摊位上摆满拆开的怀表。\n\n"
    "“赔？”韩胜叉着腰挡在巷口。他经营夜市赊账摊多年，摊上的熟客大多认他。\n\n"
    "赵千蹲在街边捡纸，闻言抬起头。他是个四处跑腿的青年，腰间挂着相机。\n\n"
    "“她是我妹妹的朋友。”赵千说。"
)
CAST = [dict(name=n) for n in ("林小夏", "韩胜", "赵千")]


def test_cast_list_opening_is_found_and_dialogue_is_not():
    rows = identity_statements(CHAPTER_ONE, CAST)
    assert [row["name"] for row in rows] == ["林小夏", "韩胜", "赵千"]
    assert rows[0]["quote"].startswith("她是夜市里修旧物的摊主")
    assert all(CHAPTER_ONE[row["start"] : row["end"]].strip() == row["quote"] for row in rows)
    assert identity_statements("林小夏推门进来。她是故意的。", CAST) == []
    checkup = book_identity_statements(CAST, [dict(number=1, text=CHAPTER_ONE), dict(number=2, text=CHAPTER_ONE)])
    assert checkup["count"] == 3 and checkup["stacked_chapters"] == [1]


def test_writer_is_no_longer_told_to_introduce_people_by_identity(tmp_path):
    cards = dict(characters=[dict(name="林小夏", identity="夜市修旧物的摊主")])
    text = writer_first_appearance_instruction(tmp_path, 1, cards, dict(scenes=[dict(characters=["林小夏"])]))
    assert "林小夏" in text and "夜市修旧物的摊主" not in text
    assert "不单独写一句交代某人是谁" in text
