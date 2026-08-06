"""Concept Lab assembly for visible quickstart story contracts."""

from __future__ import annotations

import hashlib
import random
from typing import Any

from bestseller.domain.anti_commonsense_hook import HookCandidate, HookSpec
from bestseller.domain.concept_lab import (
    ConceptLabBundle,
    ConceptLabCatalog,
    ConceptListingSeed,
    ConceptMaterialBrief,
    ConceptStoryLoop,
    ConceptTitleSeed,
)
from bestseller.services.anti_commonsense_hook import generate_hook_candidates
from bestseller.services.copy_flavor import pick_reader_facing
from bestseller.services.genre_creativity import (
    GenreCreativeDirection,
    get_genre_creative_direction,
    get_genre_creativity_pack,
)
from bestseller.services.hook_propagation import coerce_hook_spec
from bestseller.services.hype_engine import hype_scheme_from_preset_overrides
from bestseller.services.writing_presets import GenrePreset, get_genre_preset


# Centralised LLM-facing labels. Keep the keys stable; the strings are the
# actual contract headers the writer model reads in its system prompt.
CONCEPT_LAB_PROMPT_LABEL = "【已选脑洞组合合同】"
CONCEPT_LAB_MATERIAL_LABEL = "【已选脑洞物料合同】"
SOURCE_MIX_HOOK_KEY = "反常识爽点库"


def build_concept_lab_catalog(
    genre_key: str,
    *,
    creative_key: str = "",
    hook_spec: dict[str, Any] | HookSpec | None = None,
    count: int = 8,
    seed: int | None = None,
) -> ConceptLabCatalog:
    """Build deterministic concept bundles for one genre selection.

    Pass a non-null ``seed`` to shuffle the bundle order and (if the seed is
    fresh) get a different first page of bundles. The shuffle is a pure
    function of (genre_key, creative_key, hook_spec, count, seed) so the UI
    can re-fetch and the new default_bundle_id will be different.
    """

    preset = get_genre_preset(genre_key)
    pack = get_genre_creativity_pack(genre_key)
    selected_hook = coerce_hook_spec(hook_spec)
    directions = _directions_for_pack(genre_key, creative_key)
    if not directions or count <= 0:
        return ConceptLabCatalog(genre_key=genre_key, default_bundle_id="", bundles=())

    target_pool = max(count * 2, 16) if seed is not None else count
    hooks = _hook_candidates_for_preset(
        preset,
        selected_hook=selected_hook,
        count=max(4, target_pool),
    )

    bundles: list[ConceptLabBundle] = []
    seen_bundle_ids: set[str] = set()
    for direction in directions:
        for hook in hooks:
            bundle = _build_bundle(preset, direction, hook)
            if bundle.bundle_id in seen_bundle_ids:
                continue
            bundles.append(bundle)
            seen_bundle_ids.add(bundle.bundle_id)
            if len(bundles) >= target_pool:
                break
        if len(bundles) >= target_pool:
            break

    if seed is not None:
        rng = random.Random(int(seed))
        rng.shuffle(bundles)

    bundles = bundles[:count]
    default_bundle_id = bundles[0].bundle_id if bundles else ""
    if pack.default_key and not creative_key:
        for bundle in bundles:
            if bundle.creative_key == pack.default_key:
                default_bundle_id = bundle.bundle_id
                break
    return ConceptLabCatalog(
        genre_key=genre_key,
        default_bundle_id=default_bundle_id,
        bundles=tuple(bundles),
    )


def select_concept_lab_bundle(
    *,
    genre_key: str,
    bundle_id: str = "",
    creative_key: str = "",
    hook_spec: dict[str, Any] | HookSpec | None = None,
    bundle_payload: dict[str, Any] | None = None,
) -> ConceptLabBundle | None:
    """Validate an incoming UI bundle or resolve it from the deterministic catalog."""

    if isinstance(bundle_payload, dict) and bundle_payload:
        try:
            bundle = ConceptLabBundle.model_validate(bundle_payload)
        except ValueError:
            bundle = None
        if bundle is not None and bundle.genre_key == genre_key:
            return bundle

    catalog = build_concept_lab_catalog(
        genre_key,
        creative_key=creative_key,
        hook_spec=hook_spec,
    )
    if bundle_id:
        for bundle in catalog.bundles:
            if bundle.bundle_id == bundle_id:
                return bundle
    return catalog.bundles[0] if catalog.bundles else None


