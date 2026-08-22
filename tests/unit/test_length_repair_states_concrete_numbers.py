"""字数修复 prompt 必须写出具体数字——模型看不见的契约就是不存在的契约。

2026-08-22 真机（ch11）：在架稿 1797 字、下限 1800，差 3 个字整本书停产
路由到机器修复。三轮重写落在 1726 / 1726 / 1797——修复 prompt 说
「按质检结论给出的当前字数与下限的差值一次性补齐」，**但从没把数字写
出来**：当前多少字、目标多少字、要补多少，模型全都看不见。
（同款旧案见 memory writer-underproduces：门禁验的是模型看不见的契约。）

修法两条：
1. 把 `当前 X 字 / 目标 Y 字 / 本轮至少新增 Z 字` 逐字写进 prompt；
2. 补字对齐的是**目标**而不是下限——贴着下限的稿在后续任何修改里都会
   再次跌破，1726→1797 的三轮就是在下限边缘试出来的。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
from types import SimpleNamespace

from bestseller.services.pipelines import _render_chapter_first_local_repair_instructions


def _chapter(*, current: int, target: int) -> SimpleNamespace:
    return SimpleNamespace(
        metadata_json={},
        chapter_number=11,
        target_word_count=target,
        current_word_count=current,
        opening_situation="",
    )


def test_prompt_carries_current_target_and_required_addition() -> None:
    text = _render_chapter_first_local_repair_instructions(
        chapter=_chapter(current=1797, target=2600),
        block_codes=("CHAPTER_LENGTH_BLOCK_LOW",),
        scene_hints=[],
    )
    assert "1797" in text, "当前字数必须逐字可见"
    assert "2600" in text, "目标字数必须逐字可见"
    # 缺口 = 2600 - 1797 = 803：要求补到目标附近，而不是刚过 1800 的下限
    assert "803" in text


def test_small_gap_still_demands_a_meaningful_addition() -> None:
    """差 3 个字也不许只补 3 个字——那正是 ch11 三轮空转的形状。"""

    text = _render_chapter_first_local_repair_instructions(
        chapter=_chapter(current=2597, target=2600),
        block_codes=("LENGTH_UNDER",),
        scene_hints=[],
    )
    assert "2597" in text
    assert "300" in text, "最小新增量兜底，只差几个字也要求一段有实质的新内容"


def test_missing_counts_degrade_to_the_generic_wording() -> None:
    """拿不到字数时退回原有泛化措辞，不编造数字。"""

    text = _render_chapter_first_local_repair_instructions(
        chapter=_chapter(current=0, target=2600),
        block_codes=("CHAPTER_LENGTH_BLOCK_LOW",),
        scene_hints=[],
    )
    assert "补足字数缺口" in text
