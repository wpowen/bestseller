"""写手陷入循环、把三行重复 121 遍的稿，不许成为在架稿。

2026-08-22 真机定罪（custom-xuanhuan-1787383584 第 7 章）：

    123 × 「"院试排位抽签，"甲衡说，"签筒换甲家定制，壬字号的号签，作废。"」
    121 × 「甲衡的目光从祝淮的掌心挪到了他左眼底……」
    121 × 「祝淮抬脚往门里走了一步……」

写手陷入三行循环，一直重复到撞 `max_tokens=16384`（真机 output_tokens
恰好 16384），产出 18902 汉字——目标 2600、窗口上限 3500 的 7.3 倍。
这份稿成了 `is_current`、**从未被质量门评估**、最后带质量债出货。
读者会看到同一句话 121 遍。

判据异常干净，中间有巨大间隔：

    《书院笔仙》50 章  一行最多重复 2 次
    退化那一章        重复 123 次

所以阈值取 5 既远高于正常上限、又远低于退化量级，零误报。

⚠️ 按本项目铁律，新检测器**只挣重生和留痕，不发阻断权**：它把退化稿
降级为非在架版本、保留上一版，和既有的「太短的退化稿不许驱逐健康章」
（`_DEGENERATE_REGENERATION_RATIO`）走同一条路，不新增杀权。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002 — 中文标点是刻意的。
from bestseller.services.drafts import max_repeated_line_count

_LINE_A = "甲衡的目光从祝淮的掌心挪到了他左眼底，袖口底下那管竹签筒被捏得往掌心转了半寸。"
_LINE_B = "祝淮抬脚往门里走了一步，中指指尖那粒暗点贴上了残影。"


def test_healthy_prose_has_almost_no_repeated_lines() -> None:
    """人类稿与正常生成稿一行最多重复 2 次——《书院笔仙》50 章实测。"""

    prose = "\n\n".join([_LINE_A, _LINE_B, "他没有回头。", _LINE_A])
    assert max_repeated_line_count(prose) == 2


def test_degenerate_loop_is_detected() -> None:
    prose = "\n\n".join([_LINE_A] * 121 + [_LINE_B] * 121)
    assert max_repeated_line_count(prose) == 121


def test_short_lines_do_not_count() -> None:
    """「他没有回头。」这类短句本来就会复现，不是退化信号。"""

    prose = "\n\n".join(["他笑了。"] * 40 + [_LINE_A])
    assert max_repeated_line_count(prose) == 1


def test_blank_lines_never_count() -> None:
    assert max_repeated_line_count("\n\n\n\n\n") == 0
    assert max_repeated_line_count("") == 0


def test_threshold_sits_between_healthy_and_degenerate() -> None:
    """阈值必须落在实测的两个量级之间：正常 ≤2，退化 ≥121。"""

    from bestseller.services.drafts import DEGENERATE_LINE_REPEAT_LIMIT

    assert 2 < DEGENERATE_LINE_REPEAT_LIMIT < 121
