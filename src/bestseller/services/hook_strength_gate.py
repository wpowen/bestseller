# ruff: noqa: RUF001
from __future__ import annotations

from collections.abc import Mapping
import math
import re
from typing import Any

from bestseller.domain.anti_commonsense_hook import (
    HookScore,
    HookSpec,
    HookStrengthFinding,
    HookStrengthGateReport,
)

REJECT_H_NORM = 15.0
SEED_H_NORM = 30.0
REVIEW_H_NORM = 45.0

_OPPOSITION_HINTS = (
    "必须",
    "越",
    "反而",
    "不能",
    "亏",
    "死",
    "失败",
    "误解",
    "规则",
    "代价",
    "forced",
    "must",
    "lose",
    "death",
    "misread",
    "cost",
)
# Chinese web novel 爆款 emotion vocabulary. The expansion scorer rewards hooks
# that name a concrete emotional beat readers can latch onto.
_EMOTION_HINTS = (
    "打脸",
    "翻盘",
    "塌房",
    "炸场",
    "围观",
    "心动",
    "炸裂",
    "崩盘",
    "封神",
    "破防",
    "硬刚",
    "硬撑",
    "破局",
    "立威",
    "甩开",
    "断舍",
    "高甜",
    "撒糖",
    "嘴硬",
    "上头",
    "破碎",
    "封口",
)
# Antagonist-visibility vocabulary. Misunderstanding scoring rewards hooks
# that put a concrete opposition (person, group, institution) on the page.
_VILLAIN_HINTS = (
    "敌人",
    "对手",
    "反派",
    "对家",
    "死敌",
    "旁人",
    "围观",
    "群众",
    "前任",
    "婆家",
    "师门",
    "世家",
    "朝堂",
    "上司",
    "客户",
    "顾客",
    "金主",
    "public",
    "enemy",
    "rival",
    "antagonist",
)
_HIGH_REWARD_HINTS = ("权限", "跃迁", "资源", "证据", "声望", "真相", "identity", "power")
_COST_HINTS = ("代价", "失去", "折损", "风险", "牺牲", "反噬", "cost", "risk", "lose")
_ANCHOR_STOPWORDS = {
    "主角",
    "读者",
    "故事",
    "小说",
    "平台",
    "一个",
    "一部",
    "核心",
    "持续",
    "升级",
    "都市",
    "修仙",
    "职业",
    "长篇",
}
_AUTO_ANCHOR_MARKERS = (
    "灵务局",
    "考编",
    "岗位权限",
    "公务工单",
    "工单",
    "临聘",
    "巡检",
    "巡检员",
    "陆沉",
    "灵石配额",
    "配额",
    "审批",
    "审批黑箱",
    "转正",
    "正式编制",
    "编制",
    "验房",
    "验房报告",
    "强制复检",
    "合规台账",
    "执照扣分",
    "审计",
    "旧账",
    "凶宅",
    "困魂镜",
    "风水师",
    "死亡名单",
    "双穿门",
)


def _clamp_int(value: float, low: int = 0, high: int = 10) -> int:
    return max(low, min(high, round(value)))


def _clamp_float(value: float, low: float = 0.0, high: float = 100.0) -> float:
    if math.isnan(value) or math.isinf(value):
        return low
    return max(low, min(high, value))


def _text(value: object) -> str:
    return str(value or "").strip()


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,，、/／|]", value) if part.strip()]
    if isinstance(value, Mapping):
        return []
    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _clean_anchor(value: object) -> str:
    text = re.sub(r"\s+", "", str(value or "").strip())
    text = text.strip("《》“”\"'：:，,。.!！？?；;（）()[]【】")
    if not (2 <= len(text) <= 12):
        return ""
    if text in _ANCHOR_STOPWORDS:
        return ""
    return text


def _append_anchor(group: dict[str, list[str]], key: str, value: object) -> None:
    text = _clean_anchor(value)
    if not text:
        return
    values = group.setdefault(key, [])
    if text not in values:
        values.append(text)


def _extract_context_texts(context: Mapping[str, Any]) -> dict[str, str]:
    return {
        "premise": " ".join(
            _text(context.get(key))
            for key in ("premise", "synopsis", "short_intro", "logline")
            if _text(context.get(key))
        ),
        "title": " ".join(
            _text(context.get(key)) for key in ("title", "primary_title") if _text(context.get(key))
        ),
        "genre": " ".join(
            [
                _text(context.get("genre")),
                _text(context.get("sub_genre")),
                " ".join(_string_list(context.get("tags"))),
            ]
        ),
    }


