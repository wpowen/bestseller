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
_HIGH_REWARD_HINTS = ("权限", "跃迁", "资源", "证据", "声望", "真相", "identity", "power")
_COST_HINTS = ("代价", "失去", "折损", "风险", "牺牲", "反噬", "cost", "risk", "lose")


def _clamp_int(value: float, low: int = 0, high: int = 10) -> int:
    return max(low, min(high, round(value)))


def _clamp_float(value: float, low: float = 0.0, high: float = 100.0) -> float:
    if math.isnan(value) or math.isinf(value):
        return low
    return max(low, min(high, value))


def _text(value: object) -> str:
    return str(value or "").strip()


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
    if not spec.misunderstanding:
        return 3
    durable = any(
        token in spec.misunderstanding
        for token in ("外界", "敌人", "旁人", "public", "enemy")
    )
    return _clamp_int(6 + (1.5 if durable else 0), 1, 10)


def _score_expansion(spec: HookSpec) -> int:
    return _clamp_int(3 + len(spec.arc_engine) * 1.3, 1, 10)


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
    passed = score.h_norm >= min_h_norm
    return HookStrengthGateReport(
        findings=tuple(findings),
        h_norm=score.h_norm,
        passed=passed,
        rewrite_suggestions=tuple(suggestions),
        score=score,
        verdict="pass" if passed else "warn_only",
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

    deduped_constraints = {key: value for key, value in constraints.items() if value}
    deduped_anti_cheat = tuple(dict.fromkeys(item for item in anti_cheat if item))
    deduped_costs = tuple(dict.fromkeys(item for item in costs if item))
    deduped_rewards = tuple(dict.fromkeys(item for item in rewards if item))
    deduped_arc = tuple(dict.fromkeys(item for item in arc_engine if item))
    misunderstanding = spec.misunderstanding or "外界误读主角真实意图并持续放大风险"
    cost_line = "；".join(deduped_costs[:2])
    reward_line = "、".join(deduped_rewards[:2])
    one_liner = (
        f"{spec.protagonist_role or '主角'}想{spec.base_desire}，偏偏{spec.reversal}；"
        f"赢来{reward_line}，也付出{cost_line}。"
    )
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
    "hook_strength_report_to_dict",
    "repair_hook_spec_once",
    "score_hook",
]
