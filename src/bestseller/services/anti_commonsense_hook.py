# ruff: noqa: RUF001, S311
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import hashlib
import random
from typing import Any

from bestseller.domain.anti_commonsense_hook import HookCandidate, HookMechanism, HookSpec
from bestseller.services.anti_commonsense_mechanisms import (
    get_mechanism,
    select_mechanisms_for_genre,
)
from bestseller.services.deduplication import compute_jaccard_similarity
from bestseller.services.hook_strength_gate import (
    evaluate_hook_strength_gate,
    repair_hook_spec_once,
    score_hook,
)

DuplicateRiskFn = Callable[[HookSpec], float]


def _seed_from_parts(*parts: object) -> int:
    raw = "|".join(str(part or "") for part in parts)
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12], 16)


def _pick(rng: random.Random, values: Iterable[str], fallback: str) -> str:
    items = [str(item).strip() for item in values if str(item).strip()]
    if not items:
        return fallback
    return rng.choice(items)


def _render_reversal_phrase(reversal: str) -> str:
    text = str(reversal or "").strip()
    if not text:
        return "偏偏要走一条反常识路径"
    if text.startswith(("必须", "越", "最", "反而", "不能", "只有")):
        return f"偏偏{text}"
    return f"却只能{text}"


def _reference_text(spec: HookSpec) -> str:
    return "\n".join(
        item
        for item in (
            spec.one_liner,
            spec.core_rule,
            spec.reversal,
            "；".join(spec.rewards),
            "；".join(spec.costs),
        )
        if item
    )


def build_hook_duplicate_risk_fn(
    reference_texts: Iterable[str],
) -> DuplicateRiskFn:
    references = [str(item).strip() for item in reference_texts if str(item).strip()]

    def _risk(spec: HookSpec) -> float:
        if not references:
            return 0.0
        candidate_text = _reference_text(spec)
        return max(compute_jaccard_similarity(candidate_text, ref) for ref in references)

    return _risk


def _sample_constraints(
    rng: random.Random,
    mechanism: HookMechanism,
) -> dict[str, str]:
    dimensions = list(mechanism.constraint_dimensions) or ["method", "ban", "cost"]
    rng.shuffle(dimensions)
    selected = dimensions[: max(2, min(4, len(dimensions)))]
    labels = {
        "time": "必须在有限时间窗口内触发",
        "location": "只有在特定场域才生效",
        "object": "必须绑定明确对象，不能泛化",
        "target": "必须指定情绪或目标人群",
        "emotion": "只有真实情绪波动才结算",
        "count": "同一对象的触发次数有限",
        "method": "必须用反直觉方式完成",
        "ban": "禁止用最直观捷径绕过代价",
        "must_explain": "解释真相会削弱或反噬效果",
        "witness": "必须有见证者或误解者在场",
        "player_rule": "玩家行动受任务和资源上限约束",
        "respawn": "复活或重试必须消耗世界资源",
        "quest": "任务奖励不能凭空生成",
        "wording": "规则文字有不可替换的关键词",
        "profession_rule": "必须遵守职业伦理和工具边界",
        "tool": "只能使用职业可解释工具",
        "client": "委托人或客户关系会限制行动",
        "truth_gap": "读者和角色之间必须保留信息差",
        "audience": "误解必须发生在具体人群中",
    }
    return {
        dimension: labels.get(dimension, f"{dimension} 维度必须可验证")
        for dimension in selected
    }


def build_hook_spec_from_mechanism(
    mechanism: HookMechanism,
    *,
    genre: str = "",
    locale: str | None = None,
    protagonist_role: str | None = None,
    base_desire: str | None = None,
    rng: random.Random | None = None,
) -> HookSpec:
    rng = rng or random.Random(
        _seed_from_parts(mechanism.key, genre, locale, protagonist_role)
    )
    desire = base_desire or _pick(rng, mechanism.base_desire_pool, "改变命运")
    reward = _pick(rng, mechanism.reward_pool, "命运翻盘")
    cost = _pick(rng, mechanism.cost_templates, "每次成功都会留下可见代价")
    misunderstanding = _pick(rng, mechanism.misunderstanding_patterns, "外界误读主角真实意图")
    role = protagonist_role or "主角"
    constraints = _sample_constraints(rng, mechanism)
    anti_cheat = tuple(list(mechanism.anti_cheat_rules)[:3])
    arc_engine = tuple(list(mechanism.arc_escalation_axes)[:4])
    reversal_phrase = _render_reversal_phrase(mechanism.reversal_template)
    one_liner = f"{role}想{desire}，{reversal_phrase}；赢来{reward}，也付出{cost}。"
    core_rule = (
        f"{mechanism.label}机制：当{role}追求「{desire}」时，核心反常识规则是"
        f"「{mechanism.reversal_template}」；每次获得「{reward}」都绑定「{cost}」，"
        "且不可通过重复刷分或绕开限制作弊。"
    )
    return HookSpec(
        mechanism_key=mechanism.key,
        genre=genre,
        setting_locale=locale,
        protagonist_role=role,
        base_desire=desire,
        reversal=mechanism.reversal_template,
        rewards=(reward,),
        constraints=constraints,
        anti_cheat=anti_cheat,
        costs=(cost,),
        misunderstanding=misunderstanding,
        arc_engine=arc_engine,
        one_liner=one_liner,
        core_rule=core_rule,
    )