def concept_lab_to_user_hints(bundle: ConceptLabBundle) -> dict[str, Any]:
    """Render a bundle as conception/story-architect user hints."""

    title_seed = bundle.title_seeds[0].text if bundle.title_seeds else ""
    listing = bundle.listing_seeds[0] if bundle.listing_seeds else None
    return {
        "concept_lab": bundle.model_dump(mode="json"),
        "reader_promise": bundle.reader_promise,
        "concept_one_liner": bundle.one_liner,
        "title_seed": title_seed,
        "listing_hook": listing.hook if listing else "",
        "material_brief": bundle.material_brief.model_dump(mode="json"),
        "story_loop": bundle.story_loop.model_dump(mode="json"),
        "methodology_targets": list(bundle.methodology_targets),
        "hype_targets": list(bundle.hype_targets),
        "usage_rule": (
            "把 concept_lab 当作读者承诺和故事循环合同: 标题、素材、设定、每章钩子"
            "必须服务同一个 one_liner; 不得把它退化成普通题材模板。"
        ),
    }


def coerce_concept_lab_bundle(value: Any) -> ConceptLabBundle | None:
    """Return a validated concept bundle from metadata/user hints."""

    if isinstance(value, ConceptLabBundle):
        return value
    if isinstance(value, dict):
        nested = value.get("concept_lab")
        candidate = nested if isinstance(nested, dict) else value
        if isinstance(candidate, dict):
            try:
                return ConceptLabBundle.model_validate(candidate)
            except ValueError:
                return None
    return None


def concept_lab_from_source(source: Any) -> ConceptLabBundle | None:
    """Resolve a concept bundle from project metadata or user-hint payloads."""

    if isinstance(source, ConceptLabBundle):
        return source
    if not isinstance(source, dict):
        return None
    for key in ("concept_lab", "concept_lab_bundle"):
        bundle = coerce_concept_lab_bundle(source.get(key))
        if bundle is not None:
            return bundle
    return coerce_concept_lab_bundle(source)


def render_concept_lab_prompt_block(source: Any, *, language: str = "zh-CN") -> str:
    """Render the selected concept contract for LLM planning prompts."""

    bundle = concept_lab_from_source(source)
    if bundle is None:
        return ""
    is_en = str(language or "").startswith("en")
    title_seed = bundle.title_seeds[0].text if bundle.title_seeds else ""
    listing = bundle.listing_seeds[0] if bundle.listing_seeds else None
    material = bundle.material_brief
    loop = bundle.story_loop
    payload = {
        "bundle_id": bundle.bundle_id,
        "reader_promise": bundle.reader_promise,
        "one_liner": bundle.one_liner,
        "title_seed": title_seed,
        "listing_hook": listing.hook if listing else "",
        "hook_design": {
            "mechanism_key": bundle.hook_spec.get("mechanism_key"),
            "hook_type": bundle.hook_spec.get("hook_type"),
            "opening_frame": bundle.hook_spec.get("opening_frame"),
            "expression_style": bundle.hook_spec.get("expression_style"),
            "methodology_axes": bundle.hook_spec.get("methodology_axes") or [],
            "llm_design_brief": bundle.hook_spec.get("llm_design_brief") or "",
            "core_rule": bundle.hook_spec.get("core_rule") or "",
            "constraints": bundle.hook_spec.get("constraints") or {},
            "anti_cheat": bundle.hook_spec.get("anti_cheat") or [],
            "costs": bundle.hook_spec.get("costs") or [],
        },
        "material_query_terms": list(material.query_terms[:12]),
        "material_combination_rules": list(material.combination_rules[:6]),
        "story_loop": {
            "opening_question": loop.opening_question,
            "recurring_pressure": loop.recurring_pressure,
            "payoff_window_chapters": loop.payoff_window_chapters,
            "escalation_axis": list(loop.escalation_axis[:8]),
            "per_chapter_contract": list(loop.per_chapter_contract[:6]),
        },
        "methodology_targets": list(bundle.methodology_targets),
        "hype_targets": list(bundle.hype_targets[:10]),
        "guardrails": list(bundle.guardrails[:10]),
    }
    label = "[Selected Concept Lab contract]" if is_en else CONCEPT_LAB_PROMPT_LABEL
    directive = (
        "Use this as a soft reference for titles, material selection, story "
        "design, and chapter loops. You MAY adapt it to better fit the genre; "
        "transform any mechanism into an original, cost-bearing, escalating rule "
        "rather than copying fixed ability names."
        if is_en
        else (
            "把它当作标题、素材选择、故事设计和章节循环的软参考（非硬合同）。"
            "可按题材自由改造；不要照抄固定能力名，请按 hook_design 把机制重新融合成"
            "该题材内可执行、可付代价、可升级的原创规则。"
        )
    )
    return f"{label}\n{directive}\n{json_dumps(payload)}"