def premise_anchor_groups(premise_context: Mapping[str, Any] | str | None) -> dict[str, list[str]]:
    """Derive deterministic story anchors used to reject semantically wrong hooks.

    The gate intentionally stays lightweight: it only enforces alignment when the
    caller supplies enough concrete anchors. Generic genre labels alone do not
    trigger a hard mismatch.
    """

    if premise_context is None:
        return {}
    context: Mapping[str, Any]
    if isinstance(premise_context, str):
        context = {"premise": premise_context}
    elif isinstance(premise_context, Mapping):
        context = premise_context
    else:
        return {}

    groups: dict[str, list[str]] = {}
    raw_groups = context.get("title_anchor_groups")
    if isinstance(raw_groups, Mapping):
        for key, value in raw_groups.items():
            for item in _string_list(value):
                _append_anchor(groups, str(key), item)

    for item in context.get("main_characters") or ():
        if not isinstance(item, Mapping):
            continue
        _append_anchor(groups, "protagonist", item.get("name"))
        _append_anchor(groups, "identity", item.get("identity") or item.get("role"))

    dna = context.get("story_title_dna")
    if isinstance(dna, Mapping):
        _append_anchor(groups, "protagonist", dna.get("protagonist"))
        _append_anchor(groups, "identity", dna.get("identity"))
        _append_anchor(groups, "mechanism", dna.get("central_action") or dna.get("payoff"))
        _append_anchor(groups, "pressure", dna.get("stakes") or dna.get("conflict"))

    texts = _extract_context_texts(context)
    source_text = " ".join(texts.values())
    for marker in _AUTO_ANCHOR_MARKERS:
        if marker in source_text:
            if marker in {"陆沉"}:
                _append_anchor(groups, "protagonist", marker)
            elif marker in {"灵务局", "临聘", "巡检", "巡检员", "风水师", "审计"}:
                _append_anchor(groups, "identity", marker)
            elif any(token in marker for token in ("权限", "工单", "考编", "报告", "复检", "台账", "双穿门")):
                _append_anchor(groups, "mechanism", marker)
            else:
                _append_anchor(groups, "pressure", marker)

    for token in _string_list(context.get("tags")):
        _append_anchor(groups, "genre", token)
    for token in re.findall(r"[\u4e00-\u9fff]{2,6}", texts["title"]):
        _append_anchor(groups, "title", token)

    return {key: values[:8] for key, values in groups.items() if values}


def hook_premise_alignment(
    spec: HookSpec,
    premise_context: Mapping[str, Any] | str | None,
) -> tuple[bool, dict[str, list[str]], dict[str, list[str]]]:
    groups = premise_anchor_groups(premise_context)
    concrete_groups = {
        key: values
        for key, values in groups.items()
        if key != "genre" and any(value not in _ANCHOR_STOPWORDS for value in values)
    }
    if len(concrete_groups) < 2:
        return True, {}, groups
    hook_text = " ".join(
        [
            spec.one_liner,
            spec.core_rule,
            spec.genre,
            spec.setting_locale or "",
            spec.protagonist_role or "",
            spec.base_desire,
            spec.reversal,
            *(str(item) for item in spec.rewards),
            *(str(item) for item in spec.costs),
            *(str(item) for item in spec.constraints.values()),
            *(str(item) for item in spec.arc_engine),
        ]
    )
    matched = {
        key: [value for value in values if value and value in hook_text]
        for key, values in concrete_groups.items()
    }
    matched = {key: values for key, values in matched.items() if values}
    return len(matched) >= 2, matched, groups


def _score_delta(spec: HookSpec) -> int:
    combined = f"{spec.base_desire} {spec.reversal} {spec.core_rule} {spec.one_liner}".lower()
    hits = sum(1 for token in _OPPOSITION_HINTS if token.lower() in combined)
    explicit_pair = bool(
        spec.base_desire and spec.reversal and spec.base_desire not in spec.reversal
    )
    return _clamp_int(4 + hits * 1.2 + (1.5 if explicit_pair else 0), 1, 10)


def _score_reward(spec: HookSpec) -> int:
    text = " ".join([*spec.rewards, spec.one_liner]).lower()
    hits = sum(1 for token in _HIGH_REWARD_HINTS if token.lower() in text)
    return _clamp_int(3 + len(spec.rewards) * 1.4 + hits, 1, 10)


