"""一句话故事大纲【前置·严格】闸门（v3）。

为什么需要它（用户原话精炼）：读者只看简介就决定点不点；卖点劝退，后续设定/正文再
好也留不住人。所以必须**先**判「卖点是不是真卖点」，不过则不扩充。

v3 把「故事是否聪明」放在「文案是否想点」之前。核心命门除了反差、钩子、
动机和不可预测，还包括：正常人/角色决策合理性、机制因果闭环、代价内生且不可轻易
规避、忠于用户选择的题材，以及能否真正支撑长篇。没有设置代价不是缺点；为了「显得
有深度」而强行扣命、失忆、掉寿命，才是应被拦下的缺点。
  * 3 条【增益维】(计入加权总分)：情绪承诺(读者一眼知道图什么爽) / 差异化(避红海套路) /
    具体可视(compelling mental picture)。
  * 裁决：任一核心命门 < reject_floor → REJECT(不予扩充)；任一核心命门 < pass_floor，
    或加权总分 < overall_floor → REGENERATE(回炉重写卖点)；全部达标 → EXPAND。

与既有评估的分工（见审计 Part D）：本闸门是【前置熔断器·读者视角】，``premise_appeal_judge``
是【终检·编辑视角 9 维】——互补不冗余。``blurb_appeal_gate`` 只测词形/结构，测不出
「得不偿失」「一眼望到头」「无反差」这些语义硬伤，故必须由本 LLM 读者判官补上。

LLM 判官不可用时默认 **fail-closed**：不能证明一句话大纲成立，就不得建项和规划。纯函数
:func:`decide_logline_action` 是可单测的核心。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

LOGLINE_GATE_JUDGE_TYPE = "logline_gate.v3"

# 9 条核心命门（hard veto）—— 一个能支撑规划的故事核的非协商项。
CORE_AXES: tuple[str, ...] = (
    "contrast_irony",          # 反差张力（Save the Cat: irony 是钩子之核；网文人设反差）
    "click_hook",              # 点击钩子（首句即未知/危机/悬念）
    "motivation_credibility",  # 动机可信（非得不偿失）
    "unpredictability",        # 不可预测（非一眼望到头）
    "protagonist_rationality", # 正常人基线 + 角色基线，不为剧情降智
    "causal_coherence",        # 身份/能力/行动/后果是同一条因果链
    "cost_integrity",          # 代价内生、必要、不可被委托/记录/停用规避
    "genre_fidelity",          # 不把用户选的题材偷换成热门套路
    "serial_sustainability",   # 有可升级的问题链/对手反应，非机械重复扣代价
)
# 3 条增益维（计入加权总分，强烈建议但非一票否决）。
SUPPORT_AXES: tuple[str, ...] = (
    "payoff_promise",          # 情绪承诺/爽感清晰（读者一眼知道图什么）
    "differentiation",         # 差异化（避红海套路、高概念）
    "concrete_picture",        # 具体可视（compelling mental picture）
)
AXIS_KEYS: tuple[str, ...] = CORE_AXES + SUPPORT_AXES

# 加权（和=100）。核心命门权重更高。
_DEFAULT_WEIGHTS: dict[str, float] = {
    "contrast_irony": 8.0,
    "click_hook": 7.0,
    "motivation_credibility": 10.0,
    "unpredictability": 7.0,
    "protagonist_rationality": 12.0,
    "causal_coherence": 12.0,
    "cost_integrity": 12.0,
    "genre_fidelity": 10.0,
    "serial_sustainability": 10.0,
    "payoff_promise": 5.0,
    "differentiation": 4.0,
    "concrete_picture": 3.0,
}


class LoglineAction(str, Enum):
    """卖点闸门裁决。"""

    EXPAND = "expand"          # 全部达标 → 放行，进入世界观/角色/大纲扩充
    REGENERATE = "regenerate"  # 偏弱但可修 → 回炉重写卖点（有界）
    REJECT = "reject"          # 核心命门根本性硬伤 → 不予扩充（升级人工/可见拦截）


@dataclass(frozen=True)
class LoglineGateVerdict:
    """卖点前置闸门的裁决结果。"""

    action: LoglineAction
    scores: dict[str, float]
    overall: float = 0.0                 # 加权总分（0-5）
    reasons: tuple[str, ...] = ()        # 哪条命门为何不过（面向作者/编辑）
    fix_directives: tuple[str, ...] = () # 具体怎么改（回炉时注入重写 prompt）
    llm_used: bool = False
    weakest_axis: str | None = None

    @property
    def should_expand(self) -> bool:
        return self.action is LoglineAction.EXPAND

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "scores": {k: round(float(v), 2) for k, v in self.scores.items()},
            "overall": round(float(self.overall), 2),
            "reasons": list(self.reasons),
            "fix_directives": list(self.fix_directives),
            "llm_used": self.llm_used,
            "weakest_axis": self.weakest_axis,
        }


def load_logline_gate_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """读取 logline_gate 配置（缺省回退到内置安全默认）。"""

    if cfg is not None and "reject_floor" in cfg and "axes" in cfg:
        return cfg  # already-normalised gate config (used internally)
    if cfg is None:
        from bestseller.services.story_appeal import load_story_appeal_config

        cfg = load_story_appeal_config()
    block = cfg.get("logline_gate") if isinstance(cfg, dict) else None
    if not isinstance(block, dict):
        block = {}
    axes = block.get("axes")
    if not isinstance(axes, dict) or not axes:
        axes = _builtin_axes()
    return {
        "enabled": bool(block.get("enabled", True)),
        "block_expansion": bool(block.get("block_expansion", True)),
        "require_llm": bool(block.get("require_llm", True)),
        "fallback_score": float(block.get("fallback_score", 0.0)),
        "reject_floor": float(block.get("reject_floor", 2.5)),
        "pass_floor": float(block.get("pass_floor", 3.5)),
        "overall_floor": float(block.get("overall_floor", 3.6)),
        "max_regen": int(block.get("max_regen", 3)),
        "axes": axes,
    }


def verdict_from_approved_concept_contract(
    contract: dict[str, Any] | None,
    *,
    target_chapters: int,
) -> LoglineGateVerdict | None:
    """Reuse the stricter tournament verdict instead of judging the same hook twice.

    This is intentionally not a fallback score.  It only returns EXPAND when the
    unified contract is structurally valid and contains complete hook and
    seriality judge evidence that still clears the current tournament floors.
    Missing, stale, or sub-floor evidence returns ``None`` so the independent
    logline judge remains fail-closed.
    """

    if not isinstance(contract, dict) or not contract:
        return None
    try:
        from bestseller.services.concept_contract import validate_concept_contract
        from bestseller.services.concept_tournament import load_concept_tournament_config

        if validate_concept_contract(contract, target_chapters=target_chapters):
            return None
        evidence = contract.get("quality_evidence")
        if not isinstance(evidence, dict) or not evidence.get("approved"):
            return None
        hook = evidence.get("hook_judge")
        serial = evidence.get("seriality_judge")
        if not isinstance(hook, dict):
            return None
        cfg = load_concept_tournament_config()
        hook_floors = cfg.get("judge_hard_floors") or {}
        required_hook = {
            "freshness": (">=", float(hook_floors.get("freshness", 6.0))),
            "click": (">=", float(hook_floors.get("click", 7.0))),
            "predictable": ("<=", float(hook_floors.get("predictable_max", 6.0))),
            "character_logic": (">=", float(hook_floors.get("character_logic", 6.0))),
            "mechanism_causality": (
                ">=", float(hook_floors.get("mechanism_causality", 6.0))
            ),
            "genre_fidelity": (">=", float(hook_floors.get("genre_fidelity", 7.0))),
            "plain_language": (">=", float(hook_floors.get("plain_language", 7.0))),
            "story_motion": (">=", float(hook_floors.get("story_motion", 7.0))),
        }
        for axis, (operator, floor) in required_hook.items():
            value = float(hook.get(axis))
            if (operator == ">=" and value < floor) or (operator == "<=" and value > floor):
                return None
        if target_chapters >= 200:
            if not isinstance(serial, dict):
                return None
            serial_floors = cfg.get("seriality_hard_floors") or {}
            for axis in (
                "renewability",
                "escalation",
                "anti_reset",
                "coherence",
                "promise_survival",
                "unit_density",
            ):
                if float(serial.get(axis)) < float(serial_floors.get(axis, 7.0)):
                    return None
    except (TypeError, ValueError, KeyError):
        return None

    scores = {key: 4.0 for key in AXIS_KEYS}
    return LoglineGateVerdict(
        action=LoglineAction.EXPAND,
        scores=scores,
        overall=4.0,
        reasons=("已复用同一冠军的钩子八轴与长篇六轴真实裁判证据。",),
        llm_used=True,
        weakest_axis="concept_contract_evidence",
    )


def _builtin_axes() -> dict[str, dict[str, Any]]:
    """内置兜底 rubric（单一真源仍是 yaml；此处仅防配置缺失）。"""

    labels = {
        "contrast_irony": "反差张力", "click_hook": "点击钩子",
        "motivation_credibility": "动机可信", "unpredictability": "不可预测",
        "payoff_promise": "情绪承诺", "differentiation": "差异化",
        "concrete_picture": "具体可视",
        "protagonist_rationality": "主角决策智力",
        "causal_coherence": "因果闭环",
        "cost_integrity": "代价完整性",
        "genre_fidelity": "题材忠实度",
        "serial_sustainability": "长篇支撑力",
    }
    return {k: {"label": labels[k], "weight": _DEFAULT_WEIGHTS[k]} for k in AXIS_KEYS}


def _weight(cfg: dict[str, Any], key: str) -> float:
    spec = cfg.get("axes", {}).get(key, {})
    if isinstance(spec, dict) and spec.get("weight") is not None:
        try:
            return float(spec["weight"])
        except (TypeError, ValueError):
            pass
    return _DEFAULT_WEIGHTS.get(key, 10.0)


def _axis_label(cfg: dict[str, Any], key: str) -> str:
    spec = cfg.get("axes", {}).get(key, {})
    if isinstance(spec, dict) and spec.get("label"):
        return str(spec["label"])
    return _builtin_axes()[key]["label"] if key in AXIS_KEYS else key


def _weighted_overall(scores: dict[str, float], cfg: dict[str, Any]) -> float:
    total_w = sum(_weight(cfg, k) for k in AXIS_KEYS) or 1.0
    return sum(float(scores.get(k, 0.0)) * _weight(cfg, k) for k in AXIS_KEYS) / total_w


def decide_logline_action(
    scores: dict[str, float], cfg: dict[str, Any] | None = None
) -> LoglineGateVerdict:
    """纯函数：给定 12 维分数 → 裁决 EXPAND / REGENERATE / REJECT（严格）。

    规则（取最严）：
      * 某【核心命门】< reject_floor → REJECT（根本性硬伤，不予扩充）
      * 某【核心命门】< pass_floor，或 加权总分 < overall_floor → REGENERATE
      * 全部核心命门 ≥ pass_floor 且 加权总分 ≥ overall_floor → EXPAND
    """

    gate = load_logline_gate_config(cfg)
    reject_floor = float(gate.get("reject_floor", 2.5))
    pass_floor = float(gate.get("pass_floor", 3.5))
    overall_floor = float(gate.get("overall_floor", 3.6))

    reasons: list[str] = []
    fixes: list[str] = []
    action = LoglineAction.EXPAND
    weakest_key: str | None = None
    weakest_val = 5.1
    # 判官漏掉任何维度都不能被当成“默认合格”，尤其不能漏审人物理性和因果。
    full = {k: float(scores.get(k, 0.0)) for k in AXIS_KEYS}

    # 核心命门：逐维硬卡（reject / regen）。
    for key in CORE_AXES:
        val = full[key]
        if val < reject_floor:
            action = LoglineAction.REJECT
            reasons.append(_reason_for(gate, key, val, hard=True))
            fixes.append(_fix_for(key))
        elif val < pass_floor:
            if action is not LoglineAction.REJECT:
                action = LoglineAction.REGENERATE
            reasons.append(_reason_for(gate, key, val, hard=True))
            fixes.append(_fix_for(key))

    for key in AXIS_KEYS:
        if full[key] < weakest_val:
            weakest_val, weakest_key = full[key], key

    # 增益维不单独否决，只经加权总分起作用（单条弱可被补偿，整体平庸则回炉）。
    overall = _weighted_overall(full, gate)
    if action is LoglineAction.EXPAND and overall < overall_floor:
        action = LoglineAction.REGENERATE
        reasons.append(
            f"[加权总分 {overall:.2f} < {overall_floor}] 各维勉强但整体平庸，未达必点级"
        )
        for key in AXIS_KEYS:  # 指明拖后腿的维（含增益维）以指导回炉
            if full[key] < pass_floor:
                reasons.append(_reason_for(gate, key, full[key], hard=key in CORE_AXES))
                fixes.append(_fix_for(key))

    return LoglineGateVerdict(
        action=action,
        scores={k: float(scores.get(k, pass_floor)) for k in AXIS_KEYS},
        overall=overall,
        reasons=tuple(reasons),
        fix_directives=tuple(dict.fromkeys(fixes)),  # 去重保序
        weakest_axis=weakest_key,
    )


_FIX_DIRECTIVES: dict[str, str] = {
    "contrast_irony": (
        "给卖点注入强【反差/反讽】：身份反差(明×暗)、处境悖论、或「恩人即加害者」式反常识——"
        "Save the Cat 称 irony 是钩子之核，四平八稳的卖点必废。"
    ),
    "click_hook": "把最大的未知/危机/冲突提到第一句，让读者只看一句就想点开；删掉开头的设定铺陈。",
    "motivation_credibility": (
        "重写主角核心动机使其【非做不可】：让代价与收益对等，或把主角逼到没有退路——"
        "杜绝『得不偿失却还反复去做』这类读者不信的设定。"
    ),
    "unpredictability": (
        "埋一个读者预判不到、却又【有铺垫可信】(非天降设定)的反转/悖论，打破"
        "『能力→代价→揪凶手』式一眼望到头的闭环。"
    ),
    "payoff_promise": (
        "让读者一眼看清【图什么】：明确承诺一种情绪满足(爽/悬/虐/治愈/代入)并与题材匹配；"
        "若偏窄(纯虐/纯悬)，考虑加一条大众情绪锚或换更主流的爽点。"
    ),
    "differentiation": (
        "避开该题材写烂的红海套路(赘婿逆袭/废柴觉醒/系统签到…)，换成高概念或新组合，"
        "做到「没人这么写」。"
    ),
    "concrete_picture": (
        "把抽象设定换成具体可视的人/事/画面，让读者脑中能成像；"
        "删形容词堆砌。"
    ),
    "protagonist_rationality": (
        "用第一人称重做决策：先列正常人会尝试的核验、求助、停止、撤退、"
        "委托、记录和后手；若主角不选，必须在设定中证明这些选项为何更贵或无效。"
    ),
    "causal_coherence": (
        "删掉拼贴的职业名词和随机奇观，让主角身份必然导致他能发现问题，"
        "他的行动必然引发对手反制，后果从行动本身长出来。"
    ),
    # 2026-08-03：删掉「禁止随机失忆、扣命、掉寿命、资源债」的点名列举。这条指令
    # 进入【每一次】概念生成 prompt，把"债/命/寿"三个词摆在模型眼前；真机《雾街债主》
    # 通篇债印/卖身契/还债——正是这条禁令自己种下的。只保留正向判据：代价可选，
    # 且必须从能力自身的因果推导。
    "cost_integrity": (
        "代价不是必选槽位；若不能从能力/行动的因果中必然推导，直接删掉。"
        "若保留代价，它必须由这本书的能力机制自身产生，而不是外挂一套可以被"
        "停用、代劳或记录规避的收费装置。"
    ),
    "genre_fidelity": (
        "回到用户选定的题材与子题材；职业、冲突、对手和解决方式都必须属于该题材，"
        "不得因为点击套路把民俗偷换成都市异能、把现实偷换成系统爽文。"
    ),
    "serial_sustainability": (
        "说清长篇中什么在升级：问题链、证据链、对手反应或主角能力边界必须产生"
        "新决策；同一套动作换个对象重复一遍不算升级。"
    ),
}


def _fix_for(key: str) -> str:
    return _FIX_DIRECTIVES.get(key, f"加强维度：{key}")


# Axes that killed three consecutive real book creations (2026-07-21/22, all
# three tasks failed with these as weakest): they judge STORY-level properties
# that are baked at concept-kernel time and cannot be repaired by rewriting the
# logline sentence afterwards — the rescue rewriter is (correctly) forbidden
# from inventing story facts, so a kernel that bakes an irrational opening is
# unrescuable downstream. The only working fix point is generation time.
_STORY_LOGIC_AXES: tuple[str, ...] = (
    "protagonist_rationality",
    "cost_integrity",
    "causal_coherence",
)


def render_story_logic_writer_rules() -> str:
    """Render this gate's story-logic contract for the CONCEPT GENERATOR.

    Same single-source pattern as plain_language and the cliché list: the
    generator must see the exact rubric the gate later enforces, phrased from
    the judge's own ``_FIX_DIRECTIVES`` so the two sides cannot drift. Without
    this, the tournament's own judge passes 人物决策 at its 7.0 floor while
    this gate hard-kills 主角决策智力 at 3.0 — a contract the kernel never saw.
    """

    labels = {
        "protagonist_rationality": "主角决策智力",
        "cost_integrity": "代价完整性",
        "causal_coherence": "因果闭环",
    }
    lines = [
        "【故事逻辑硬门（规划前终审会按这三条一票否决，现在就写对）】",
    ]
    for axis in _STORY_LOGIC_AXES:
        directive = _FIX_DIRECTIVES.get(axis, "").strip()
        if directive:
            lines.append(f"- {labels.get(axis, axis)}：{directive}")
    return "\n".join(lines) + "\n"


_AXIS_HINT: dict[str, str] = {
    "contrast_irony": "卖点四平八稳、无反差/反讽，读者没有「咦?」的一下",
    "click_hook": "开头是设定/背景铺陈，钩子迟到或缺席",
    "motivation_credibility": "主角动机得不偿失/牵强，读者不信他会反复这么做",
    "unpredictability": "套路可一眼望到头，意料之中、无惊喜",
    "payoff_promise": "说不清读者图什么情绪满足，卖点是「文笔好/世界观宏大」这类非承诺",
    "differentiation": "命中该题材红海套路、与头部高度雷同",
    "concrete_picture": "抽象设定罗列、形容词堆砌，读者脑中没有画面",
    "protagonist_rationality": "正常人有更低成本的核验/求助/停止/委托/撤退选项，主角却只为让剧情发生而冒险",
    "causal_coherence": "主角身份、核心机制、行动和后果是概念拼贴，没有同一条因果链",
    "cost_integrity": "代价是随机系统收税，与行动无因果，或能被停用/代劳/记录轻易规避",
    "genre_fidelity": "一句话的主冲突已偏离用户选定题材，被热门异能/打脸/系统套路偷换",
    "serial_sustainability": "除了重复触发能力和扣代价外没有新决策、新对手反应或递进问题，撑不起长篇",
}


def _reason_for(gate: dict[str, Any], key: str, val: float, *, hard: bool) -> str:
    label = _axis_label(gate, key)
    tag = "硬伤" if hard else "偏弱"
    return f"[{label}|{tag} {val:.1f}] {_AXIS_HINT.get(key, '')}"


def _build_reader_judge_system_prompt(cfg: dict[str, Any]) -> str:
    axes = cfg.get("axes", {})
    lines = []
    for tier, keys in (("核心命门(一票否决)", CORE_AXES), ("增益维", SUPPORT_AXES)):
        lines.append(f"## {tier}")
        for key in keys:
            spec = axes.get(key, {}) if isinstance(axes, dict) else {}
            lines.append(
                f"- {key}（{_axis_label(cfg, key)}）：高分=「{spec.get('high', '')}」；"
                f"低分=「{spec.get('low', '')}」"
            )
    rubric = "\n".join(lines)
    return (
        "你是【故事总编+敌对性理性审计员+目标读者】。你的第一任务不是给文案找优点，"
        "而是阻止一句包装漂亮、底层愚蠢的设定进入书籍规划。\n"
        "审查时先做三件事：\n"
        "1. 用第一人称站进主角：正常人会不会先核验、求助、停止、委托、记录、撤退或留后手？"
        "若这些低成本方案能破局，主角仍冒险就是为剧情降智。\n"
        "2. 做因果审计：身份为何让他发现问题，行动为何会引发该后果？代价不是必须有；"
        "若书中有代价，它必须是该行动的必然后果，且不能靠不用/代劳/记录轻易规避，"
        "否则 cost_integrity 最高 1 分。\n"
        "3. 做题材与长篇审计：不得把民俗偷换成都市异能、把现实偷换成系统爽文；"
        "长篇必须靠问题、对手、证据和选择升级，不是同一套动作换个对象重复。\n"
        "只有所有核心命门都成立，才能再评点击力。\n"
        "真卖点的硬指标（Save the Cat 官方 logline 法 + 番茄/起点爆款简介公式）：\n"
        "① 反差/反讽(irony)是钩子之核——必须有强反差/反常识/期待违背；四平八稳=废。\n"
        "② 首句即抛未知/危机，不是背景设定铺陈。\n"
        "③ 动机可信、非得不偿失——读者得信主角会【反复】这么做。\n"
        "④ 走向不可预测、不能一眼望到头；且反转要【有铺垫可信】，非天降设定。\n"
        "⑤ 情绪承诺清晰——读者一眼知道能得到哪种情绪满足(爽/悬/虐/治愈)且与题材匹配。\n"
        "⑥ 差异化——避开该题材写烂的红海套路。\n"
        "⑦ 具体可视——是能在脑中成像的人/事/画面，不是抽象设定罗列。\n"
        f"\n# 评分维度（每维 0-5）\n{rubric}\n"
        "# 评分锚点（毒辣，防虚高）\n"
        "- 5：对标该题材头部，一句话就让人【必点】。4：被钩住、想点。3：合格但平庸，会划走。\n"
        "- 2：犹豫且大概率划走。0-1：直接划走(平淡/套路/动机假/无画面)。\n"
        "禁止因为句子通顺、有惨痛代价、有反转词就给高分。没有代价完全可以得 5 分；"
        "乱塞一个代价则必须低分。\n"
        '只输出严格 JSON：{"scores":{"contrast_irony":n,"click_hook":n,'
        '"motivation_credibility":n,"unpredictability":n,"protagonist_rationality":n,'
        '"causal_coherence":n,"cost_integrity":n,"genre_fidelity":n,'
        '"serial_sustainability":n,"payoff_promise":n,"differentiation":n,'
        '"concrete_picture":n},"why":{...}}'
    )


async def evaluate_logline_gate(
    session: Any,
    settings: Any,
    *,
    logline: str,
    premise: str = "",
    genre: str | None = None,
    sub_genre: str | None = None,
    judge_model_key: str | None = None,
    config: dict[str, Any] | None = None,
) -> LoglineGateVerdict:
    """审查一句话故事大纲；判官失败时默认拒绝进入规划。"""

    gate = load_logline_gate_config(config)
    if not gate.get("enabled", True):
        return LoglineGateVerdict(
            action=LoglineAction.EXPAND,
            scores={k: 5.0 for k in AXIS_KEYS},
            overall=5.0,
        )

    fallback = float(gate.get("fallback_score", 3.7))
    scores: dict[str, float] = {k: fallback for k in AXIS_KEYS}
    llm_used = False
    text = (logline or premise or "").strip()
    if text:
        # Judge UNAVAILABILITY (rate limit, timeout, transient API failure) is an
        # infrastructure fault, not evidence that the story is bad.  Fail-closing
        # a book on the first hiccup killed viable concepts whenever a concurrent
        # run saturated the shared model API.  Retry the judge with backoff and
        # only fail-closed once it is *persistently* unavailable.
        max_judge_attempts = max(1, int(gate.get("judge_retry_attempts", 3)))
        for attempt in range(1, max_judge_attempts + 1):
            try:
                scores = await _run_reader_judge(
                    session, settings, text=text, premise=premise, genre=genre, sub_genre=sub_genre,
                    gate=gate, judge_model_key=judge_model_key, fallback=fallback,
                )
                if not any(float(scores.get(key, 0.0)) > 0.0 for key in AXIS_KEYS):
                    raise RuntimeError("logline judge returned only the configured zero fallback")
                llm_used = True
                break
            except Exception:
                logger.warning(
                    "logline reader judge attempt %d/%d failed",
                    attempt, max_judge_attempts, exc_info=True,
                )
                scores = {k: fallback for k in AXIS_KEYS}
                llm_used = False
                if attempt < max_judge_attempts:
                    await asyncio.sleep(min(2.0 * attempt, 8.0))

    if bool(gate.get("require_llm", True)) and not llm_used:
        return LoglineGateVerdict(
            action=LoglineAction.REJECT,
            scores={k: 0.0 for k in AXIS_KEYS},
            overall=0.0,
            reasons=("一句话大纲判官不可用，无法证明该故事值得进入规划。",),
            fix_directives=("恢复判官后重新生成和审查，不得以默认分放行。",),
            llm_used=False,
            weakest_axis="judge_availability",
        )

    verdict = decide_logline_action(scores, gate)
    return LoglineGateVerdict(
        action=verdict.action,
        scores=verdict.scores,
        overall=verdict.overall,
        reasons=verdict.reasons,
        fix_directives=verdict.fix_directives,
        llm_used=llm_used,
        weakest_axis=verdict.weakest_axis,
    )


async def _run_reader_judge(
    session: Any,
    settings: Any,
    *,
    text: str,
    premise: str,
    genre: str | None,
    sub_genre: str | None,
    gate: dict[str, Any],
    judge_model_key: str | None,
    fallback: float,
) -> dict[str, float]:
    from bestseller.services.llm import LLMCompletionRequest, complete_text

    # complete_text 要求 fallback_response；这里使用全零并显式识别，避免把 fallback
    # 伪装成真实判官结果和 llm_used=true。
    fallback_json = json.dumps(
        {"scores": {k: fallback for k in AXIS_KEYS}}, ensure_ascii=False
    )
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            model_tier="strong",
            system_prompt=_build_reader_judge_system_prompt(gate),
            user_prompt=(
                f"题材：{genre or '未注明'}{('/' + sub_genre) if sub_genre else ''}\n"
                f"一句话故事大纲：\n{text[:800]}\n"
                f"补充故事核（仅用于核对因果，不得用它替一句话圆谎）：\n{premise[:1200]}\n\n"
                "立即输出严格 JSON，所有维度都要给分。"
            ),
            fallback_response=fallback_json,
            prompt_template="logline_gate",
            prompt_version="v3",
            model_catalog_key=judge_model_key,
            metadata={"judge_scope": "logline_gate", "genre": str(genre or "")},
            # MiniMax-M3 emits a <think> block that consumes output budget before the
            # multi-axis JSON verdict; 900 truncated it (finish_reason=length →
            # unparseable → false judge_availability failure). Give think + JSON room.
            max_tokens_override=8000,
        ),
    )
    raw = getattr(completion, "content", None) or getattr(completion, "text", None) or ""
    if raw.strip() == fallback_json:
        raise RuntimeError("logline judge used fallback_response")
    return _parse_scores(raw, fallback)


def _parse_scores(raw: str, fallback: float) -> dict[str, float]:
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        data = json.loads(raw[start : end + 1]) if start >= 0 and end > start else {}
    except Exception:
        data = {}
    sc = data.get("scores", data) if isinstance(data, dict) else {}
    out: dict[str, float] = {}
    for key in AXIS_KEYS:
        try:
            out[key] = max(0.0, min(5.0, float(sc.get(key, fallback))))
        except (TypeError, ValueError):
            out[key] = fallback
    return out
