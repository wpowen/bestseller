"""末句强标点集漏了半角（2026-08-20 真机校准）。

`_ENDING_STRONG_PUNCT_ZH = "！？…"` 只有全角。501 篇真实出版章的原文
最后一个字符分布：

    。232 / ”93 / )32 / **!25** / .18 / **?10** / …

以全角「！？…」收尾的是 **0/501**，而真正用了感叹/问号的 35 篇**全是半角**。
于是这一维对所有人恒为 0，人类得分分布 {0:1, 1:140, 2:330, 3:30}
**从来没有 4 分**——一个谁都拿不到的维度不是判据，是常数扣分。

只补半角（纯扩集，不放宽任何别的判据）：这一维仍然歧视句号收尾，
所以门的松紧几乎不变——我们本来就 19/21 合格，人类 28.1% 不合格。
本次修的是**正确性**，不是收益。
"""

from __future__ import annotations

import pytest

from bestseller.services.chapter_validator import _score_ending_sentence

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("tail", ["！", "？", "…", "!", "?"])
def test_both_widths_count_as_strong_punctuation(tail: str):
    score, notes = _score_ending_sentence(f"下一个被换的是谁{tail}", "zh-CN")
    assert not any("不是强标点" in n for n in notes), notes


def test_period_is_still_not_strong():
    _, notes = _score_ending_sentence("他把簿册合上了。", "zh-CN")
    assert any("不是强标点" in n for n in notes)
