"""Reader Persona Simulator — deterministic multi-persona chapter scoring.

The simulator takes a generated chapter's text plus a small signal pack
(hook count, payoff count, voice DNA observation, market constraint hit
count, etc.) and produces ``PersonaSimulationResult`` — N persona scores
plus aggregated abandon-rate and per-channel directives that feed into
the next chapter's prompt.

Design choices:

* **Deterministic core**: scoring is a weighted linear combination of
  signal channels. Same inputs → same outputs. No LLM in the hot path.
* **Configurable personas**: ``default_personas()`` ships 7 representative
  Chinese serialized-fiction archetypes covering the main reader segments
  that drive 番茄/起点 rankings. Callers can override or extend.
* **Pluggable evidence**: ``ChapterSignalPack`` is an explicit DTO so the
  caller assembles the evidence from whatever tools they have available
  (hype_engine, voice_signature, market_constraint_compiler, reviews
  scores). No tight coupling to a particular pipeline shape.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from bestseller.domain.reader_persona import (
    PersonaScore,
    PersonaSimulationResult,
    PersonaWeights,
    ReaderPersona,
)


@dataclass(frozen=True)
class ChapterSignalPack:
    """Evidence used by the persona simulator.

    All fields are 0-anchored numerics; the simulator normalizes internally.
    """

    chapter_position: int
    chapter_text_chars: int
    hook_count: int
    payoff_count: int
    cliffhanger_strength: float  # 0..1
    voice_dna_drift: float  # 0..1 (0 = on target, 1 = totally off)
    market_hooks_hit: int
    market_hooks_required: int
    novelty_score: float  # 0..1
    consistency_score: float  # 0..1 (1 = perfectly consistent)
    emotional_beat_count: int
    saturated_trope_hits: int
    target_length_min: int
    target_length_max: int
    dialogue_ratio: float
    action_ratio: float
    interior_ratio: float
    prose_quality_score: float = 0.7  # external critic, default neutral


# ---------- default personas ----------


def default_personas() -> list[ReaderPersona]:
    """Return the seven built-in reader personas.

    These cover the main reader segments that drive Chinese serialized
    fiction ranking signals. Adjust freely; the simulator does not assume
    this exact set.
    """

    return [
        ReaderPersona(
            key="laobai",
            label="老白书虫",
            description="资深读者，看过 100+ 长篇，对套路敏感，1 章不爽就弃书。",
            weights=PersonaWeights(
                hook_density=1.3,
                pacing=1.1,
                novelty=1.6,
                prose_quality=1.0,
                emotional_impact=0.9,
                consistency=1.3,
                payoff_density=1.1,
            ),
            abandon_threshold=0.55,
            saturated_trope_tolerance=0.15,
            population_share=0.18,
        ),
        ReaderPersona(
            key="commute",
            label="通勤爽文党",
            description="碎片化阅读，节奏第一，描写过多就弃；爽点密度核心需求。",
            weights=PersonaWeights(
                hook_density=1.4,
                pacing=1.6,
                novelty=0.7,
                prose_quality=0.6,
                emotional_impact=0.8,
                consistency=0.7,
                payoff_density=1.5,
            ),
            abandon_threshold=0.45,
            saturated_trope_tolerance=0.7,
            population_share=0.30,
        ),
        ReaderPersona(
            key="kaoju",
            label="考据党",
            description="设定/世界观漏洞零容忍，发现矛盾会暴怒并写差评。",
            weights=PersonaWeights(
                hook_density=0.7,
                pacing=0.8,
                novelty=0.9,
                prose_quality=1.0,
                emotional_impact=0.7,
                consistency=2.0,
                payoff_density=0.9,
            ),
            abandon_threshold=0.4,
            saturated_trope_tolerance=0.5,
            population_share=0.07,
        ),
        ReaderPersona(
            key="emotion",
            label="情感党",
            description="感情戏权重 ×3，关系拉扯和情绪节拍是主要追书动力。",
            weights=PersonaWeights(
                hook_density=0.8,
                pacing=0.8,
                novelty=0.9,
                prose_quality=1.2,
                emotional_impact=2.2,
                consistency=1.0,
                payoff_density=0.9,
            ),
            abandon_threshold=0.4,
            saturated_trope_tolerance=0.55,
            population_share=0.15,
        ),
        ReaderPersona(
            key="vip",
            label="氪金月票党",
            description="爽点-付费转化敏感，每章都要有'我赚到了'的兑现感。",
            weights=PersonaWeights(
                hook_density=1.5,
                pacing=1.2,
                novelty=0.8,
                prose_quality=0.8,
                emotional_impact=1.0,
                consistency=1.0,
                payoff_density=1.7,
            ),
            abandon_threshold=0.5,
            saturated_trope_tolerance=0.6,
            population_share=0.10,
        ),
        ReaderPersona(
            key="newbie",
            label="新人小白",
            description="第一次看长篇，被钩子和金句留下，对成熟套路无免疫。",
            weights=PersonaWeights(
                hook_density=1.2,
                pacing=1.0,
                novelty=0.6,
                prose_quality=0.9,
                emotional_impact=1.1,
                consistency=0.8,
                payoff_density=1.2,
            ),
            abandon_threshold=0.35,
            saturated_trope_tolerance=0.9,
            population_share=0.15,
        ),
        ReaderPersona(
            key="literati",
            label="文笔党",
            description="句子质感和语言美学权重高，节奏可以慢，但绝不能糙。",
            weights=PersonaWeights(
                hook_density=0.7,
                pacing=0.6,
                novelty=1.1,
                prose_quality=2.2,
                emotional_impact=1.2,
                consistency=1.1,
                payoff_density=0.7,
            ),
            abandon_threshold=0.5,
            saturated_trope_tolerance=0.3,
            population_share=0.05,
        ),
    ]


# ---------- simulator ----------


def simulate_readers(
    signals: ChapterSignalPack,
    *,
    personas: Sequence[ReaderPersona] | None = None,
) -> PersonaSimulationResult:
    """Run all personas against one chapter's signal pack."""

    if personas is None:
        personas = default_personas()
    personas = list(personas)
    if not personas:
        raise ValueError("simulate_readers requires at least one persona")

    channel_norm = _normalize_channels(signals)

    per_persona: list[PersonaScore] = []
    for persona in personas:
        per_persona.append(_score_for_persona(persona, signals, channel_norm))

    total_share = sum(p.population_share for p in personas) or 1.0

    weighted_score = (
        sum(
            score.overall_score * persona.population_share
            for score, persona in zip(per_persona, personas, strict=True)
        )
        / total_share
    )
    abandon_rate = (
        sum(
            score.abandon_probability * persona.population_share
            for score, persona in zip(per_persona, personas, strict=True)
        )
        / total_share
    )

    high_risk = [
        s.persona_label
        for s in per_persona
        if s.abandon_probability >= 0.6
    ]

    aggregated_concerns = _aggregate_concerns(per_persona)
    directives = _build_next_chapter_directives(per_persona, signals)

    return PersonaSimulationResult(
        chapter_position=signals.chapter_position,
        per_persona=per_persona,
        weighted_score=_clamp01(weighted_score),
        abandon_rate=_clamp01(abandon_rate),
        high_risk_personas=high_risk,
        aggregated_concerns=aggregated_concerns,
        next_chapter_directives=directives,
    )


