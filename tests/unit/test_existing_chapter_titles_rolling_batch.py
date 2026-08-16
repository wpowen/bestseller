"""滚动章纲下的跨批次标题查重（2026-08-16《端盘画神》定罪）。

真机现象：第 3 章与第 50 章都叫「油渍」，读者会当成点错了章。

查证顺序（先证伪再动手，避免造第二套查重）：
  1. `_normalize_generated_outline_titles_or_fail` 本身是**对的** —— 精确重名
     与短标题都能拦下并抛 TitleCollisionError（见下 test_dedup_itself_works）。
  2. 三个调用点**都传了** existing_titles。
  3. 真因在数据源：两章同属第 1 卷（该书全书只有 1 卷），而
     `_fetch_existing_chapter_titles(exclude_volume_number=volume_number)`
     把整卷都排除了 —— 这个排除是给「整卷重规划」设计的（旧章即将被替换，
     不算前作），但滚动章纲每批都在扩*同一卷*，于是生成第 43-50 章那批时
     已经写完的 1-42 章全被清出对照集，查重函数拿到的是空前作。

修：同卷但**正文已写出来**（current_word_count > 0）的章仍算前作 ——
已落地成文的章不可能"即将被替换"。
"""

from __future__ import annotations

from uuid import uuid4

from bestseller.services.planner import (
    TitleCollisionError,
    _fetch_existing_chapter_titles,
    _normalize_generated_outline_titles_or_fail,
)


def test_dedup_itself_works_on_exact_and_short_titles() -> None:
    """查重函数本身没坏 —— 修错地方之前先证伪这一条。"""

    for title in ("油渍", "旧桌裂纹"):
        chapters = [{"chapter_number": 50, "title": title}]
        try:
            _normalize_generated_outline_titles_or_fail(
                chapters, logical_name="t", existing_titles=[(3, title)]
            )
        except TitleCollisionError:
            continue
        raise AssertionError(f"重名「{title}」未被拦下")


def test_unrelated_title_passes() -> None:
    chapters = [{"chapter_number": 50, "title": "油渍"}]
    _normalize_generated_outline_titles_or_fail(
        chapters, logical_name="t", existing_titles=[(3, "抹布")]
    )


def _where_sql(exclude: int | None) -> str:
    """编译出 fetcher 的 WHERE 子句文本（不需要真库）。"""

    import asyncio

    captured: dict[str, object] = {}

    class _FakeResult:
        def all(self) -> list:
            return []

    class _FakeSession:
        async def execute(self, stmt):  # noqa: ANN001
            captured["stmt"] = stmt
            return _FakeResult()

    asyncio.run(
        _fetch_existing_chapter_titles(
            _FakeSession(), uuid4(), exclude_volume_number=exclude
        )
    )
    return str(captured["stmt"])


def test_written_chapters_survive_the_volume_exclusion() -> None:
    """同卷已写正文的章必须留在对照集 —— 这就是真机那次漏网的形状。"""

    sql = _where_sql(1)
    assert "current_word_count" in sql, (
        "整卷排除必须给「正文已写出来」的章留后门，否则滚动批次下"
        "本卷已完成的章会被清出对照集，跨批次重名再次不可见"
    )


def test_no_exclusion_path_is_unchanged() -> None:
    """不传 exclude 时行为不变（不引入新条件）。"""

    sql = _where_sql(None)
    assert "current_word_count" not in sql
    assert "volume" not in sql.lower()