def generate_hook_candidates(
    *,
    genre: str,
    locale: str | None = None,
    role: str | None = None,
    base_desire: str | None = None,
    mechanism_keys: list[str] | tuple[str, ...] | None = None,
    count: int = 6,
    seed: int | None = None,
    min_h_norm: float = 30.0,
    duplicate_risk_fn: DuplicateRiskFn | None = None,
    rank_weights: Mapping[str, float] | None = None,
) -> list[HookCandidate]:
    """Generate deterministic HookSpec candidates and rank them."""

    if mechanism_keys:
        mechanisms = [get_mechanism(key) for key in mechanism_keys]
    else:
        mechanisms = list(select_mechanisms_for_genre(genre))
    if not mechanisms:
        return []
    rng = random.Random(
        seed if seed is not None else _seed_from_parts(genre, locale, role, base_desire)
    )
    weights = {
        "h_norm": 0.62,
        "novelty": 0.28,
        "duplicate_risk": 0.10,
        **dict(rank_weights or {}),
    }
    candidates: list[HookCandidate] = []
    attempts = max(count * 8, len(mechanisms) * 3)
    seen_one_liners: set[str] = set()
    for idx in range(attempts):
        mechanism = mechanisms[idx % len(mechanisms)]
        local_rng = random.Random(rng.randint(1, 10_000_000) + idx)
        spec = build_hook_spec_from_mechanism(
            mechanism,
            genre=genre,
            locale=locale,
            protagonist_role=role,
            base_desire=base_desire,
            rng=local_rng,
        )
        if spec.one_liner in seen_one_liners:
            continue
        seen_one_liners.add(spec.one_liner)
        score = score_hook(spec)
        novelty = round(max(0.0, min(1.0, 1.0 - mechanism.saturation_score)), 3)
        duplicate_risk = 0.0
        if duplicate_risk_fn is not None:
            duplicate_risk = max(0.0, min(1.0, float(duplicate_risk_fn(spec))))
        combined = (
            weights["h_norm"] * (score.h_norm / 100.0)
            + weights["novelty"] * novelty
            - weights["duplicate_risk"] * duplicate_risk
        )
        if score.h_norm < min_h_norm:
            combined -= 0.12
        candidates.append(
            HookCandidate(
                spec=spec,
                score=score,
                novelty_score=novelty,
                duplicate_risk=round(duplicate_risk, 3),
                combined_rank=round(max(0.0, min(1.0, combined)), 4),
            )
        )
    if candidates and not any(item.score.h_norm >= min_h_norm for item in candidates):
        repaired_candidates: list[HookCandidate] = []
        for item in candidates:
            report = evaluate_hook_strength_gate(item.spec, min_h_norm=min_h_norm)
            repaired = repair_hook_spec_once(item.spec, report)
            repaired_score = score_hook(repaired)
            if repaired_score.h_norm <= item.score.h_norm:
                repaired_candidates.append(item)
                continue
            combined = (
                weights["h_norm"] * (repaired_score.h_norm / 100.0)
                + weights["novelty"] * item.novelty_score
                - weights["duplicate_risk"] * item.duplicate_risk
            )
            repaired_candidates.append(
                HookCandidate(
                    spec=repaired,
                    score=repaired_score,
                    novelty_score=item.novelty_score,
                    duplicate_risk=item.duplicate_risk,
                    combined_rank=round(max(0.0, min(1.0, combined)), 4),
                )
            )
        candidates = repaired_candidates
    candidates.sort(key=lambda item: item.combined_rank, reverse=True)
    passing = [item for item in candidates if item.score.h_norm >= min_h_norm]
    failing = [item for item in candidates if item.score.h_norm < min_h_norm]
    ordered = [*passing, *failing]
    return ordered[: max(0, count)]


def hook_candidates_to_payload(candidates: list[HookCandidate]) -> list[dict[str, Any]]:
    return [candidate.model_dump(mode="json") for candidate in candidates]


__all__ = [
    "build_hook_duplicate_risk_fn",
    "build_hook_spec_from_mechanism",
    "generate_hook_candidates",
    "hook_candidates_to_payload",
]
