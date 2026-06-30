"""Arena-proven prose prompt fusion rules (position-aware).

These rules distill the 2026-06 prose prompt arena + 2026-06-29 increment-validation
(red-line gate + claude pairwise blind judges) results into a compact PROSE_SCENE block.
They intentionally avoid methodology labels and tell the writer what must appear on the page.

Position-aware additions (blind-judge validated):
- OPENING chapters get the 开篇炸点律 (前300字"被抖音养坏耐心"判官: 炸点前置/开场即代价 = 100%).
- Non-opening chapters get the 中段持续追读律 (第50章老读者判官: 不可逆推进/成长可见 = 100%,
  plus an anti-巧合堆砌 guard from the judges' high-frequency "强行开挂" complaint).
See docs/正文质量-通用题材框架-最终报告-20260629.md.
"""

from __future__ import annotations

# ruff: noqa: RUF001

# 开篇炸点律(仅开篇章) — 盲评:炸点前置O1/开场即代价O3 = 100%(前300字判官)
_OPENING_HOOK_BLOCK = """【开篇炸点律 · 仅开篇章 · 最高优先(管第一句)】
- 第一句就要信息或画面冲击——一句抉择/一个动作/一声冲突；前150字内主角立刻登场并被扔进一个
  具体绝境（当众羞辱/生死关头/身份暴露/暴力冲突），开场就让读者看见具体损失或痛感。
- 严禁用起床、洗漱、天气、景物（如“水晶灯晃得眼睛发疼”）、世界观介绍、回忆铺垫开场。
"""

# 中段持续追读律(非开篇章) — 盲评:不可逆推进M2/成长可见M5 = 100%(第50章老读者判官)
_MID_CHAPTER_BLOCK = """【中段持续追读律 · 非开篇章 · 最高优先】
- 章头三句之内接住上一章的钩子/悬念并立刻推进，不复述前情、不重新介绍人物或世界、不慢热铺垫。
- 单章必须不可逆推进：本章结束时局面发生不可逆变化（获得/失去/暴露/升级/新威胁），绝不回到
  本章开头原点，让读者清楚感到“又往前走了一步”。
- 成长可见：让长期读者看到主角相比之前的阶段性成长（实力/筹码/地位/关系更进一步）。
- 切忌为升级/反转而堆砌巧合、空降外援、强行开挂；一章一个核心反转写透，胜过三四个堆砌的反转。
"""

# 去AI腔铁律 + 横测胜出页面动作(位置无关,任何章 always-on)
_BASE_FUSION_BLOCK = """【叙述结构 · 去AI腔铁律】（最高优先：先满足这一组，再谈下面的页面动作）
读者读小说是为了跟着人物体验过程，不是来看你先报结论。这一场严格做到：
- 不要结论先行/总分总：禁止先抛出判断、情绪标签或场面总结、再用描写去补证。先写正在发生的
  具体动作与感知，结论尽量让读者自己得出，能不说就不说。删掉一切替读者算账、下定论的句子
  （如“他算了一笔账”“命不能拿来垫房租”“脑子里过的是房东、单量、余额”）。
- 不要用“没做什么”当叙事主句（“他没抬头”“他没回头”“他没吭声”）；直接写他此刻实际在做的那个动作。
- 不要为文学感硬造比喻（“像骨头响”“跟心电图似的”“像指甲刮过搪瓷盆底”）；要么不用比喻，
  要么只用这个人物此刻真会联想到、贴他生活经验的东西。
- 逐层透出、不要开场announce：先落进一个正在进行的具体动作，让读者跟着人物做事，
  再把环境、旁人、不对劲之处一层层慢慢透出来——是逐步发现，不是开场就给结论。
- 句子向前流动地叙事，不要为了节奏感切成一连串短促独行句。

【横测胜出融合写法 · 正文硬约束】
在满足上面叙述结构铁律的前提下，本场再执行这些页面动作，不要写出“黄金三章/爽点/去AI味”等方法论名词：
- 开场即放出不可逆代价或倒计时：读者必须在第一屏知道主角为什么不能等。
- 每 300-500 字制造一个来自行动结果的具体问题；解掉一个小问题后，立刻抛出更强问题。
- 主角判断先落到手、眼、呼吸、步伐、停顿、触感，再给一句以内判断；少写“他意识到/他明白了”。
- 每个关键动作必须碰到具体地点、道具、规则或人物反应；禁止用泛词替代已给定物料。
- 爽点必须写完整四拍：压迫 → 选择 → 执行 → 反馈；
  反馈要来自环境、对手、旁观者或规则系统，不要只写“震惊”。
- 围绕本场最强画面写开中结：开头埋视觉部件，中段推进，结尾兑现、反转，或让该画面变成下一场钩子。"""

# 非开篇位置(用中段持续追读律)
_MID_POSITIONS = frozenset({"early", "midgame", "climax", "endgame"})


def render_prose_prompt_fusion_block(
    *, language: str = "zh-CN", position: str | None = None
) -> str:
    """Render scene-level hard prose actions proven useful by prompt arena.

    ``position`` is a ``ChapterPosition`` value (opening/early/midgame/climax/endgame).
    Opening chapters prepend the 开篇炸点律; non-opening chapters prepend the
    中段持续追读律. Unknown/None falls back to the position-invariant base only.
    """

    if str(language or "").lower().startswith("en"):
        return ""
    pos = str(position or "").lower()
    if pos == "opening":
        return _OPENING_HOOK_BLOCK + "\n" + _BASE_FUSION_BLOCK
    if pos in _MID_POSITIONS:
        return _MID_CHAPTER_BLOCK + "\n" + _BASE_FUSION_BLOCK
    return _BASE_FUSION_BLOCK


__all__ = ["render_prose_prompt_fusion_block"]