def render_persona_feedback_block(
    result: PersonaSimulationResult | Mapping[str, Any] | None,
    *,
    language: str = "zh-CN",
    max_items: int = 6,
) -> str:
    """Render a prompt-ready feedback block for the *next* chapter."""

    payload = _to_payload(result)
    if not payload:
        return ""

    if language.lower().startswith("zh"):
        lines = ["【上章读者画像反馈 — 本章必须响应】"]
        cp = payload.get("chapter_position")
        if cp is not None:
            lines.append(f"- 反馈来源: 第 {int(cp)} 章")

        weighted = payload.get("weighted_score")
        abandon = payload.get("abandon_rate")
        if weighted is not None:
            lines.append(
                f"- 加权评分: {float(weighted):.2f}  /  整体弃书率: {float(abandon or 0):.2f}"
            )

        high_risk = list(payload.get("high_risk_personas") or [])[:max_items]
        if high_risk:
            lines.append("- 高风险读者群: " + "; ".join(high_risk))

        concerns = list(payload.get("aggregated_concerns") or [])[:max_items]
        if concerns:
            lines.append("- 主要不满: " + "; ".join(concerns))

        directives = list(payload.get("next_chapter_directives") or [])[:max_items]
        if directives:
            lines.append("- 本章必须执行的修正:")
            for directive in directives:
                lines.append(f"  · {directive}")

        lines.append(
            "- 如果直接忽略以上修正，本章质检将判定不合格并退回重写。"
        )
        return "\n".join(lines)

    lines = ["[Prior-chapter Reader Persona Feedback — must address]"]
    cp = payload.get("chapter_position")
    if cp is not None:
        lines.append(f"- From chapter: {int(cp)}")
    if payload.get("weighted_score") is not None:
        lines.append(
            f"- Weighted score: {float(payload['weighted_score']):.2f}  "
            f"abandon-rate: {float(payload.get('abandon_rate') or 0):.2f}"
        )
    high_risk = list(payload.get("high_risk_personas") or [])[:max_items]
    if high_risk:
        lines.append("- High-risk personas: " + "; ".join(high_risk))
    directives = list(payload.get("next_chapter_directives") or [])[:max_items]
    if directives:
        lines.append("- Required next-chapter corrections:")
        for directive in directives:
            lines.append(f"  · {directive}")
    return "\n".join(lines)