def _score_constraint(spec: HookSpec) -> int:
    dimensions = len([v for v in spec.constraints.values() if v])
    anti_cheat_bonus = min(2.0, len(spec.anti_cheat) * 0.6)
    return _clamp_int(2 + dimensions * 1.5 + anti_cheat_bonus, 1, 10)


def _score_penalty(spec: HookSpec) -> int:
    text = " ".join([*spec.costs, spec.one_liner, spec.core_rule]).lower()
    hits = sum(1 for token in _COST_HINTS if token.lower() in text)
    return _clamp_int(2 + len(spec.costs) * 1.8 + hits * 0.6, 1, 10)


def _score_misunderstanding(spec: HookSpec) -> int:
    text = spec.misunderstanding or ""
    if not text:
        return 3
    durable = any(token in text for token in ("外界", "敌人", "旁人", "public", "enemy"))
    # Also reward hooks whose misunderstanding explicitly names an antagonist —
    # a visible opposing party is the single biggest CN retention predictor.
    villain_visible = any(token in text for token in _VILLAIN_HINTS)
    bonus = 1.5 if durable else 0
    if villain_visible:
        bonus += 0.8
    return _clamp_int(6 + bonus, 1, 10)


def _score_expansion(spec: HookSpec) -> int:
    """Reward arcs that name a concrete emotional beat readers can latch onto.

    Keeps the ``expansion`` field name for backwards compatibility, but the
    formula now mixes arc-engine length with Chinese web novel emotion-vocabulary
    hits in the one_liner + core_rule.
    """
    arc_boost = len(spec.arc_engine) * 1.0
    emotion_text = f"{spec.one_liner} {spec.core_rule}"
    emotion_hits = sum(1 for token in _EMOTION_HINTS if token in emotion_text)
    return _clamp_int(3 + arc_boost + emotion_hits * 1.4, 1, 10)


def _score_learning_cost(spec: HookSpec) -> int:
    one_liner = spec.one_liner.strip()
    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", one_liner))
    latin_words = len(re.findall(r"[A-Za-z]+", one_liner))
    length = cjk_chars if cjk_chars >= latin_words else latin_words
    if 25 <= cjk_chars <= 60 or 7 <= latin_words <= 22:
        return 4
    if length <= 0:
        return 10
    if length < 12:
        return 6
    if length <= 90:
        return 5
    return 8


def _verdict_for_h_norm(h_norm: float) -> str:
    if h_norm < REJECT_H_NORM:
        return "reject"
    if h_norm < SEED_H_NORM:
        return "seed"
    if h_norm < REVIEW_H_NORM:
        return "review"
    return "expand"


def score_hook(
    spec: HookSpec | str,
    *,
    platform_profile: Mapping[str, Any] | None = None,
) -> HookScore:
    """Calculate normalized hook strength with deterministic rules."""

    hook_spec = extract_hook_spec_from_text(spec) if isinstance(spec, str) else spec
    delta = _score_delta(hook_spec)
    reward = _score_reward(hook_spec)
    constraint = _score_constraint(hook_spec)
    penalty = _score_penalty(hook_spec)
    misunderstanding = _score_misunderstanding(hook_spec)
    expansion = _score_expansion(hook_spec)
    learning_cost = _score_learning_cost(hook_spec)
    raw = (
        100.0
        * (delta / 10.0)
        * (reward / 10.0)
        * (constraint / 10.0)
        * (penalty / 10.0)
        * (misunderstanding / 10.0)
        * (expansion / 10.0)
        / max(0.3, learning_cost / 10.0)
    )
    h_norm = round(_clamp_float(raw), 2)
    return HookScore(
        delta=delta,
        reward=reward,
        constraint=constraint,
        penalty=penalty,
        misunderstanding=misunderstanding,
        expansion=expansion,
        learning_cost=learning_cost,
        h_norm=h_norm,
        verdict=_verdict_for_h_norm(h_norm),  # type: ignore[arg-type]
    )


