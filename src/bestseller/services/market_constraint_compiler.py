"""Compile FanqieMarketAnalysisBundle → ChapterMarketConstraints.

Three bands govern compilation:

* **early** (chapters 1-3, "golden three") — hooks must be loud, payoff
  cadence tight, saturated tropes most aggressively suppressed.
* **rising** (chapters 4-30) — emotional beats become as important as
  hooks; structure patterns enforce repeatable mechanism loops.
* **steady** (chapters 31+) — payoff patterns dominate; the early-stage
  attention machinery relaxes.

Saturation detection is heuristic but deterministic: any hook/structure
pattern appearing in ≥60% of top-N competitor profiles is flagged as
"saturated" and surfaced as a *forbidden* pattern even though it is
"market-validated" — because at saturation, copying it kills
differentiation.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from bestseller.domain.fanqie_market import (
    FanqieCategoryProfile,
    FanqieCompetitorProfile,
    FanqieCraftProfile,
    FanqieMarketAnalysisBundle,
)
from bestseller.domain.market_constraint import ChapterMarketConstraints

_SATURATION_THRESHOLD = 0.60
_TOP_COMPETITORS_FOR_SATURATION = 10
_DEFAULT_LENGTH_RANGES = {
    "early": (1800, 2600),
    "rising": (2200, 3000),
    "steady": (2400, 3200),
}


def compile_chapter_constraints(
    bundle: FanqieMarketAnalysisBundle | None,
    *,
    chapter_position: int,
    target_length: int | None = None,
    extra_safety_notes: Sequence[str] | None = None,
) -> ChapterMarketConstraints:
    """Compile per-chapter constraints from a market analysis bundle."""

    if chapter_position < 1:
        raise ValueError("chapter_position must be >= 1")

    band = _band_for_chapter(chapter_position)
    default_min, default_max = _DEFAULT_LENGTH_RANGES[band]

    if bundle is None:
        length_min, length_max = _length_range(band, target_length)
        return ChapterMarketConstraints(
            chapter_position=chapter_position,
            band=band,
            optimal_chapter_length_min=length_min,
            optimal_chapter_length_max=length_max,
            safety_boundary="；".join(extra_safety_notes or []),
            confidence=0.0,
            rationale=["no market bundle supplied"],
        )

    category_profile: FanqieCategoryProfile = bundle.category_profile
    craft_profile: FanqieCraftProfile = bundle.craft_profile
    competitors: list[FanqieCompetitorProfile] = list(bundle.competitor_profiles)

    saturated_hooks = _detect_saturated(
        [c.hook_patterns for c in competitors[:_TOP_COMPETITORS_FOR_SATURATION]]
    )
    saturated_structures = _detect_saturated(
        [c.structure_patterns for c in competitors[:_TOP_COMPETITORS_FOR_SATURATION]]
    )
    saturated = sorted(set(saturated_hooks) | set(saturated_structures))

    must_hit_hooks = _band_aware_hooks(category_profile.hook_patterns, band, saturated_hooks)
    payoff = _take_unique(category_profile.payoff_patterns, limit=6)
    structures = [s for s in category_profile.structure_patterns if s not in saturated_structures][:6]
    pacing_notes = _take_unique(craft_profile.pacing_rules, limit=4)
    emotional_beats = _emotional_beats_for_band(band, category_profile, payoff)

    forbidden = sorted(
        set(craft_profile.disallowed_copy_targets)
        | {f"avoid_saturated:{p}" for p in saturated}
    )

    min_required = _min_hooks_for_band(band, len(must_hit_hooks))

    length_min, length_max = _length_range(band, target_length)

    rationale = [
        f"band={band} for chapter {chapter_position}",
        f"sample_size={category_profile.sample_size}",
        f"saturated={len(saturated)} (suppressed)",
    ]
    if extra_safety_notes:
        rationale.append("extra_safety_notes applied")

    safety_boundary = craft_profile.safety_boundary or ""
    if extra_safety_notes:
        safety_boundary = "；".join(
            note for note in [safety_boundary, *extra_safety_notes] if note
        )

    confidence = min(craft_profile.confidence, category_profile.confidence)

    return ChapterMarketConstraints(
        chapter_position=chapter_position,
        band=band,
        category=category_profile.category,
        must_hit_hooks=must_hit_hooks,
        min_hooks_required=min_required,
        forbidden_patterns=forbidden,
        saturated_tropes=saturated,
        optimal_chapter_length_min=length_min,
        optimal_chapter_length_max=length_max,
        must_appear_emotional_beats=emotional_beats,
        payoff_patterns=payoff,
        structure_patterns=structures,
        pacing_notes=pacing_notes,
        safety_boundary=safety_boundary,
        confidence=confidence,
        rationale=rationale,
    )


def render_chapter_constraints_block(
    constraints: ChapterMarketConstraints | Mapping[str, Any] | None,
    *,
    language: str = "zh-CN",
    max_items: int = 6,
) -> str:
    """Render constraints as a prompt-ready text block."""

    payload = _to_payload(constraints)
    if not payload:
        return ""

    if language.lower().startswith("zh"):
        lines = ["【市场硬约束 — 本章必须满足】"]
        cp = payload.get("chapter_position")
        band = payload.get("band")
        if cp is not None and band:
            lines.append(f"- 章节位置: 第{cp}章（{band}阶段）")
        category = payload.get("category")
        if category:
            lines.append(f"- 类目: {category}")

        hooks = list(payload.get("must_hit_hooks") or [])[:max_items]
        if hooks:
            min_required = payload.get("min_hooks_required") or len(hooks)
            lines.append(f"- 必须命中至少 {min_required} 个钩子: " + "; ".join(hooks))

        emotional = list(payload.get("must_appear_emotional_beats") or [])[:max_items]
        if emotional:
            lines.append("- 必须出现的情绪节拍: " + "; ".join(emotional))

        payoffs = list(payload.get("payoff_patterns") or [])[:max_items]
        if payoffs:
            lines.append("- 回报模式（择其一兑现）: " + "; ".join(payoffs))

        forbidden = list(payload.get("forbidden_patterns") or [])[:max_items]
        if forbidden:
            lines.append("- 禁止落入的模式: " + "; ".join(forbidden))

        saturated = list(payload.get("saturated_tropes") or [])[:max_items]
        if saturated:
            lines.append("- 已审美疲劳套路（避让而非复刻）: " + "; ".join(saturated))

        length_range = payload.get("length_range") or [0, 0]
        if length_range and length_range[1]:
            lines.append(f"- 目标章节字数区间: {length_range[0]}–{length_range[1]} 字")

        pacing = list(payload.get("pacing_notes") or [])[:max_items]
        if pacing:
            lines.append("- 节奏要求: " + "; ".join(pacing))

        safety = payload.get("safety_boundary")
        if safety:
            lines.append(f"- 安全边界: {safety}")

        confidence = payload.get("confidence")
        if confidence is not None:
            lines.append(f"- 约束置信度: {float(confidence):.2f}")

        lines.append(
            "- 若本章未达到上述必命中数量，质检将判定不合格并触发重写。"
        )
        return "\n".join(lines)

    lines = ["[Market Hard Constraints — must satisfy this chapter]"]
    cp = payload.get("chapter_position")
    band = payload.get("band")
    if cp is not None and band:
        lines.append(f"- Chapter position: #{cp} ({band} band)")
    hooks = list(payload.get("must_hit_hooks") or [])[:max_items]
    if hooks:
        min_required = payload.get("min_hooks_required") or len(hooks)
        lines.append(f"- Must hit ≥ {min_required} hooks: " + "; ".join(hooks))
    forbidden = list(payload.get("forbidden_patterns") or [])[:max_items]
    if forbidden:
        lines.append("- Forbidden patterns: " + "; ".join(forbidden))
    return "\n".join(lines)


# ---------- internals ----------


def _band_for_chapter(position: int) -> str:
    if position <= 3:
        return "early"
    if position <= 30:
        return "rising"
    return "steady"


def _detect_saturated(pattern_lists: Sequence[Sequence[str]]) -> list[str]:
    if not pattern_lists:
        return []
    n = len(pattern_lists)
    if n == 0:
        return []
    counts: Counter[str] = Counter()
    for lst in pattern_lists:
        for pattern in set(lst):
            if pattern:
                counts[pattern] += 1
    threshold = max(2, int(_SATURATION_THRESHOLD * n))
    return [p for p, c in counts.items() if c >= threshold]


def _band_aware_hooks(
    hooks: Sequence[str],
    band: str,
    saturated: Sequence[str],
) -> list[str]:
    saturated_set = set(saturated)
    fresh = [h for h in hooks if h not in saturated_set]
    if band == "early":
        limit = 5
    elif band == "rising":
        limit = 4
    else:
        limit = 3
    return _take_unique(fresh, limit=limit)


def _emotional_beats_for_band(
    band: str,
    category: FanqieCategoryProfile,
    payoff_patterns: Sequence[str],
) -> list[str]:
    defaults = {
        "early": ["即时压力", "身份/处境反差", "可被截图的金句或冲突"],
        "rising": ["关系拉扯", "代价/损失的暴露", "递进式赌注上调"],
        "steady": ["旧账兑现", "盟友/敌人的立场翻转", "下一阶段的钩子预埋"],
    }
    base = list(defaults.get(band, []))
    for payoff in payoff_patterns[:3]:
        if payoff and payoff not in base:
            base.append(payoff)
    return base[:6]


def _min_hooks_for_band(band: str, available: int) -> int:
    target = {"early": 3, "rising": 2, "steady": 1}.get(band, 1)
    return min(target, available)


def _length_range(band: str, target: int | None) -> tuple[int, int]:
    default_min, default_max = _DEFAULT_LENGTH_RANGES[band]
    if target is None or target <= 0:
        return default_min, default_max
    half_band = max(200, int(target * 0.12))
    return max(800, target - half_band), target + half_band


def _take_unique(items: Sequence[str], *, limit: int) -> list[str]:
    seen: list[str] = []
    for item in items:
        cleaned = (item or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.append(cleaned)
        if len(seen) >= limit:
            break
    return seen


def _to_payload(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, ChapterMarketConstraints):
        return value.to_prompt_card()
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    return {}


__all__ = [
    "compile_chapter_constraints",
    "render_chapter_constraints_block",
]
