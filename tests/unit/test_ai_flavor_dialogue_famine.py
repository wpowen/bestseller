"""对话饥饿检测（2026-08-16 两本真机书复发）。

评委盲评把它判成断代级差距（对话维 AI 2 分 vs 人类 7 分）：
    《端盘画神》   全书对话占比 中位 0.0%（主角被设定成哑女）→ 命中 40/50 章
    《破澡堂真话局》中位 1.3%、24/50 章一句对白都没有 → 命中 26/50 章
        ——而它的核心机制就是「人必须当众说真心话」。机制写在设定里，
        正文里没人开口。

语料标定（.distillation_private 1160 章）：
    对话占比 中位 26.5% / p10 7.2% / p5 3.6% / p1 0.3%
    完全没有对话的章只占 1.7%
阈值取 p5=3.6%（真机人类命中率实测 7.0%）：写景/赶路/单人潜行确实存在，
换来对「整本书没人说话」的高召回。

处置：advisory + 计分封顶（缺对白是可读性缺陷不是句法 tell，独立毙章会误伤
那 5% 的人类章）+ 进 deslop 触发集 + 免引文（本来也没有对白可引）。
改法只说「把已有的信息交换改成人物开口」，绝不要求硬塞寒暄。
"""

from __future__ import annotations

import re

from bestseller.services.ai_flavor.detector import detect
from bestseller.services.ai_flavor_gate import DESLOP_DISCOURSE_CATEGORIES
from bestseller.services.deslop_revise import _findings_text


def _cats(text: str) -> list[str]:
    return [s.category for s in detect(text, language="zh").spans]


# 需超过 min_chars=1200（全章口径），×16 ≈ 1344 字
NARRATION = (
    "他把窗关上，回头看了一眼灶台，锅里的水已经开了，白汽顶得锅盖啪啪作响。"
    "院子外面有人挑着担子经过，扁担吱呀吱呀，声音由近及远。"
    "她把抹布搭在盆沿上，转身去添柴，柴堆边上的老猫抬了抬眼皮，没有动。"
) * 16


def test_zero_dialogue_chapter_flags() -> None:
    assert "dialogue_famine" in _cats(NARRATION)


def test_healthy_dialogue_chapter_does_not_flag() -> None:
    """人类中位是 26.5%，正常对白量的章不该命中。"""

    talky = NARRATION + ("「你到底把它放哪儿了？」「灶膛后头，我记着呢。」" * 30)
    assert "dialogue_famine" not in _cats(talky)


def test_threshold_sits_at_human_p5() -> None:
    """阈值 3.6%：略高于它不报、略低于它要报。"""

    base_chars = len(re.findall(r"[一-鿿]", NARRATION))
    line = "「就放在灶膛后头我记着呢你别翻了」"
    n_line = len(re.findall(r"[一-鿿]", line))
    # 目标占比 ~5%（安全线以上）
    over = NARRATION + line * max(1, round(base_chars * 0.05 / max(n_line, 1)))
    assert "dialogue_famine" not in _cats(over)
    # 目标占比 ~2%（安全线以下）
    under = NARRATION + line * max(1, round(base_chars * 0.02 / max(n_line, 1)))
    assert "dialogue_famine" in _cats(under)


def test_short_fragment_exempt_by_min_chars() -> None:
    """min_chars=1200 护栏：场景卡/片段不评（量级失明老坑）。"""

    assert "dialogue_famine" not in _cats("他把窗关上，回头看了一眼灶台。")


def test_in_deslop_trigger_set() -> None:
    assert "dialogue_famine" in DESLOP_DISCOURSE_CATEGORIES


def test_score_is_capped_never_blocks_alone() -> None:
    """封顶合同：人类语料里也有约 5% 的章命中，独立毙章会误伤它们。"""

    report = detect(NARRATION, language="zh")
    assert "dialogue_famine" in [s.category for s in report.spans]
    assert report.overall_score < 38, "对话饥饿不得独立把一章推过 block 阈值"


def test_finding_line_is_fix_first_and_not_truncated() -> None:
    findings, _score, _n = _findings_text(NARRATION, "zh-CN")
    line = next(ln for ln in findings.split("\n") if "dialogue_famine" in ln)
    assert "改法" in line
    assert "不要硬塞寒暄" in line, "必须明确禁止硬塞寒暄，否则模型会灌水对白"


def test_all_quote_styles_are_recognized() -> None:
    """引号风格盲区（2026-08-16 真机《健身房》ch1 抓到）。

    模型会在轮次之间切换引号：v1/v3 用弯引号“”，v2 整章改用直引号 "。
    只认弯引号的正则把 v2 读成「零对话」→ dialogue_famine 误报整章，
    moment_slice 的对白屏蔽也同时失效（三处正则同源，必须一起认）。
    """

    line = "今天什么鬼，你自己看那台配重。"
    for opener, closer in (("“", "”"), ("「", "」"), ("『", "』"), ('"', '"')):
        talky = NARRATION + f"{opener}{line}{closer}" * 30
        assert "dialogue_famine" not in _cats(talky), (
            f"引号风格 {opener}{closer} 未被识别，会造成整章误报"
        )


def test_straight_quotes_count_toward_ratio() -> None:
    """直引号必须真的计入占比，不是只要不报就算过。"""

    from bestseller.services.ai_flavor.detector import _detect_dialogue_famine

    curly = NARRATION + "“今天什么鬼。”" * 20
    straight = NARRATION + '"今天什么鬼。"' * 20
    assert not _detect_dialogue_famine(curly, lang="zh")
    assert not _detect_dialogue_famine(straight, lang="zh")
