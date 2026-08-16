"""reader_judge 的 payoff_density 判据必须编码「兑现」的三段律（2026-08-16）。

来源：三类爽文读者（番茄免费男频 / 起点付费老书虫 / 女频）独立盲评后的**唯一
共识**——三人在别的维度吵得很凶（憋屈耐受 1.5 章 vs 5 章、反派智商下限、
是否接受「安静地赢」全都相反），但这条完全一致：

    「赢」必须 ①落到一个具体的人脸上 ②被具体的人看见
             ③在主角账上留下一笔能带走的东西

三件缺任何一件，文笔再好都会被判「这章不爽」。读者原话：
    「'某个势力'不算」「「众人震惊」四个字不算变化」「爽了三秒，账上是零」

原判据只写「是否有真实兑现（揭示/对抗结果/代价）」——太糊，判官可以拿
「主角心里冷笑了一下」当兑现。锐化后三条都要能被指认。

⚠️ 这条律是**跨档通用**的（三档读者共识），所以放在通用判官里而不是新建
爽文专用判官——避免同一事实住两地。
"""

from __future__ import annotations

from bestseller.services.reader_judge import _SYSTEM_PROMPT


def test_payoff_criterion_encodes_all_three_parts() -> None:
    """三段律逐条可指认，不是笼统的「真实兑现」。"""

    # ① 落到具体的人
    assert "有名字的对象" in _SYSTEM_PROMPT
    # ② 被具体的人看见（且点名反模式）
    assert "众人震惊" in _SYSTEM_PROMPT, "必须点名「众人震惊」这类概括不算数"
    # ③ 账上留一笔，且强调可延续性
    assert "下一章还能用" in _SYSTEM_PROMPT


def test_payoff_criterion_rejects_emotion_only_payoff() -> None:
    """只有情绪没有账面收益的「爽了三秒」必须被判低分。"""

    assert "只有情绪没有账面收益" in _SYSTEM_PROMPT


def test_prompt_does_not_seed_hype_tokens() -> None:
    """种词铁律：判官 prompt 不得点名爽点 token。

    把「打脸/跪下/求饶/碾压」写进 prompt，模型会朝那几个词写，所有书长成
    同一副样子——用户 2026-08-16 明确指出的正是这个病：内容允许写这些，
    禁止的是把它们当「最优解」注入系统。判据只描述**结构**，不给词。
    """

    for token in ("打脸", "跪下", "求饶", "碾压", "装逼", "扮猪吃虎"):
        assert token not in _SYSTEM_PROMPT, f"prompt 含爽点 token「{token}」= 种词"


def test_judge_stays_disabled_by_default() -> None:
    """判官仍默认关闭——启用是成本与行为的双重变更，须先跑影子校准
    （scripts/reader_judge_shadow_calibration.py）。静默启用是本项目吃过亏的做法。"""

    from bestseller.services.quality_gates_config import ReaderQualityGateConfig

    cfg = ReaderQualityGateConfig()
    assert cfg.enable_llm_reader_judge is False
    assert cfg.reader_judge_audit_only is True
