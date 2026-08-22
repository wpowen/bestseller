"""跨书机制回声：一个共享短语只能算一份证据，不是三份。

2026-08-22 真机定罪：《书院笔仙》完本后，用**同参数**建第二本玄幻书，
构思被 `CONCEPTION_POLLUTION_GATE_UNRESOLVED` 整体毙掉。阻断证据是：

    shared_span    : ""                        ← 没有任何连续共享片段
    shared_bigrams : ["杂役", "逆袭"]
    重试那次       : ["废柴", "打脸", "柴逆", "逆袭"]

两个问题：

1. **「柴逆」不是词**。`_content_bigrams` 是滑动 2-gram，「废柴逆袭」
   会产出「废柴」「柴逆」「逆袭」三条——一个共享短语被数成三份独立
   证据，命中数虚高 3 倍。定罪门槛是 2，虚高的计数直接把门槛冲破。
2. 「杂役」「逆袭」「废柴」「打脸」是**玄幻题材通用词**，不是机制。
   代码注释自己写着「Two generic bigrams (例如 血脉/觉醒/逆袭) must
   never turn a user-selected genre trope into cross-book pollution」——
   但那条护栏只给了 platform_cliche 条目，真实前作书走的是另一条路。

这是同一个 bug 的第二次复发（注释里记着 2026-08-16 那次：新书因与旧书
共享「事情」「所有」被毙）。上次只修了「两本都出现的词算背景」，没修
「只有一本同题材前作时，那本书的题材词就是它的指纹」。

修法两条，都不放过真污染：
* 重叠的 bigram 合并成一个片段再计数——它们本来就不是独立证据；
* 没有连续共享片段（span）时，门槛提高：无 span 意味着没有任何一段
  机制描述被复制，光靠零散двух字词不足以定罪（两票定罪铁律）。
"""

from __future__ import annotations

# ruff: noqa: RUF002, RUF003 — 中文标点是刻意的。
from bestseller.services.conception import merge_overlapping_bigrams


def test_overlapping_bigrams_from_one_phrase_count_as_one() -> None:
    """「废柴逆袭」→ 废柴/柴逆/逆袭：一个短语，一份证据。"""

    assert merge_overlapping_bigrams(["废柴", "柴逆", "逆袭"]) == ["废柴逆袭"]


def test_disjoint_bigrams_stay_separate() -> None:
    assert merge_overlapping_bigrams(["杂役", "逆袭"]) == ["杂役", "逆袭"]


def test_mixed_case_merges_only_the_overlapping_run() -> None:
    merged = merge_overlapping_bigrams(["废柴", "柴逆", "逆袭", "打脸"])
    assert merged == ["废柴逆袭", "打脸"]


def test_merge_is_order_independent_and_stable() -> None:
    a = merge_overlapping_bigrams(["逆袭", "废柴", "柴逆"])
    b = merge_overlapping_bigrams(["废柴", "柴逆", "逆袭"])
    assert a == b == ["废柴逆袭"]


def test_empty_and_single_inputs_are_safe() -> None:
    assert merge_overlapping_bigrams([]) == []
    assert merge_overlapping_bigrams(["杂役"]) == ["杂役"]


# ── 门禁本身：无连续片段时不许仅凭零散двух字词定罪 ──────────────────────

from bestseller.services.conception import (  # noqa: E402
    _ECHO_BIGRAM_MIN_HITS,
    _ECHO_BIGRAM_MIN_HITS_WITHOUT_SPAN,
)


def test_no_span_threshold_is_strictly_higher_than_the_with_span_one() -> None:
    """没有任何机制描述被复制时，证据要求必须更高。

    真机那次：span="" 且合并后证据只有 2 份（杂役 / 逆袭），却足以把
    整本书的构思毙掉。合并后的重试那次也只有 2 份（废柴逆袭 / 打脸）。
    """

    assert _ECHO_BIGRAM_MIN_HITS_WITHOUT_SPAN > _ECHO_BIGRAM_MIN_HITS
    assert _ECHO_BIGRAM_MIN_HITS_WITHOUT_SPAN >= 3


def test_the_real_machine_case_would_no_longer_convict() -> None:
    """把真机那两组证据按新口径重算，都不该定罪。"""

    first = merge_overlapping_bigrams(["杂役", "逆袭"])
    retry = merge_overlapping_bigrams(["废柴", "打脸", "柴逆", "逆袭"])
    assert len(first) < _ECHO_BIGRAM_MIN_HITS_WITHOUT_SPAN
    assert len(retry) < _ECHO_BIGRAM_MIN_HITS_WITHOUT_SPAN
