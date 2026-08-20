"""钩子回声幻影 token（2026-08-20 真机《罚我守坟》定罪）。

真机 21 章实测平均覆盖率 **75.2%**（门槛 0.5 / 榜单基线 0.65），看着很健康。
但撑起这个数字的 token 是：

    '钟楼'(主角名)  出现7 回声7 = 100%
    '第二'          出现5 回声5 = 100%
    '第三/第四/第五' 出现5 回声4 =  80%
    '执事'          出现4 回声4 = 100%
    '脚步声'        出现4 回声4 = 100%
    '钟楼没'        出现4 回声1 =  25%   ← 反向噪声，制造假 miss

裸序数「第二/第三/第四」被 `_DIALOGUE_NAME_RE` 当成人名抽成钩子 token，
下一章只要出现「第二」就算兑现——**结构上不可能失败**。
与 2026-07-26 定罪的「裸字『门』使 91% 钩子为幻影」同形：
词表每项必须是事件，不能是名词，更不能是序数词。

`钟楼没` 则是相反方向的病：正则 `([一-鿿]{2,4})[说道…]` 贪婪吃进否定词
「没」，把「钟楼没说话」抽成人名「钟楼没」。它只有 25% 被回声，
制造的是**假 miss**。注意 stopword 表里已经有一条 "今天没" ——
说明有人见过这个病，但补的是**实例**不是**形状**。
"""

from __future__ import annotations

import pytest

from bestseller.services.hook_echo_gate import extract_hook_tokens

pytestmark = pytest.mark.unit


def test_bare_ordinals_are_not_hook_tokens():
    text = (
        "钟楼蹲在坟前数着日子。第二天他又来了，第三天照旧。"
        "第四说了句什么，他没听清。" * 12
    )
    tokens = extract_hook_tokens(text)
    assert not any(t.startswith("第") and len(t) <= 3 for t in tokens), tokens


def test_negation_fragment_is_not_a_name():
    text = ("钟楼没说话，只把簿册合上。吴六没答，脚步声停在门口。" * 15)
    tokens = extract_hook_tokens(text)
    assert "钟楼没" not in tokens
    assert "吴六没" not in tokens


def test_real_name_still_extracted():
    text = ("苗青灯笑了一声，把灯挪开。“你认得他？”季伯常问。" * 15)
    tokens = extract_hook_tokens(text)
    assert "苗青灯" in tokens or "季伯常" in tokens
