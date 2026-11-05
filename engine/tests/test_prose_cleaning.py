"""Deterministic cleaning, including copied dirty lines from the old book."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.book_checkup import clean_prose


def test_copied_old_book_formatting_is_removed_without_changing_prose():
    cases = json.loads((Path(__file__).parent / "fixtures/prose_cleaning_33.json").read_text())
    assert [c["chapter"] for c in cases if c["kind"] == "heading"] == [8, 28, 29, 35, 40, 43]
    for case in cases:
        cleaned, counts = clean_prose(case["text"] + "\n")
        assert cleaned == (case["text"].replace("🏼", "") + "\n" if case["kind"] == "emoji" else "")
        assert counts["total"] == 1


@pytest.mark.parametrize("line", ["# 开场", "###### 旧路", "第二场", "第12幕：夜雨", "---", "***", "* * *", "___"])
def test_standalone_structural_lines_are_removed(line):
    cleaned, counts = clean_prose("她停下。\n" + line + "\n他追来。\n")
    assert cleaned == "她停下。\n他追来。\n"
    assert counts["total"] == 1


def test_preserves_normal_prose_unicode_punctuation_and_layout():
    text = "她说：“第三章写的是我。”\r\n\r\n这是第2场雨。\n编号#3，3 * 4 = 12，温度−5℃。𠀀\n"
    assert clean_prose(text)[0] == text
    assert clean_prose(text)[1]["total"] == 0


def test_internal_numbered_input_tags_are_not_published_as_story_text():
    text = "[P77] 它们没有统一阵形。\n\n[P78] [S3] 他收起刀。\n提到的代号[P12]留在句中。\n"
    cleaned, counts = clean_prose(text)
    assert cleaned == "它们没有统一阵形。\n\n他收起刀。\n提到的代号[P12]留在句中。\n"
    assert counts["paragraph_markers"] == 3
    assert clean_prose(cleaned)[1]["total"] == 0


def test_emoji_sequences_private_use_counts_and_idempotence():
    text = "甲👩🏽‍💻乙🇨🇳丙1️⃣丁\ue000\U000f0001\U00100001。\n"
    cleaned, counts = clean_prose(text)
    assert cleaned == "甲乙丙丁。\n"
    assert counts["emoji"] == 3
    assert counts["private_use"] == 3
    assert counts["total"] == 6
    assert clean_prose(cleaned)[0] == cleaned
    assert clean_prose(cleaned)[1]["total"] == 0


@pytest.mark.parametrize("status", ["polished", "unchanged", "retained", "partial", "original"])
def test_deai_cleans_all_publish_paths_and_records_counts_without_touching_source(tmp_path, status):
    from deai.io import process

    text = "# 混入标题\n[P1] 她抬头🏼看他。\n---\n"
    source = tmp_path / "chapter-001.body.md"
    source.write_text(text)
    client = lambda *a, **kw: SimpleNamespace(calls=[], estimate=lambda: 0)
    polish = lambda *a: SimpleNamespace(text=text, status=status, edits=[])
    assert process(tmp_path, "001", source, None, "local", polish, client, original=status == "original") == status
    report_path = tmp_path / "final/chapter-001.deai.json"
    report = json.loads(report_path.read_text())
    final = tmp_path / "final/chapter-001.md"
    assert final.read_text() == "她抬头看他。\n"
    assert source.read_text() == text
    assert report["cleaning"]["total"] == 4
    assert report["text"] == final.read_text()
    assert (tmp_path / "final/.deai/001/output.md").read_text() == final.read_text()
    before = final.read_bytes(), report_path.read_bytes()
    assert process(tmp_path, "001", source, None, "local", polish, client, preserve=True) == "cached"
    assert (final.read_bytes(), report_path.read_bytes()) == before
