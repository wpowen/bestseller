"""对话饥饿观测器（2026-08-20 真机《罚我守坟》定罪）。

`style_guide.dialogue_ratio` 在两处被**声明进 prompt**（drafts.py / reviews.py
的 PROJECT PROFILE blob，本书声明 0.35），但全库**没有任何一处测量它**——
grep `dialogue_ratio` 只有这两个声明点，零检测器、零门、零留痕。

真机 21 章实测中位 **6.1%**，其中 ch08/ch20/ch21 分别 0.8% / 0.7% / 1.0%，
即整章几乎没有人开口说话。人类出版章校准（.distillation_private
1526 章 / 400 本，同一正则）：p05=1.4% p10=3.1% p25=9.3% 中位=20.7%。

阈值不按分位数拍脑袋，按**实测误报率**选：另一份 400 章独立人类抽样上
1.4%→7.5% 误报、1.0%→4.2%、0.8%→2.8%。取 0.8%，只报「整章确实没有
人开口」（本书 21 章里只有 ch20 命中）。warn-only + 进 advisory 封顶集，
**不进 deslop 触发集**——去水器换的是措辞，凭空造对话是内容捏造。
本观测器只挣留痕。

注意它只看得见极端尾部：真正的差距是**分布级**的（我们中位 6.1% vs
人类 20.7%），那需要书级量具，不是章级下限探针。
"""

from __future__ import annotations

import pytest

from bestseller.services.ai_flavor.detector import detect

pytestmark = pytest.mark.unit


def _narration(n: int) -> str:
    return ("他沿着坟道往下走，脚底的碎石翻过来又落回去，簿册夹在腋下。" * n)


def test_chapter_without_anyone_speaking_is_flagged():
    text = _narration(40)
    result = detect(text, language="zh-CN")
    cats = {s.category for s in result.spans}
    assert "dialogue_starvation" in cats
    span = next(s for s in result.spans if s.category == "dialogue_starvation")
    assert span.severity == "warn"
    assert "0.0%" in span.why or "%" in span.why


def test_normal_dialogue_share_is_clean():
    body = _narration(30)
    talk = "「你是谁？」他问。「守坟的。」那人答。「值守簿上没有你的名字。」" * 12
    result = detect(body + talk, language="zh-CN")
    assert not any(s.category == "dialogue_starvation" for s in result.spans)


def test_short_text_is_not_measured():
    result = detect(_narration(3), language="zh-CN")
    assert not any(s.category == "dialogue_starvation" for s in result.spans)


def test_not_wired_into_rewrite_trigger_set():
    """新观测器只挣留痕：不得进 deslop 触发集，也不得夺任何杀权。"""
    import inspect

    from bestseller.services import ai_flavor_gate, deslop_revise

    assert "dialogue_starvation" not in inspect.getsource(deslop_revise)
    assert "dialogue_starvation" not in inspect.getsource(ai_flavor_gate)


def test_never_a_block_driver():
    """与 inner_voice_absence 同族：进 advisory 封顶集，不得独立推高分数。"""
    from bestseller.services.ai_flavor import detector

    src = detector.__file__
    import inspect

    body = inspect.getsource(detector._score)
    assert '"dialogue_starvation",' in body, "必须在 _ADVISORY_STRUCTURAL 封顶集里"