# ---------- internals ----------


def _normalize_channels(s: ChapterSignalPack) -> dict[str, float]:
    """Translate raw signals into 0..1 channel scores."""

    hook_density_score = _sigmoid_around(s.hook_count, midpoint=3, slope=0.7)
    payoff_density_score = _sigmoid_around(s.payoff_count, midpoint=2, slope=0.8)

    if s.target_length_min and s.target_length_max:
        target_mid = (s.target_length_min + s.target_length_max) / 2
        target_width = max(1, (s.target_length_max - s.target_length_min) / 2)
        length_gap = abs(s.chapter_text_chars - target_mid) / target_width
        pacing_score = _clamp01(1.0 - 0.6 * min(2.0, length_gap))
    else:
        pacing_score = 0.6

    novelty_score = _clamp01(s.novelty_score)
    prose_score = _clamp01(s.prose_quality_score)
    consistency_score = _clamp01(s.consistency_score)

    emotional_score = _sigmoid_around(s.emotional_beat_count, midpoint=2, slope=0.9)

    cliff_score = _clamp01(s.cliffhanger_strength)

    market_hit_score = (
        min(1.0, s.market_hooks_hit / s.market_hooks_required)
        if s.market_hooks_required > 0
        else 0.6
    )

    voice_match_score = _clamp01(1.0 - s.voice_dna_drift)

    saturated_penalty = _clamp01(s.saturated_trope_hits / 3.0)

    return {
        "hook_density": _clamp01(
            0.65 * hook_density_score + 0.35 * cliff_score
        ),
        "payoff_density": payoff_density_score,
        "pacing": pacing_score,
        "novelty": _clamp01(0.7 * novelty_score + 0.3 * (1.0 - saturated_penalty)),
        "prose_quality": _clamp01(0.7 * prose_score + 0.3 * voice_match_score),
        "emotional_impact": emotional_score,
        "consistency": consistency_score,
        "market_alignment": market_hit_score,
        "voice_match": voice_match_score,
        "saturation_penalty": saturated_penalty,
    }


def _score_for_persona(
    persona: ReaderPersona,
    signals: ChapterSignalPack,
    channels: Mapping[str, float],
) -> PersonaScore:
    w = persona.weights
    pairs = [
        ("hook_density", w.hook_density),
        ("payoff_density", w.payoff_density),
        ("pacing", w.pacing),
        ("novelty", w.novelty),
        ("prose_quality", w.prose_quality),
        ("emotional_impact", w.emotional_impact),
        ("consistency", w.consistency),
    ]

    total_weight = sum(weight for _, weight in pairs)
    weighted_sum = sum(channels[channel] * weight for channel, weight in pairs)
    base = weighted_sum / total_weight if total_weight > 0 else 0.0

    saturation_penalty = channels["saturation_penalty"] * (
        1.0 - persona.saturated_trope_tolerance
    )
    base = _clamp01(base - 0.25 * saturation_penalty)

    market_bonus = (channels["market_alignment"] - 0.5) * 0.10
    base = _clamp01(base + market_bonus)

    voice_match = channels["voice_match"]
    if persona.key in {"literati", "laobai", "kaoju"}:
        base = _clamp01(base + (voice_match - 0.5) * 0.15)

    abandon_probability = _clamp01(
        _logistic(persona.abandon_threshold - base, slope=8.0)
    )

    concerns: list[str] = []
    likes: list[str] = []

    if channels["hook_density"] < 0.4 and w.hook_density >= 1.0:
        concerns.append("钩子密度不足，章节末尾留不住人")
    if channels["payoff_density"] < 0.35 and w.payoff_density >= 1.0:
        concerns.append("缺少明确兑现，'白看一章'的感觉")
    if channels["pacing"] < 0.45:
        concerns.append("节奏偏离目标字数区间，体感拖沓或太赶")
    if channels["novelty"] < 0.5 and w.novelty >= 1.0:
        concerns.append("创意密度低，套路化太重")
    if channels["consistency"] < 0.5 and w.consistency >= 1.3:
        concerns.append("出现设定/逻辑矛盾，可信度受损")
    if channels["emotional_impact"] < 0.4 and w.emotional_impact >= 1.5:
        concerns.append("情绪节拍稀薄，关系/赌注没有被推进")
    if channels["voice_match"] < 0.55 and persona.key == "literati":
        concerns.append("文笔偏离目标声纹，语感差")
    if channels["saturation_penalty"] > 0.5 and persona.saturated_trope_tolerance < 0.4:
        concerns.append("踩到已审美疲劳的套路")

    if channels["hook_density"] >= 0.7:
        likes.append("钩子密度饱满")
    if channels["payoff_density"] >= 0.7:
        likes.append("兑现清晰")
    if channels["emotional_impact"] >= 0.7 and w.emotional_impact >= 1.5:
        likes.append("情绪节拍到位")
    if channels["novelty"] >= 0.7 and w.novelty >= 1.3:
        likes.append("创意上跳出了平均网文")

    demand = _persona_demand(persona, channels)

    return PersonaScore(
        persona_key=persona.key,
        persona_label=persona.label,
        overall_score=base,
        abandon_probability=abandon_probability,
        channel_scores={k: float(v) for k, v in channels.items()},
        concerns=concerns,
        likes=likes,
        next_chapter_demand=demand,
    )


