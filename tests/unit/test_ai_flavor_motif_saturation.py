"""默认母题族饱和检测（2026-08-16《破澡堂真话局》定罪）。

与**已退役**的 debt_metaphor_leak 的区别就是它退役的理由：那个把正文里每个
债/账/欠 都当 AI 味标记，于是在一本本来就写债务的书里把故事本身删掉。
出现 ≠ 病；这里测的是**支配**。

语料标定（.distillation_private 969 章，与 anti_default_motif 同一套正则）：
    每千字 中位 0.00 / p90 0.38 / p95 0.56 / p99 1.67 / max 14.88
    子族数 ≥2 的章占 2.8%
判据要求两条同时成立（任一单独都会误伤真写债务的书）：
    ① 每千字 ≥ 人类 p99(1.67)  ② 同时命中 ≥2 个子族

真机验证：喜剧书 14/50 命中、玄幻书 4/50、人类语料误报 1/293 = 0.3%。
处置：advisory + 计分封顶（永不单独毙章）+ 进 deslop 触发集 + 免引文。
"""

from __future__ import annotations

import re

from bestseller.services.ai_flavor.detector import detect
from bestseller.services.ai_flavor_gate import DESLOP_DISCOURSE_CATEGORIES
from bestseller.services.anti_default_motif import (
    user_intent_is_motif_dominant,
    user_requested_debt,
)
from bestseller.services.deslop_revise import _findings_text


def _cats(text: str) -> list[str]:
    return [s.category for s in detect(text, language="zh").spans]


# 干净叙述填充，撑过 min_chars=1200 且不含任何母题词
PAD = (
    "他把窗关上，回头看了一眼灶台，锅里的水已经开了，白汽顶得锅盖啪啪作响。"
    "院子外面有人挑着担子经过，扁担吱呀吱呀，声音由近及远。"
    "她把抹布搭在盆沿上，转身去添柴，柴堆边上的老猫抬了抬眼皮，没有动。"
) * 8


def test_two_families_over_p99_flags() -> None:
    # 账目族 + 丧葬族同时在场且密度越过人类 p99
    text = PAD + ("他翻开账簿，记下欠的那一笔。灵堂里的棺木还没合上。" * 8) + PAD
    assert "motif_saturation" in _cats(text)


def test_single_family_never_flags_however_dense() -> None:
    """真写债务的书：一族再密也不报——这正是 debt_metaphor_leak 的死因。"""

    text = PAD + ("他翻开账簿，把欠款一笔笔记上，账上又添了新债。" * 12) + PAD
    assert "motif_saturation" not in _cats(text)


def test_two_families_below_p99_does_not_flag() -> None:
    """两族都在场但只是背景（密度在人类区间内）→ 不报。"""

    text = PAD + "他翻开账簿看了一眼。村口的坟头刚长出新草。" + PAD
    assert "motif_saturation" not in _cats(text)


def test_short_fragment_exempt_by_min_chars() -> None:
    """min_chars=1200 护栏：短卡片不计速率（量级失明老坑）。"""

    text = "账簿摊开，欠条压在棺盖上，灵堂里没人说话。"
    assert "motif_saturation" not in _cats(text)


def test_clean_prose_no_hit() -> None:
    assert "motif_saturation" not in _cats(PAD)


def test_dialogue_is_masked() -> None:
    """对白里的母题词不计入叙述层密度。"""

    quoted = PAD + ("「账簿呢？棺材本都在里头。」" * 10) + PAD
    assert "motif_saturation" not in _cats(quoted)


def test_in_deslop_trigger_set() -> None:
    assert "motif_saturation" in DESLOP_DISCOURSE_CATEGORIES


def test_score_is_capped_never_blocks_alone() -> None:
    """封顶合同：母题饱和测的是题材内容不是句法 tell，独立推高分数会把
    一本真写债务的书推进 block→重写，正是退役那次的死因。"""

    text = PAD + ("他翻开账簿，记下欠的那一笔。灵堂里的棺木还没合上。" * 20) + PAD
    report = detect(text, language="zh")
    assert "motif_saturation" in [s.category for s in report.spans]
    assert report.overall_score < 38, "母题饱和不得独立把一章推过 block 阈值"


def test_finding_line_carries_no_motif_tokens() -> None:
    """种词铁律：给写手的 finding 不许含母题 token，只给类别+改法。"""

    text = PAD + ("他翻开账簿，记下欠的那一笔。灵堂里的棺木还没合上。" * 8) + PAD
    findings, _score, _n = _findings_text(text, "zh-CN")
    line = next(ln for ln in findings.split("\n") if "motif_saturation" in ln)
    assert not re.search(r"账簿|欠条|灵堂|棺", line), f"finding 含母题 token：{line}"
    assert "改法" in line


def test_intent_exemption_has_a_ceiling() -> None:
    """顺口一提 ≠ 用户要写这个题材（真机《破澡堂真话局》种子）。"""

    casual = {"premise_seed": "追债的、逼婚的、抢房的亲戚全挤进来泡澡"}
    assert user_requested_debt(casual) is True  # 旧语义：出现即豁免
    assert user_intent_is_motif_dominant(casual) is False  # 新判据：只算一提

    real = {"premise_seed": "主角替亡母讨一笔旧账，灵堂上开棺认债，一笔一笔清算"}
    assert user_intent_is_motif_dominant(real) is True

    explicit = {"allow_debt_theme": True}
    assert user_intent_is_motif_dominant(explicit) is True