def render_concept_lab_material_brief_block(source: Any, *, language: str = "zh-CN") -> str:
    """Render only the material-forge portion of the selected concept."""

    bundle = concept_lab_from_source(source)
    if bundle is None:
        return ""
    is_en = str(language or "").startswith("en")
    material = bundle.material_brief
    payload = {
        "one_liner": bundle.one_liner,
        "reader_promise": bundle.reader_promise,
        "dimensions": list(material.dimensions),
        "query_terms": list(material.query_terms),
        "combination_rules": list(material.combination_rules),
        "novelty_guardrails": list(material.novelty_guardrails),
        "seed_examples": list(material.seed_examples),
    }
    label = "[Concept Lab material brief]" if is_en else CONCEPT_LAB_MATERIAL_LABEL
    return f"{label}\n{json_dumps(payload)}"


def concept_lab_listing_overrides(source: Any) -> dict[str, Any]:
    """Return listing-profile fields derived from the selected concept bundle."""

    bundle = concept_lab_from_source(source)
    if bundle is None:
        return {}
    listing = bundle.listing_seeds[0] if bundle.listing_seeds else None
    title_candidates = [
        {
            "id": index,
            "title": seed.text,
            "subtitle": bundle.reader_promise[:80],
            "angle": seed.angle,
            "recommendation": seed.reason or "Concept Lab seed",
        }
        for index, seed in enumerate(bundle.title_seeds, start=1)
    ]
    tags = list(listing.tags if listing else ())
    return {
        "logline": bundle.one_liner,
        "short_intro": listing.blurb if listing and listing.blurb else bundle.reader_promise,
        "promo_copy": [
            item
            for item in [bundle.reader_promise, bundle.one_liner, listing.hook if listing else ""]
            if item
        ],
        "reader_promise": [bundle.reader_promise, *bundle.hype_targets[:4]],
        "tags": [*tags, *bundle.hype_targets[:4]],
        "title_candidates": title_candidates,
    }


def json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)


def _directions_for_pack(genre_key: str, creative_key: str) -> list[GenreCreativeDirection]:
    if creative_key:
        return [get_genre_creative_direction(genre_key, creative_key)]
    pack = get_genre_creativity_pack(genre_key)
    return list(pack.directions)


def _hook_candidates_for_preset(
    preset: GenrePreset,
    *,
    selected_hook: HookSpec | None,
    count: int,
) -> list[HookCandidate | HookSpec]:
    hooks: list[HookCandidate | HookSpec] = []
    if selected_hook is not None:
        hooks.append(selected_hook)
    hooks.extend(
        generate_hook_candidates(
            genre=preset.genre,
            locale=preset.sub_genre,
            count=count,
            seed=_seed_from_parts(preset.key, "concept-lab"),
            min_h_norm=30.0,
        )
    )
    return hooks[:count]


