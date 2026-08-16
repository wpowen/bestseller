"""章纲阶段按章位置定档（2026-08-16 真机定罪：单卷书永远到不了 finale）。

真机现象：两本 50 章书的终章都不是结局。
    《破澡堂真话局》ch50 hook=sudden_reveal —— 全书最后一章还在「突然揭示」
    《端盘画神》   ch50 以一声没来源的尖叫加「木纹还在往外长」收尾
    作家评审原话：「50 章的书没有结局，这是结构层的死刑」

根因：`_volume_outline_prompts` 里阶段是按**卷号**选的——

    if volume_number <= 1:              _method_stage = "opening"
    elif volume_number >= _total_volumes: _method_stage = "finale"

两本书都只有 1 卷，于是 `1 <= 1` 恒真，第 1 章到第 50 章**整本书**都按
「opening」档规划，finale 档永远到不了。而 opening 档推荐的钩子正是
`[urgent_crisis, interrupted_action, sudden_reveal]` —— ch50 拿到的就是它。

卷号只在多卷书里才等价于故事进度；单卷书里它恒等于 1。改按本批次覆盖到的
章位置占全书的比例定档。
"""

from __future__ import annotations


def _stage(
    batch_end: int | None,
    book_total: int,
    *,
    volume_number: int = 1,
    total_volumes: int = 1,
) -> str:
    """与 planner._volume_outline_prompts 中的选档逻辑同形（测试镜像）。"""

    if book_total > 0 and batch_end:
        progress = batch_end / book_total
        if batch_end >= book_total:
            return "finale"
        if progress >= 0.8:
            return "pre_climax"
        if progress <= 0.2:
            return "opening"
        return "middle"
    if volume_number <= 1:
        return "opening"
    if volume_number >= total_volumes:
        return "finale"
    if volume_number == total_volumes - 1:
        return "pre_climax"
    return "middle"


def test_single_volume_book_reaches_finale() -> None:
    """真机形状：单卷 50 章，滚动批次 9/18/26/34/42/50。"""

    stages = [_stage(end, 50) for end in (9, 18, 26, 34, 42, 50)]
    assert stages == [
        "opening",
        "middle",
        "middle",
        "middle",
        "pre_climax",
        "finale",
    ], f"单卷书的阶段曲线不对：{stages}"


def test_last_batch_is_always_finale() -> None:
    """覆盖到最后一章的批次必须是 finale —— 这是「书要有结局」的结构保证。"""

    for total in (20, 50, 120, 400):
        assert _stage(total, total) == "finale"


def test_opening_only_covers_the_real_opening() -> None:
    """修前的病：整本书都是 opening。现在只有前 20% 是。"""

    assert _stage(9, 50) == "opening"
    assert _stage(18, 50) != "opening"
    assert _stage(50, 50) != "opening"


def test_falls_back_to_volume_number_when_bounds_missing() -> None:
    """拿不到章界时退回旧的卷号逻辑（不引入新的失败模式）。"""

    assert _stage(None, 0, volume_number=1, total_volumes=3) == "opening"
    assert _stage(None, 0, volume_number=3, total_volumes=3) == "finale"
    assert _stage(None, 0, volume_number=2, total_volumes=3) == "pre_climax"


def test_multi_volume_book_still_ends_in_finale() -> None:
    """多卷书按章位置同样正确（不依赖卷号）。"""

    assert _stage(400, 400) == "finale"
    assert _stage(40, 400) == "opening"
    assert _stage(340, 400) == "pre_climax"
