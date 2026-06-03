# ruff: noqa: RUF001, S311
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import hashlib
import random
import re
from typing import Any

from bestseller.domain.anti_commonsense_hook import HookCandidate, HookMechanism, HookSpec
from bestseller.services.anti_commonsense_mechanisms import (
    get_mechanism,
    list_mechanisms,
    select_mechanisms_for_genre,
)
from bestseller.services.deduplication import compute_jaccard_similarity
from bestseller.services.hook_strength_gate import (
    evaluate_hook_strength_gate,
    repair_hook_spec_once,
    score_hook,
)

DuplicateRiskFn = Callable[[HookSpec], float]

HOOK_METHODOLOGY_TYPES: tuple[tuple[str, str], ...] = (
    ("information_gap", "信息差"),
    ("deadline", "倒计时"),
    ("mystery", "悬念"),
    ("desire", "欲望兑现"),
    ("threat", "逼近威胁"),
)

OPENING_FRAMES: tuple[tuple[str, str], ...] = (
    ("countdown_threat", "倒计时威胁开场"),
    ("forbidden_witness", "撞见禁忌开场"),
    ("betrayed_first_paragraph", "首段背叛开场"),
    ("identity_crash", "身份崩塌起笔"),
    ("body_anomaly", "身体失控异象"),
    ("public_misread", "公开误读/围观压力"),
)

EXPRESSION_STYLES: tuple[str, ...] = (
    "rule_collision",
    "public_misread",
    "opening_deadlock",
    "cost_first",
    "reader_question",
    "market_logline",
)


def _seed_from_parts(*parts: object) -> int:
    raw = "|".join(str(part or "") for part in parts)
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12], 16)


def _pick_indexed(values: Iterable[str], fallback: str, index: int) -> str:
    items = [str(item).strip() for item in values if str(item).strip()]
    if not items:
        return fallback
    return items[index % len(items)]


def _render_reversal_phrase(reversal: str) -> str:
    text = str(reversal or "").strip()
    if not text:
        return "偏偏要走一条反常识路径"
    if text.startswith(("必须", "越", "最", "反而", "不能", "只有")):
        return f"偏偏{text}"
    return f"却只能{text}"


def _quote_reversal(reversal: str) -> str:
    text = str(reversal or "").strip(" ，。；;")
    return f"「{text or '反常识规则'}」"


def _mechanism_pool_for_generation(
    genre: str,
    mechanism_keys: list[str] | tuple[str, ...] | None,
) -> list[HookMechanism]:
    if mechanism_keys:
        return [get_mechanism(key) for key in mechanism_keys]
    primary = list(select_mechanisms_for_genre(genre))
    primary_keys = {item.key for item in primary}
    adjacent = [item for item in list_mechanisms() if item.key not in primary_keys]
    adjacent.sort(key=lambda item: item.saturation_score)
    return [*primary, *adjacent]


def _methodology_pair(index: int) -> tuple[str, str]:
    return HOOK_METHODOLOGY_TYPES[index % len(HOOK_METHODOLOGY_TYPES)]


def _opening_pair(index: int) -> tuple[str, str]:
    return OPENING_FRAMES[index % len(OPENING_FRAMES)]


def _uses_chinese_hook_labels(*values: object) -> bool:
    return any(re.search(r"[\u4e00-\u9fff]", str(value or "")) for value in values)


def _render_one_liner(
    *,
    role: str,
    desire: str,
    mechanism: HookMechanism,
    reversal: str,
    reward: str,
    cost: str,
    misunderstanding: str,
    hook_type_label: str,
    opening_label: str,
    style: str,
) -> str:
    """Render the one-liner using the YAML formula pool.

    Kept as a thin wrapper around ``render_one_liner_for_spec`` for backwards
    compatibility with code that calls it directly. The 6 legacy ``style`` ids
    (public_misread, opening_deadlock, cost_first, reader_question,
    market_logline, rule_collision) are now formula ids in the pool and
    render exactly the same way as before.
    """

    from bestseller.services.hook_formula_pool import render_one_liner_for_spec

    # Build a temporary spec with the slot values; the spec.mechanism_key is
    # the only attribute the formula selector reads (via select_formula_for_mechanism).
    proxy_spec = HookSpec(
        mechanism_key=mechanism.key,
        genre=getattr(mechanism, "_genre_for_pool", "") or "",
        setting_locale=None,
        protagonist_role=role,
        base_desire=desire,
        reversal=reversal,
        rewards=(reward,),
        costs=(cost,),
        misunderstanding=misunderstanding or None,
        hook_type=hook_type_label or "",
        opening_frame=opening_label or "",
        expression_style=style,
        one_liner="placeholder",
        core_rule="placeholder",
    )
    return render_one_liner_for_spec(
        proxy_spec,
        formula_id=style or None,
        mechanism=mechanism,
        mechanism_label=mechanism.label,
    )


