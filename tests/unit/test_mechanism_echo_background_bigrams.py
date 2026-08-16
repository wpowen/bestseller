"""跨书机制回声门禁：虚词不是指纹。

2026-08-16 真机事故：一本新书构思被 `CONCEPTION_POLLUTION_GATE_UNRESOLVED`
整体毙掉，撞车判定来自与旧书共享的两个二元组——**「事情」和「所有」**。

人类语料（.distillation_private，3969 章抽样）文档频率：

    一个 95.2% · 自己 89.6% · 事情 56.6% · 所有 53.8%
    ——真正的书指纹——
    真话 0.9% · 画符 0.4% · 澡堂 0.2% · 挂号 0.1%

相差三个数量级。原实现有两处叠加：

1. `_content_bigrams` 名字承诺 content，实现返回**全部** CJK 二元组，零过滤；
2. 唯一的背景过滤要求同一二元组出现在 `>= 3` 个历史条目里 —— 书库只有 2 本
   时**没有任何词能成为背景词**，于是最常见的虚词变成「独特指纹」。

这里锁三件事：虚词不算指纹、逐字照抄仍被抓、换皮不换机制仍被抓。
中间那条最重要——把门禁改瞎比误报更糟，跨书同质化是本项目反复复发的真问题。
"""

from __future__ import annotations

import pytest

from bestseller.services.conception import (
    _ECHO_BIGRAM_MIN_HITS,
    _ECHO_SPAN_MIN_CHARS,
    _background_bigrams_zh,
    _content_bigrams,
    _longest_common_cjk_span,
)


BOOK2 = (
    "破澡堂真话局 真正的摆烂不是不做事，而是让对方自己把事情做完——一个想躺平的人，"
    "只要掌握让别人说真话的规矩，就能不费力地收拾所有上门找茬的人。"
    "一个只想烧水递毛巾的社畜，靠一条祖传死规矩，让进澡堂的人必须说真话。"
)

BLOCKED_SEED = (
    "主角是县医院挂号窗口的临时工，脾气最软，谁插队都让。他手写的《当日就诊顺序册》，"
    "把谁的名字往后挪一行，那人当天在所有事情上都会被排到后面；往前挪则处处有人让路。"
)

VERBATIM_ECHO = (
    "一个只想烧水递毛巾的社畜，靠一条祖传死规矩，让进澡堂的人必须说真话，"
    "不费力地收拾所有上门找茬的人。"
)

RESKINNED_ECHO = "他在理发店立下一条祖传规矩：坐上这把椅子的人必须说真话，说假话就剪坏。"


def _collides(candidate: str, prior: str) -> bool:
    """与 `_mechanism_echo_report` 同源的判定。"""

    shared = _content_bigrams(candidate) & _content_bigrams(prior)
    span = _longest_common_cjk_span(candidate, prior)
    if len(span) < _ECHO_SPAN_MIN_CHARS:
        span = ""
    return bool(span) or len(shared) >= _ECHO_BIGRAM_MIN_HITS


def test_background_list_loads():
    grams = _background_bigrams_zh()
    assert len(grams) > 1000, "背景词表没载入——门禁会退回误报行为"
    assert all(len(g) == 2 for g in grams)


@pytest.mark.parametrize("gram", ["一个", "自己", "事情", "所有"])
def test_function_word_bigrams_are_background(gram):
    """真机毙书用的就是「事情」「所有」这两个。"""

    assert gram in _background_bigrams_zh()
    assert gram not in _content_bigrams(f"他把{gram}都说了")


@pytest.mark.parametrize("gram", ["澡堂", "真话", "挂号", "画符"])
def test_distinctive_terms_survive_as_fingerprints(gram):
    """书指纹绝不能被背景表吃掉，否则门禁就瞎了。"""

    assert gram not in _background_bigrams_zh()
    assert gram in _content_bigrams(f"他走进{gram}的门")


def test_blocked_seed_no_longer_collides():
    """真机事故复现：这颗种子曾因「事情」「所有」被判跨书污染。"""

    shared = _content_bigrams(BLOCKED_SEED) & _content_bigrams(BOOK2)
    assert shared == set(), f"仍有残留伪指纹：{sorted(shared)}"
    assert not _collides(BLOCKED_SEED, BOOK2)


def test_verbatim_copy_still_caught():
    """逐字照抄必须照抓——这是门禁存在的理由。"""

    assert _collides(VERBATIM_ECHO, BOOK2)
    assert len(_longest_common_cjk_span(VERBATIM_ECHO, BOOK2)) >= 10


def test_reskinned_mechanism_still_caught():
    """换场所但照搬「祖传规矩·必须说真话」——最该抓的一类，最容易被改瞎。"""

    assert _collides(RESKINNED_ECHO, BOOK2)
    shared = _content_bigrams(RESKINNED_ECHO) & _content_bigrams(BOOK2)
    assert {"真话", "祖传"} <= shared, f"机制词被背景表误吞：{sorted(shared)}"


def test_small_library_does_not_disable_background_filter():
    """`>= 3 条目` 在 2 本书的库里恒不成立，退化保护取 min(3, max(2, N))。"""

    from bestseller.services.conception import _ECHO_BACKGROUND_ENTRY_COUNT

    for entries, expected in ((0, 2), (1, 2), (2, 2), (3, 3), (9, 3)):
        got = min(_ECHO_BACKGROUND_ENTRY_COUNT, max(2, entries))
        assert got == expected, f"{entries} 个条目时背景阈值应为 {expected}，实为 {got}"
