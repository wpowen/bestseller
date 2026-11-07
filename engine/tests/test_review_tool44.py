"""Instruction 44: external review packaging, verification and reporting."""

import json

import pytest

from tools.review_pack import create_reading_pack, parse_chapter_spec


def _book(root):
    (root / "final").mkdir(parents=True)
    (root / "chapters").mkdir()
    (root / "story_bible.yaml").write_text(
        "title: 《匿名测试书》\nmodel: MiniMax-M3\ninternal_check: should-not-leak\n",
        encoding="utf-8",
    )
    for number, title, body in (
        (1, "门后", "第一章正文。"),
        (2, "雨夜", "第二章正文。"),
        (3, "回声", "第三章正文。"),
    ):
        (root / f"chapters/{number:03d}.json").write_text(
            json.dumps({"title": title, "model": "internal"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (root / f"final/chapter-{number:03d}.md").write_text(
            f"# 第{number}章 错误的内部标题\n\n"
            "<!-- model: MiniMax-M3 -->\n"
            "[内部状态：去 AI 未完成]\n"
            "> 检查标记：unknown\n"
            f"{body}\n",
            encoding="utf-8",
        )
    return root


def test_parse_chapter_spec_supports_ranges_and_deduplicates():
    assert parse_chapter_spec("3,1-2,2,5") == [1, 2, 3, 5]
    with pytest.raises(ValueError, match="章段"):
        parse_chapter_spec("3-1")


def test_anonymous_pack_contains_only_reader_facing_fields(tmp_path):
    book = _book(tmp_path / "book")
    output = tmp_path / "run" / "reading-pack.md"

    result = create_reading_pack(book, output=output, chapter_spec="2-3")

    text = output.read_text(encoding="utf-8")
    assert result["chapters"] == [2, 3]
    assert text.startswith("# 匿名测试书\n")
    assert "## 第2章 雨夜" in text
    assert "## 第3章 回声" in text
    assert "第一章正文" not in text
    assert "第二章正文。" in text
    for forbidden in ("MiniMax", "内部", "检查标记", "unknown", str(book), "错误的内部标题"):
        assert forbidden not in text


def test_anonymous_pack_must_be_written_under_system_temp(tmp_path):
    book = _book(tmp_path / "book")
    with pytest.raises(ValueError, match="系统临时目录"):
        create_reading_pack(book, output="/var/not-system-temp/review.md")