def extract_hook_spec_from_text(text: str) -> HookSpec:
    """Coarse heuristic fallback for free-string premise evaluation.

    This path is a deterministic rough estimate, not semantic premise extraction.
    Production planning should pass a structured HookSpec.
    """

    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        clean = "普通人想改变命运，却被迫承担反常识规则的代价。"
    costs: list[str] = []
    if any(token in clean for token in _COST_HINTS):
        costs.append("每次兑现爽点都必须支付可见代价")
    constraints = {"ban": "不能绕开核心反常识规则"}
    if any(token in clean for token in ("时间", "deadline", "每天", "每次")):
        constraints["time"] = "触发窗口受时间限制"
    if any(token in clean for token in ("规则", "必须", "不能", "must", "rule")):
        constraints["method"] = "必须按规则指定方式完成"
    misunderstanding = "外界误读主角真实意图" if any(
        token in clean for token in ("误解", "以为", "迪化", "misread")
    ) else None
    return HookSpec(
        mechanism_key="free_text",
        genre="",
        setting_locale=None,
        protagonist_role=None,
        base_desire="改变命运",
        reversal=clean[:180],
        rewards=("命运翻盘",),
        constraints=constraints,
        anti_cheat=("不能无代价重复触发",),
        costs=tuple(costs or ["失败会留下后续债务"]),
        misunderstanding=misunderstanding,
        arc_engine=("规则升级", "代价升级"),
        one_liner=clean[:120],
        core_rule=clean[:240],
    )


def evaluate_hook_strength_gate(
    spec: HookSpec | str,
    *,
    min_h_norm: float = SEED_H_NORM,
    platform_profile: Mapping[str, Any] | None = None,
    premise_context: Mapping[str, Any] | str | None = None,
) -> HookStrengthGateReport:
    hook_spec = extract_hook_spec_from_text(spec) if isinstance(spec, str) else spec
    score = score_hook(hook_spec, platform_profile=platform_profile)
    findings: list[HookStrengthFinding] = []
    suggestions: list[str] = []
    if score.delta < 6:
        findings.append(
            HookStrengthFinding(
                code="weak_reversal",
                severity="high",
                message="Hook reversal is not clearly opposed to the base desire.",
                path="reversal",
                repair_action=(
                    "Make the protagonist's normal desire collide with a mandatory "
                    "opposite action."
                ),
            )
        )
        suggestions.append("补强欲望与反转之间的正面冲突。")
    if score.constraint < 6:
        findings.append(
            HookStrengthFinding(
                code="thin_constraints",
                severity="medium",
                message="Hook has too few operational constraints or anti-cheat rules.",
                path="constraints",
                repair_action=(
                    "Add time/object/method/ban constraints and explicit anti-cheat rules."
                ),
            )
        )
        suggestions.append("增加时间、对象、方式、禁止项或反作弊规则。")
    if score.penalty < 6:
        findings.append(
            HookStrengthFinding(
                code="low_cost",
                severity="medium",
                message="Reward lacks a visible cost or aftereffect.",
                path="costs",
                repair_action="Attach a recurring cost to every successful use of the hook.",
            )
        )
        suggestions.append("给每次成功绑定可见代价或后效。")
    if score.h_norm < min_h_norm:
        findings.append(
            HookStrengthFinding(
                code="below_h_norm_threshold",
                severity="high",
                message=f"H_norm {score.h_norm:.2f} is below threshold {min_h_norm:.2f}.",
                path="h_norm",
                repair_action=(
                    "Rewrite the premise with stronger reversal, constraints, cost, "
                    "misunderstanding, or expansion axes."
                ),
            )
        )
    aligned, _matched_anchor_groups, _anchor_groups = hook_premise_alignment(
        hook_spec,
        premise_context,
    )
    if not aligned:
        findings.append(
            HookStrengthFinding(
                code="hook_premise_mismatch",
                severity="high",
                message="Hook does not match the concrete premise anchors.",
                path="premise_context",
                repair_action=(
                    "Regenerate a hook that names the protagonist identity, core mechanism, "
                    "or opening pressure from the approved premise."
                ),
            )
        )
        suggestions.append("重写 hook，使其明确贴合主角身份、核心机制或开局压力。")
    # CN-market 爆款 emotion vocabulary check. Hooks without at least one
    # emotion marker read like AI template copy.
    emotion_text = f"{hook_spec.one_liner} {hook_spec.core_rule}"
    if not any(token in emotion_text for token in _EMOTION_HINTS):
        findings.append(
            HookStrengthFinding(
                code="weak_emotion_keywords",
                severity="low",
                message="Hook lacks Chinese-web-novel emotion vocabulary markers.",
                path="one_liner",
                repair_action=(
                    "Inject at least one of: 打脸 / 翻盘 / 塌房 / 炸场 / 围观 / 破防 / 上头 / 高甜 / 撒糖."
                ),
            )
        )
        suggestions.append("在 one_liner 或 core_rule 注入至少一个网文爆款情绪词。")
    hard_failed = any(
        finding.code == "hook_premise_mismatch" and finding.severity == "high"
        for finding in findings
    )
    passed = score.h_norm >= min_h_norm and not hard_failed
    return HookStrengthGateReport(
        findings=tuple(findings),
        h_norm=score.h_norm,
        passed=passed,
        rewrite_suggestions=tuple(suggestions),
        score=score,
        verdict="pass" if passed else "reject" if hard_failed else "warn_only",
    )