def _build_bundle(
    preset: GenrePreset,
    direction: GenreCreativeDirection,
    hook: HookCandidate | HookSpec,
) -> ConceptLabBundle:
    hook_spec = hook.spec if isinstance(hook, HookCandidate) else hook
    hook_score = hook.score.model_dump(mode="json") if isinstance(hook, HookCandidate) else {}
    novelty_score = hook.novelty_score if isinstance(hook, HookCandidate) else 0.0
    combined_rank = hook.combined_rank if isinstance(hook, HookCandidate) else 0.0
    hype = hype_scheme_from_preset_overrides(preset.writing_profile_overrides)
    reader_promise = pick_reader_facing(
        hype.reader_promise, preset.trend_summary, direction.logline
    )
    one_liner = hook_spec.one_liner or direction.logline
    bundle_id = _bundle_id(preset.key, direction.key, hook_spec.mechanism_key, one_liner)
    selling_points = _dedupe(
        [
            *hype.selling_points,
            *preset.reader_rewards,
            *direction.reader_rewards,
            *hook_spec.rewards,
        ],
        limit=8,
    )
    hype_targets = _dedupe(
        [
            *selling_points,
            *hype.hook_keywords,
            hype.chapter_hook_strategy,
            *hook_spec.arc_engine,
        ],
        limit=10,
    )
    guardrails = _dedupe(
        [
            *direction.anti_cliche_guardrails,
            *hook_spec.anti_cheat,
            "不得只靠金手指说明替代行动、代价和反转",
            "每个素材必须改变选择压力, 不能只做背景装饰",
        ],
        limit=10,
    )
    material_brief = _material_brief(preset, direction, hook_spec, guardrails)
    story_loop = _story_loop(direction, hook_spec, hype.payoff_window_chapters, guardrails)
    titles = _title_seeds(preset, direction, hook_spec)
    listings = _listing_seeds(reader_promise, one_liner, selling_points, preset, hook_spec)
    scores = {
        "trend_score": float(preset.trend_score or 0.0),
        "h_norm": float(hook_score.get("h_norm") or 0.0),
        "novelty_score": float(novelty_score),
        "combined_rank": float(combined_rank),
    }
    return ConceptLabBundle(
        bundle_id=bundle_id,
        genre_key=preset.key,
        creative_key=direction.key,
        hook_spec=hook_spec.model_dump(mode="json"),
        reader_promise=reader_promise,
        one_liner=one_liner,
        title_seeds=tuple(titles),
        listing_seeds=tuple(listings),
        material_brief=material_brief,
        story_loop=story_loop,
        methodology_targets=(
            "reader_promise_lineage",
            "opening_retention",
            "setup_payoff_tracking",
            "chapter_hook_continuity",
            "material_combination_trace",
        ),
        hype_targets=tuple(hype_targets),
        guardrails=tuple(guardrails),
        source_mix=tuple(
            _dedupe(
                [
                    *direction.source_mix,
                    SOURCE_MIX_HOOK_KEY,
                    "爽感/读者承诺引擎",
                    "素材组合合同",
                ],
                limit=8,
            )
        ),
        scores=scores,
    )


def _material_brief(
    preset: GenrePreset,
    direction: GenreCreativeDirection,
    hook_spec: HookSpec,
    guardrails: list[str],
) -> ConceptMaterialBrief:
    query_terms = _dedupe(
        [
            preset.genre,
            preset.sub_genre,
            *preset.trend_keywords,
            *preset.reader_rewards,
            *preset.narrative_drives,
            *direction.genre_lenses,
            hook_spec.mechanism_key,
            hook_spec.base_desire,
            hook_spec.reversal,
            *hook_spec.rewards,
        ],
        limit=14,
    )
    return ConceptMaterialBrief(
        dimensions=(
            "world_rules",
            "power_constraints",
            "status_system",
            "character_obligations",
            "plot_devices",
            "reader_payoff_beats",
        ),
        query_terms=tuple(query_terms),
        combination_rules=(
            "先选能强化 one_liner 的素材, 再选能制造代价的素材",
            "每组三素材至少包含一个世界规则、一个人物义务、一个可见爽点",
            "素材组合必须能生成可连续升级的问题链, 而不是一次性噱头",
        ),
        novelty_guardrails=tuple(guardrails[:6]),
        seed_examples=(
            direction.conflict_engine,
            hook_spec.core_rule,
            hook_spec.misunderstanding or "",
        ),
    )


def _story_loop(
    direction: GenreCreativeDirection,
    hook_spec: HookSpec,
    payoff_window: int,
    guardrails: list[str],
) -> ConceptStoryLoop:
    pressure_parts = _dedupe(
        [
            direction.conflict_engine,
            *(hook_spec.costs or ()),
            hook_spec.misunderstanding or "",
        ],
        limit=4,
    )
    escalation = _dedupe(
        [
            *hook_spec.arc_engine,
            *direction.narrative_drives,
            "代价升级",
            "误解升级",
            "身份/能力曝光风险升级",
        ],
        limit=8,
    )
    return ConceptStoryLoop(
        opening_question=direction.opening_hook or hook_spec.one_liner,
        recurring_pressure="; ".join(pressure_parts),
        payoff_window_chapters=payoff_window or 5,
        escalation_axis=tuple(escalation),
        per_chapter_contract=(
            "开章必须让 reader_promise 面临一个具体阻力",
            "中段必须暴露一个限制、代价或误解",
            "章尾必须兑现小回报并留下新变量",
        ),
        guardrails=tuple(guardrails[:6]),
    )