def _persona_demand(persona: ReaderPersona, channels: Mapping[str, float]) -> str:
    weakest = min(
        ("hook_density", "payoff_density", "pacing", "novelty",
         "prose_quality", "emotional_impact", "consistency"),
        key=lambda k: channels[k] * _persona_weight(persona, k),
    )
    label_map = {
        "hook_density": "下一章必须把钩子密度提上去",
        "payoff_density": "下一章必须兑现一个明确的赌注",
        "pacing": "下一章必须把节奏拉回目标字数区间",
        "novelty": "下一章必须有一个'非平均'的桥段或概念跨界",
        "prose_quality": "下一章必须打磨句子质感，回到目标声纹",
        "emotional_impact": "下一章必须推一次关系/情绪节拍",
        "consistency": "下一章必须修复已暴露的设定问题",
    }
    return label_map.get(weakest, "下一章保持当前曲线")


def _persona_weight(persona: ReaderPersona, channel: str) -> float:
    w = persona.weights
    return {
        "hook_density": w.hook_density,
        "payoff_density": w.payoff_density,
        "pacing": w.pacing,
        "novelty": w.novelty,
        "prose_quality": w.prose_quality,
        "emotional_impact": w.emotional_impact,
        "consistency": w.consistency,
    }.get(channel, 1.0)


def _aggregate_concerns(scores: Sequence[PersonaScore]) -> list[str]:
    counter: Counter[str] = Counter()
    for s in scores:
        for c in s.concerns:
            counter[c] += 1
    return [c for c, _ in counter.most_common(8)]


def _build_next_chapter_directives(
    scores: Sequence[PersonaScore],
    signals: ChapterSignalPack,
) -> list[str]:
    demands = Counter(s.next_chapter_demand for s in scores if s.next_chapter_demand)
    base_directives = [d for d, _ in demands.most_common(4)]

    if signals.market_hooks_required > 0:
        miss = signals.market_hooks_required - signals.market_hooks_hit
        if miss > 0:
            base_directives.append(
                f"本章漏掉了 {miss} 个市场必命中钩子，下一章必须补齐"
            )

    if signals.voice_dna_drift > 0.35:
        base_directives.append(
            f"上一章声纹漂移 {signals.voice_dna_drift:.2f}，下一章必须收回目标声纹"
        )

    if signals.saturated_trope_hits >= 2:
        base_directives.append(
            f"上一章踩到 {signals.saturated_trope_hits} 个已审美疲劳套路，下一章必须换花样"
        )

    return base_directives


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _sigmoid_around(value: float, *, midpoint: float, slope: float) -> float:
    return _clamp01(_logistic((value - midpoint) * slope))


def _logistic(x: float, *, slope: float = 1.0) -> float:
    try:
        from math import exp

        return 1.0 / (1.0 + exp(-slope * x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def _to_payload(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, PersonaSimulationResult):
        return value.to_feedback_card()
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    return {}


__all__ = [
    "ChapterSignalPack",
    "default_personas",
    "simulate_readers",
    "render_persona_feedback_block",
]
