"""Review assertions must match the cited chapter, not another chapter."""

from tools.verify_review import verify, table


def test_quotes_chinese_numbers_ranges_and_chapter_scope(tmp_path):
    (tmp_path / "final").mkdir()
    (tmp_path / "chapters").mkdir()
    (tmp_path / "final/chapter-001.md").write_text("他推开门。真正的话。")
    (tmp_path / "chapter-002.body.md").write_text("第1章才有的句子。")
    (tmp_path / "chapters/001.json").write_text('{"title":"开门"}')
    rows = verify(tmp_path, "第 一 章：“他推开门”；第 2 章：“真正的话”。\n第三章节奏太慢。\n第 1 章《开门》。")
    assert [r["status"] for r in rows] == ["核实", "找不到", "无引文", "核实"]
    assert "找不到" in table(rows)


def test_title_list_checks_each_chapter_and_catalog_literal(tmp_path):
    (tmp_path / "chapters").mkdir()
    for number, title in [(7, "谁配领人"), (9, "谁握着矿道"), (50, "巨眼之外")]:
        (tmp_path / f"chapters/{number:03d}.json").write_text('{"title":"' + title + '"}')
    rows = verify(
        tmp_path,
        "第 7 章《谁配领人》；第 50 章《巨眼之外》。\n"
        "目录：`第 7 章1谁配领人`、`第 50 章1巨眼之外`。\n"
        "第 9 章叫《拿稳你的锤》，正文叫《谁握着矿道》。",
    )
    assert len([r for r in rows if r["status"] == "找不到"]) == 3
    assert any(r["quote"] == "谁握着矿道" and r["status"] == "核实" for r in rows)


def test_postfix_chapter_lists_override_previous_quote_scope(tmp_path):
    (tmp_path / "chapters").mkdir()
    for n, title in [(1, "闸门"), (2, "阵图"), (3, "一掌")]:
        (tmp_path / f"chapters/{n:03d}.json").write_text('{"title":"' + title + '"}')
    rows = verify(tmp_path, "章名：“闸”（第1章）、“阵图/图”（第2章）、“一掌”（第3章）。")
    assert [(r["chapter"], r["status"]) for r in rows] == [(1, "核实"), (2, "核实"), (3, "核实")]
