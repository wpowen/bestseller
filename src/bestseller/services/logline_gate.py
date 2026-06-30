"""一句话卖点【前置·严格】闸门 —— 扩充世界观/角色/大纲之前的读者视角硬判别（v2）。

为什么需要它（用户原话精炼）：读者只看简介就决定点不点；卖点劝退，后续设定/正文再
好也留不住人。所以必须**先**判「卖点是不是真卖点」，不过则不扩充。

v2 把判官从 3 轴升级为【研究验证的 7 维 / 两档】，更严格地卡控（依据 Save the Cat
官方 logline 法 + 番茄/起点爆款简介公式 + 框架方法论 ch3/ch4）：
  * 4 条【核心命门】(hard veto，任一不达标即拦)：
      反差张力(irony，Save the Cat 称其为钩子之核) / 点击钩子 / 动机可信(非得不偿失) /
      不可预测(非一眼望到头)。
  * 3 条【增益维】(计入加权总分)：情绪承诺(读者一眼知道图什么爽) / 差异化(避红海套路) /
    具体可视(compelling mental picture)。
  * 裁决：任一核心命门 < reject_floor → REJECT(不予扩充)；任一核心命门 < pass_floor，
    或加权总分 < overall_floor → REGENERATE(回炉重写卖点)；全部达标 → EXPAND。

与既有评估的分工（见审计 Part D）：本闸门是【前置熔断器·读者视角】，``premise_appeal_judge``
是【终检·编辑视角 9 维】——互补不冗余。``blurb_appeal_gate`` 只测词形/结构，测不出
「得不偿失」「一眼望到头」「无反差」这些语义硬伤，故必须由本 LLM 读者判官补上。

LLM 不可用时 **fail-open** 到中性 lean-pass（``fallback_score``），绝不在无判官时误毙
（延续 [[scene-richness-gate-self-harm]] 的「严苛确定性地板是反模式」）。纯函数
:func:`decide_logline_action` 是可单测的核心。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

LOGLINE_GATE_JUDGE_TYPE = "logline_gate.v2"

# 4 条核心命门（hard veto）—— 一个真卖点的非协商项。
CORE_AXES: tuple[str, ...] = (
    "contrast_irony",          # 反差张力（Save the Cat: irony 是钩子之核；网文人设反差）
    "click_hook",              # 点击钩子（首句即未知/危机/悬念）
    "motivation_credibility",  # 动机可信（非得不偿失）
    "unpredictability",        # 不可预测（非一眼望到头）
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
    "contrast_irony": 22.0,
    "click_hook": 18.0,
    "motivation_credibility": 16.0,
    "unpredictability": 14.0,
    "payoff_promise": 12.0,
    "differentiation": 10.0,
    "concrete_picture": 8.0,
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
        # 默认 advisory：闸门照常跑分+持久化+日志，但不硬阻断扩充（沿用 meets_bar
        # block_below_bar=false 的反误毙立场）。真机校准 7 维 floor 后再置 true 硬卡。
        "block_expansion": bool(block.get("block_expansion", False)),
        "fallback_score": float(block.get("fallback_score", 3.7)),
        "reject_floor": float(block.get("reject_floor", 2.5)),
        "pass_floor": float(block.get("pass_floor", 3.5)),
        "overall_floor": float(block.get("overall_floor", 3.6)),
        "max_regen": int(block.get("max_regen", 3)),
        "axes": axes,
    }


def _builtin_axes() -> dict[str, dict[str, Any]]:
    """内置兜底 rubric（单一真源仍是 yaml；此处仅防配置缺失）。"""

    labels = {
        "contrast_irony": "反差张力", "click_hook": "点击钩子",
        "motivation_credibility": "动机可信", "unpredictability": "不可预测",
        "payoff_promise": "情绪承诺", "differentiation": "差异化",
        "concrete_picture": "具体可视",
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
    """纯函数：给定 7 维分数 → 裁决 EXPAND / REGENERATE / REJECT（严格）。

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
    full = {k: float(scores.get(k, pass_floor)) for k in AXIS_KEYS}  # 缺失=中性放行

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
        "把抽象设定换成具体可视的人/事/画面(如「殡仪馆第七具遗体睁眼」)，让读者脑中能成像；"
        "删形容词堆砌。"
    ),
}


def _fix_for(key: str) -> str:
    return _FIX_DIRECTIVES.get(key, f"加强维度：{key}")


_AXIS_HINT: dict[str, str] = {
    "contrast_irony": "卖点四平八稳、无反差/反讽，读者没有「咦?」的一下",
    "click_hook": "开头是设定/背景铺陈，钩子迟到或缺席",
    "motivation_credibility": "主角动机得不偿失/牵强，读者不信他会反复这么做",
    "unpredictability": "套路可一眼望到头，意料之中、无惊喜",
    "payoff_promise": "说不清读者图什么情绪满足，卖点是「文笔好/世界观宏大」这类非承诺",
    "differentiation": "命中该题材红海套路、与头部高度雷同",
    "concrete_picture": "抽象设定罗列、形容词堆砌，读者脑中没有画面",
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
        "你不是和善的编辑，你是【在书城刷简介、3 秒就划走的挑剔读者】与签约主编的合体。"
        "只凭这一句话卖点，毒辣判断它是不是【真卖点】——能不能让你【必点】。\n"
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
        "特别严打两个致命伤：① 动机得不偿失到你不信主角会做；② 你一句话就能预判结局。\n"
        '只输出严格 JSON：{"scores":{"contrast_irony":n,"click_hook":n,'
        '"motivation_credibility":n,"unpredictability":n,"payoff_promise":n,'
        '"differentiation":n,"concrete_picture":n},"why":{...}}'
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
    """读者视角评卖点 → 裁决是否放行扩充。永不抛错（LLM 失败 → fail-open lean-pass）。"""

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
        try:
            scores = await _run_reader_judge(
                session, settings, text=text, genre=genre, sub_genre=sub_genre,
                gate=gate, judge_model_key=judge_model_key, fallback=fallback,
            )
            llm_used = True
        except Exception:
            logger.warning("logline reader judge failed; fail-open lean-pass", exc_info=True)
            scores = {k: fallback for k in AXIS_KEYS}
            llm_used = False

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
    genre: str | None,
    sub_genre: str | None,
    gate: dict[str, Any],
    judge_model_key: str | None,
    fallback: float,
) -> dict[str, float]:
    from bestseller.services.llm import LLMCompletionRequest, complete_text

    # 中性 fallback JSON：LLM 截断/失败时 complete_text 回此值 → _parse_scores 得 lean-pass，
    # 绝不无判官误毙（fallback_response 是 LLMCompletionRequest 的必填项）。
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
                f"一句话卖点：\n{text[:800]}\n\n立即输出严格 JSON（7 个维度都要给分）。"
            ),
            fallback_response=fallback_json,
            prompt_template="logline_gate",
            prompt_version="v2",
            model_catalog_key=judge_model_key,
            metadata={"judge_scope": "logline_gate", "genre": str(genre or "")},
            max_tokens_override=512,
        ),
    )
    raw = getattr(completion, "content", None) or getattr(completion, "text", None) or ""
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