def repair_hook_spec_once(
    spec: HookSpec,
    report: HookStrengthGateReport,
) -> HookSpec:
    """Apply one deterministic strengthening pass based on gate findings."""

    constraints = dict(spec.constraints)
    anti_cheat = list(spec.anti_cheat)
    costs = list(spec.costs)
    rewards = list(spec.rewards)
    arc_engine = list(spec.arc_engine)

    codes = {finding.code for finding in report.findings}
    if "hook_premise_mismatch" in codes:
        return spec
    if "weak_reversal" in codes and "method" not in constraints:
        constraints["method"] = "每次兑现奖励前必须执行与正常欲望相反的可见动作"
    if "thin_constraints" in codes:
        constraints.setdefault("time", "触发必须发生在明确时限或场域内")
        constraints.setdefault("ban", "禁止用最直观捷径绕开核心代价")
        anti_cheat.extend(["同一对象重复触发收益衰减", "绕开代价会反噬"])
    if "low_cost" in codes:
        costs.extend(["每次成功都会留下公开误解或资源债务", "代价会在下一轮升级"])
    if "below_h_norm_threshold" in codes:
        rewards.extend(["权限提升", "真相碎片"])
        arc_engine.extend(["代价升级", "误解升级", "规则边界升级"])
    if "weak_emotion_keywords" in codes:
        # Bake a 爆款 emotion marker into the core_rule so the next round of
        # expansion scoring has something to lock onto. The one_liner is
        # rebuilt below via the formula pool, so we don't mutate it here.
        emotion_marker = "，在围观与打脸的反复推拉中持续兑现"
        if emotion_marker not in (spec.core_rule or ""):
            arc_engine.append("打脸升级")
            arc_engine.append("围观发酵")

    deduped_constraints = {key: value for key, value in constraints.items() if value}
    deduped_anti_cheat = tuple(dict.fromkeys(item for item in anti_cheat if item))
    deduped_costs = tuple(dict.fromkeys(item for item in costs if item))
    deduped_rewards = tuple(dict.fromkeys(item for item in rewards if item))
    deduped_arc = tuple(dict.fromkeys(item for item in arc_engine if item))
    misunderstanding = spec.misunderstanding or "外界误读主角真实意图并持续放大风险"

    # Build a provisional spec with the strengthened fields, then route the
    # one_liner through the same formula pool the original renderer uses, so
    # repaired hooks read in the same voice as freshly generated ones.
    provisional = spec.model_copy(
        update={
            "rewards": deduped_rewards,
            "costs": deduped_costs,
        }
    )
    from bestseller.services.hook_formula_pool import render_one_liner_for_spec

    one_liner = render_one_liner_for_spec(provisional, formula_id=spec.expression_style or None)
    core_rule = (
        f"{spec.core_rule} 每次触发必须同时满足限制、反作弊与可见代价；"
        "下一轮代价或误解必须升级。"
    )
    return spec.model_copy(
        update={
            "rewards": deduped_rewards,
            "constraints": deduped_constraints,
            "anti_cheat": deduped_anti_cheat,
            "costs": deduped_costs,
            "misunderstanding": misunderstanding,
            "arc_engine": deduped_arc,
            "one_liner": one_liner[:240],
            "core_rule": core_rule[:500],
        }
    )


def hook_strength_report_to_dict(report: HookStrengthGateReport) -> dict[str, Any]:
    return report.model_dump(mode="json")


__all__ = [
    "REJECT_H_NORM",
    "REVIEW_H_NORM",
    "SEED_H_NORM",
    "evaluate_hook_strength_gate",
    "extract_hook_spec_from_text",
    "hook_premise_alignment",
    "hook_strength_report_to_dict",
    "premise_anchor_groups",
    "repair_hook_spec_once",
    "score_hook",
]