def _render_one_liner_from_spec(spec: HookSpec, *, style: str | None = None) -> str:
    """Public alias for tests / external callers that already hold a HookSpec."""

    from bestseller.services.hook_formula_pool import render_one_liner_for_spec

    return render_one_liner_for_spec(spec, formula_id=style)


def _render_design_brief(
    *,
    mechanism: HookMechanism,
    desire: str,
    reward: str,
    cost: str,
    misunderstanding: str,
    hook_type_label: str,
    opening_label: str,
) -> str:
    return (
        "给大模型的机制融合任务: 不要照抄固定能力名, 先围绕题材重新设计可执行规则。"
        f"底层机制={mechanism.label}; 读者欲望={desire}; 反常识规则={mechanism.reversal_template}; "
        f"首章钩子形态={opening_label}; 章节钩子类型={hook_type_label}; "
        f"爽点回报={reward}; 必付代价={cost}; 持续误解={misunderstanding or '外界误读'}。"
        "输出时必须把它落成世界规则、限制、反作弊、代价升级和章节循环, 不能只写成一句设定说明。"
    )


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


def _diversify_by_mechanism(
    candidates: list[HookCandidate],
    *,
    count: int,
    min_h_norm: float,
    rotation_seed: int | None = None,
) -> list[HookCandidate]:
    """Keep ranking quality while preventing one mechanism from flooding previews.

    When ``rotation_seed`` is set, the order in which mechanism buckets are
    sampled is rotated so the visible page changes on each fresh request
    (e.g. the "换一批" button). Falling candidates still fill in at the end
    if there are not enough passing candidates to fill the quota.
    """

    passing_buckets: dict[str, list[HookCandidate]] = {}
    fallback_buckets: dict[str, list[HookCandidate]] = {}
    for item in candidates:
        key = item.spec.mechanism_key
        target = passing_buckets if item.score.h_norm >= min_h_norm else fallback_buckets
        target.setdefault(key, []).append(item)

    passing_keys = list(passing_buckets.keys())
    falling_keys = [key for key in fallback_buckets.keys() if key not in passing_buckets]

    if rotation_seed is not None:
        rng = random.Random(int(rotation_seed))
        rng.shuffle(passing_keys)
        rng.shuffle(falling_keys)

    selected: list[HookCandidate] = []
    seen_ids: set[str] = set()
    for walk_order, source in ((passing_keys, passing_buckets), (falling_keys, fallback_buckets)):
        for key in walk_order:
            bucket = source[key]
            while bucket and bucket[0].spec.one_liner in seen_ids:
                bucket.pop(0)
            if not bucket:
                continue
            item = bucket.pop(0)
            selected.append(item)
            seen_ids.add(item.spec.one_liner)
            if len(selected) >= count:
                break
        if len(selected) >= count:
            break
    return selected


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
        "relationship": "关系变动必须被关键关系人感知",
        "public_eye": "代价或收益必须在公开场合被看见",
        "oath": "誓言、契约或承诺会反向约束行动",
        "inheritance": "传承、师徒或血脉会绑定不可卸下的责任",
        "ledger": "因果账本必须实时结算，不可预支或抹除",
        "disguise": "身份伪装必须有可被验证的失效条件",
        "script": "剧本、规则文本或设定本身会反向约束角色",
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
    variant_index: int = 0,
    expression_style: str | None = None,
) -> HookSpec:
    rng = rng or random.Random(
        _seed_from_parts(mechanism.key, genre, locale, protagonist_role)
    )
    desire = base_desire or _pick_indexed(
        mechanism.base_desire_pool,
        "改变命运",
        variant_index,
    )
    reward = _pick_indexed(
        mechanism.reward_pool,
        "命运翻盘",
        variant_index // max(1, len(mechanism.base_desire_pool)),
    )
    cost = _pick_indexed(
        mechanism.cost_templates,
        "每次成功都会留下可见代价",
        variant_index // max(1, len(mechanism.base_desire_pool) * len(mechanism.reward_pool)),
    )
    misunderstanding = _pick_indexed(
        mechanism.misunderstanding_patterns,
        "外界误读主角真实意图",
        variant_index,
    )
    hook_type, hook_type_label = _methodology_pair(variant_index)
    opening_frame, opening_label = _opening_pair(variant_index)
    role = protagonist_role or "主角"
    use_chinese_labels = _uses_chinese_hook_labels(
        genre,
        locale,
        role,
        desire,
        reward,
        cost,
        misunderstanding,
        mechanism.label,
    )
    constraints = _sample_constraints(rng, mechanism)
    anti_cheat = tuple(list(mechanism.anti_cheat_rules)[:3])
    arc_hook_type = hook_type_label if use_chinese_labels else hook_type
    arc_opening_frame = opening_label if use_chinese_labels else opening_frame
    arc_engine = tuple(
        dict.fromkeys(
            [
                *list(mechanism.arc_escalation_axes)[:4],
                arc_hook_type,
                arc_opening_frame,
            ]
        )
    )
    core_rule = (
        f"{mechanism.label}机制骨架：当{role}追求「{desire}」时，必须把"
        f"「{mechanism.reversal_template}」改造成题材内可验证的行动规则；"
        f"每次获得「{reward}」都绑定「{cost}」，并通过「{hook_type_label}」"
        "维持钩子生命周期。"
    )
    design_brief = _render_design_brief(
        mechanism=mechanism,
        desire=desire,
        reward=reward,
        cost=cost,
        misunderstanding=misunderstanding,
        hook_type_label=hook_type_label,
        opening_label=opening_label,
    )
    # Build a provisional spec so the formula pool can pick the right template
    # via the mechanism's formula_affinity and genre, then render the one_liner.
    from bestseller.services.hook_formula_pool import (
        render_one_liner_for_spec,
        select_formula_for_mechanism,
    )

    formula = select_formula_for_mechanism(
        mechanism,
        genre=genre,
        variant_index=variant_index,
        formula_id=expression_style,
    )
    style = formula.id
    provisional = HookSpec(
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
        hook_type=hook_type,
        opening_frame=opening_frame,
        expression_style=style,
        methodology_axes=(
            hook_type_label if use_chinese_labels else hook_type,
            opening_label if use_chinese_labels else opening_frame,
            "钩子生命周期" if use_chinese_labels else "hook_lifecycle",
            "核心循环触发-行动-回报-投入"
            if use_chinese_labels
            else "core_loop_trigger_action_reward_investment",
            "转折规则" if use_chinese_labels else "but_rule",
        ),
        llm_design_brief=design_brief,
        one_liner="placeholder",
        core_rule=core_rule,
    )
    one_liner = render_one_liner_for_spec(
        provisional,
        formula_id=style,
        mechanism=mechanism,
        mechanism_label=mechanism.label,
    )
    return provisional.model_copy(update={"one_liner": one_liner})


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
    """Generate deterministic HookSpec candidates and rank them.

    The ``seed`` argument doubles as the rotation seed: a different seed
    produces a different first-page composition (different mechanisms surfaced
    first), not just a different score ordering. Pass ``None`` to fall back
    to the genre-derived default seed.
    """

    mechanisms = _mechanism_pool_for_generation(genre, mechanism_keys)
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
        variant_index = idx // len(mechanisms)
        local_rng = random.Random(rng.randint(1, 10_000_000) + idx)
        spec = build_hook_spec_from_mechanism(
            mechanism,
            genre=genre,
            locale=locale,
            protagonist_role=role,
            base_desire=base_desire,
            rng=local_rng,
            variant_index=variant_index,
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
    if candidates:
        repaired_candidates: list[HookCandidate] = []
        for item in candidates:
            if item.score.h_norm >= min_h_norm:
                repaired_candidates.append(item)
                continue
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
    return _diversify_by_mechanism(
        ordered,
        count=max(0, count),
        min_h_norm=min_h_norm,
        rotation_seed=seed,
    )


def hook_candidates_to_payload(candidates: list[HookCandidate]) -> list[dict[str, Any]]:
    return [candidate.model_dump(mode="json") for candidate in candidates]


__all__ = [
    "build_hook_duplicate_risk_fn",
    "build_hook_spec_from_mechanism",
    "generate_hook_candidates",
    "hook_candidates_to_payload",
]
