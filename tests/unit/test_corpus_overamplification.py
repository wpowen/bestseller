"""语料级词频放大（2026-08-20 真机《罚我守坟》定罪）。

用户原话「AI 味非常非常足，一个字都不想读」。真机读完 21 章最后一行发现
ch10-ch18 **连续九章**都以一个「指方向」的动作收尾。数下来：

    全书「方向」417 次 = 8.10/千字；ch20 = 23.38/千字，ch11 = 21.13。
    人类出版章（400 篇）：中位 0.00、p90 0.54、p99 1.70、**最大值 2.65**。

即 ch20 是人类语料最大值的 8.8 倍，而我们 21 章**没有一章低于人类 p99**。
这不是词汇问题，是**一个词变成了整本书的主导句法装置**——和「破折号列车」
「时刻切片」同族，但严重得多。

现有检测器全瞎：deslop 词表里没有「方向」（它不是 AI 套话，是普通名词）；
母题放大检测器按语义族判，「方向」不属于任何母题族。

所以量具必须**不带词表**：拿 .distillation_private 1200 篇人类章算出
逐 bigram 的密度**最大值**表（全部由语料导出），运行时比对。
判据 = 「本章密度超过人类语料该词最大值」的词个数 ≥ 8。

留出校准（另一颗种子的 343 篇人类章，与建表样本不重叠）：
该计数 中位 0、p90 2、p95 3、p99 5、**max 7**，62% 的章为 0。
取 ≥8 → 人类误报 **0/343**。我们 21 章里 9 章命中（ch10/11/12/13/14/16/19/20/21）。
"""

from __future__ import annotations

import pytest

from bestseller.services.ai_flavor.detector import detect

pytestmark = pytest.mark.unit


def test_directional_saturation_is_caught():
    # 把「方向/朝着/对着/底下」堆到真机 ch20 的量级
    body = (
        "他把簿册往灯下推，灯芯的方向偏了一寸。"
        "石阶底下那道纹朝着东墙的方向裂开，裂口对着他掌心。"
        "他抬手，指尖的方向对着井底下那半张脸。"
    ) * 22
    result = detect(body, language="zh-CN")
    hits = [s for s in result.spans if s.category == "corpus_overamplified"]
    assert hits, "整章被少数几个词占满必须留痕"
    assert hits[0].severity == "warn"
    assert "方向" in hits[0].why


def test_ordinary_prose_is_clean():
    body = (
        "他蹲下去，把香灰拨到一边，纸背的字迹在灯下慢慢显出来。"
        "远处有人咳了一声，接着是脚步，越走越近。"
        "他没抬头，只把那张纸折好，塞进袖子里。"
    ) * 20
    result = detect(body, language="zh-CN")
    assert not [s for s in result.spans if s.category == "corpus_overamplified"]


def test_short_text_is_not_measured():
    assert not [
        s
        for s in detect("他走了过去。", language="zh-CN").spans
        if s.category == "corpus_overamplified"
    ]


def test_never_a_block_driver():
    """与 inner_voice_absence / dialogue_starvation 同族：进 advisory 封顶集。"""
    import inspect

    from bestseller.services.ai_flavor import detector

    assert '"corpus_overamplified",' in inspect.getsource(detector._score)


def test_wired_into_deslop_trigger_set():
    """挣到定向重写：patcher 无静态替换，只有整段重写能拆。

    敢发这个权是因为留出校准误报 0/343，且去掉该 finding 分数确实降 4.0
    （采纳判据认得出来），不会重蹈「抓到了不修」。
    """
    import inspect

    from bestseller.services import ai_flavor_gate

    assert "corpus_overamplified" in inspect.getsource(ai_flavor_gate)