def _title_seeds(
    preset: GenrePreset,
    direction: GenreCreativeDirection,
    hook_spec: HookSpec,
) -> list[ConceptTitleSeed]:
    from bestseller.services.concept_title_formulas import (
        clamp_title_length,
        load_title_cores,
        load_title_formulas,
        render_title,
    )

    genre_label = preset.sub_genre or preset.genre
    reward = hook_spec.rewards[0] if hook_spec.rewards else "翻盘"
    cost = hook_spec.costs[0] if hook_spec.costs else "代价升级"
    hook_type_labels = {
        "information_gap": "信息差",
        "deadline": "倒计时",
        "mystery": "悬念",
        "desire": "欲望兑现",
        "threat": "逼近威胁",
    }
    hook_type = hook_type_labels.get(hook_spec.hook_type, hook_spec.hook_type) or "反常识规则"
    cores = load_title_cores()
    formulas = load_title_formulas()
    fallback_one_liner = clamp_title_length(hook_spec.one_liner[:28], low=0, high=28)
    title_core = cores.get(hook_spec.mechanism_key) or fallback_one_liner

    angle_reasons = (
        ("反常识命题标题", "直接给出反直觉的核心命题，让读者一眼看见机制。"),
        ("题材融合标题", "把题材、回报与代价同时放上台面。"),
        ("方法论钩子标题", "把创意方向与钩子类型绑定，方便后续 LLM 改写。"),
        ("反问爆点标题", "用一句反问/凭什么型短句制造点击冲动。"),
        ("倒计时型标题", "倒计时+爽点+代价，短视频感强。"),
    )
    seeds: list[ConceptTitleSeed] = []
    for index, formula in enumerate(formulas):
        text = render_title(
            formula,
            title_core=title_core,
            genre_label=genre_label,
            reward=reward,
            cost=cost,
            direction_title=direction.title,
            hook_type=hook_type,
            n=7,
        )
        if not text:
            text = fallback_one_liner or title_core or hook_spec.one_liner[:12]
        text = clamp_title_length(text)
        angle, reason = angle_reasons[min(index, len(angle_reasons) - 1)]
        seeds.append(ConceptTitleSeed(text=text, angle=angle, reason=reason))
        if len(seeds) >= 3:
            break
    if not seeds:
        seeds.append(
            ConceptTitleSeed(
                text=title_core or hook_spec.one_liner[:12],
                angle="反常识命题标题",
                reason="兜底标题。",
            )
        )
    return seeds


def _listing_seeds(
    reader_promise: str,
    one_liner: str,
    selling_points: list[str],
    preset: GenrePreset,
    hook_spec: HookSpec,
) -> list[ConceptListingSeed]:
    tags = _dedupe(
        [
            preset.genre,
            preset.sub_genre,
            *preset.reader_rewards,
            hook_spec.mechanism_key,
        ],
        limit=8,
    )
    bullets = _dedupe(
        [
            *selling_points[:4],
            hook_spec.core_rule,
            *(hook_spec.costs or ()),
        ],
        limit=5,
    )
    return [
        ConceptListingSeed(
            hook=one_liner,
            blurb=f"{reader_promise} 主角每次选择都要同时赢得回报、付出代价, 并把误解推向下一轮。",
            bullets=tuple(bullets),
            tags=tuple(tags),
        )
    ]


def _bundle_id(*parts: object) -> str:
    digest = hashlib.sha256(
        "|".join(str(part or "") for part in parts).encode("utf-8")
    ).hexdigest()
    return f"concept-{digest[:12]}"


def _seed_from_parts(*parts: object) -> int:
    raw = "|".join(str(part or "") for part in parts)
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12], 16)


def _dedupe(values: list[object] | tuple[object, ...], *, limit: int) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


__all__ = [
    "build_concept_lab_catalog",
    "coerce_concept_lab_bundle",
    "concept_lab_from_source",
    "concept_lab_listing_overrides",
    "concept_lab_to_user_hints",
    "render_concept_lab_material_brief_block",
    "render_concept_lab_prompt_block",
    "select_concept_lab_bundle",
]
