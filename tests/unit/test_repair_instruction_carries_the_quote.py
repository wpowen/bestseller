"""让模型删掉「那段重复」，却不告诉它是哪一段。

2026-08-24 真机定罪链（每一步都推翻了我前一个假设）：

1. 重复内容是重写被否的**唯一**决定性输入（回执 6 例判定完全一致）。
2. 我以为是写手在重写时制造重复 —— **证伪**：8 例里重写新增 0 次，
   两边都有 6 次、重写修掉 2 次，**重复全是从在架稿继承的**。
3. 我改口猜「能毙但不教」 —— **也证伪**：8/8 的指令都提到了重复，
   6/8 还带明确码。
4. 真正缺的是**引文**：指令只有一句通用配方
   ``CHAPTER_SPLICE_REPEATED_SENTENCE: 合并重复草稿段，只保留一次…``，
   没有一个字说明是哪一段。模型得在两千多字里自己重找，结果 6/8 次没删掉，
   然后因为这个它继承来的、且没被指明位置的缺陷被否决。

而引文**本来就在检测器手上**：``QualityFinding.evidence['text']``（真机 159 次
命中），只是渲染指令时只取了 code + repair_hint 把它丢了。
本项目在别处早已确立「逐项证据引文」，这里补齐。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
from dataclasses import dataclass, field
from typing import Any

import pytest

from bestseller.services import reviews as review_services

pytestmark = pytest.mark.unit


@dataclass
class _Finding:
    code: str
    repair_hint: str = "合并重复草稿段，只保留一次。"
    repair_scope: str = "chapter"
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Report:
    blocking_findings: tuple[_Finding, ...]

    def to_dict(self) -> dict[str, Any]:
        # 真实的 bundle report 会被整份写进 chapter metadata —— 夹具照做，
        # 免得测试走的路径和真机不是同一条。
        return {"findings": [f.__dict__ for f in self.blocking_findings]}


def _render(findings: tuple[_Finding, ...]) -> str:
    review = _make_review()
    out = review_services._merge_chapter_quality_bundle_into_review(
        review, _Report(findings), language="zh"
    )
    return out.rewrite_instructions or ""


def _make_review():
    from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores

    return ChapterReviewResult(
        verdict="rewrite",
        severity_max="high",
        scores=ChapterReviewScores(
            **{name: 0.8 for name in ChapterReviewScores.model_fields}
        ),
        rewrite_instructions="",
    )


def test_the_duplicated_text_is_quoted_in_the_instruction() -> None:
    text = "攥得那只备账册的册页又自个儿翻了一下"
    out = _render((_Finding("CHAPTER_SPLICE_REPEATED_SENTENCE", evidence={"text": text}),))

    assert text in out, out


def test_a_finding_without_evidence_still_renders() -> None:
    """没有引文的发现不得因此消失或报错。"""

    out = _render((_Finding("SOME_CODE"),))
    assert "SOME_CODE" in out


def test_a_too_short_snippet_is_not_quoted() -> None:
    """太短的片段引了也没用，反而是噪声。"""

    out = _render((_Finding("SOME_CODE", evidence={"text": "短"}),))
    assert "原文" not in out


def test_the_quote_is_truncated() -> None:
    """长段落只引开头，避免把整章塞进指令。"""

    long_text = "锯" * 300
    out = _render((_Finding("X", evidence={"text": long_text}),))
    assert "锯" * 60 in out
    assert "锯" * 80 not in out


def test_other_evidence_keys_are_accepted() -> None:
    out = _render((_Finding("X", evidence={"sample": "灶膛里的火舌舔到柴尾的尽头"}),))
    assert "灶膛里的火舌舔到柴尾的尽头" in out


# ── 部署后复验暴露的另一半：只看 evidence['text'] 覆盖不到主要的码 ──────────
#
# 真机分布（书 9，2026-08-24 07:20）：
#   CROSS_CHAPTER_REPETITION          13 次，12 次有 text   ← 第一版能取到
#   CHAPTER_SPLICE_REPEATED_SENTENCE  12 次，**0 次**有 text
#   INTRA_CHAPTER_REPETITION         157 次，仅 2 次有 text
# 后两者把引文嵌在 message 里。第一版对它们是空操作——而 SPLICE 正是当初举例
# 的那条码。下面的夹具用真机原样的 evidence 形状。


def test_splice_code_quotes_from_the_message() -> None:
    """真机形状：CHAPTER_SPLICE_REPEATED_SENTENCE 只有 message，没有 text。"""

    evidence = {
        "gate": "chapter_splice_coherence",
        "path": "line:55",
        "message": "同一句叙事/对白在章内重复出现 2 次：他那双眼睛先从砚台上那卷青布布头上挪到自己袖口",
    }
    out = _render((_Finding("CHAPTER_SPLICE_REPEATED_SENTENCE", evidence=evidence),))

    assert "他那双眼睛先从砚台上" in out, out


def test_a_message_without_a_colon_is_still_usable() -> None:
    out = _render((_Finding("X", evidence={"message": "第51–74段构成重复演绎同一事件节拍的段落簇"}),))
    assert "第51–74段" in out


def test_text_still_wins_over_message() -> None:
    """有 text 时优先用它——它是精确引文，message 带样板话。"""

    out = _render((_Finding("X", evidence={
        "text": "攥得那只备账册的册页又自个儿翻了一下",
        "message": "样板：某某检测器报告如下",
    }),))
    assert "攥得那只备账册" in out
    assert "样板" not in out
