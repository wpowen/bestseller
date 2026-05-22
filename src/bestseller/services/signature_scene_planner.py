"""Signature Scene Planner — schedule memorable scene slots across a book.

The planner takes a target chapter count + cadence (default 1 signature
scene every 10 chapters) and produces a ``SignatureScenePlan`` with
explicit archetype, stake, intensity, and prompt-side mandate fields.

It does **not** try to invent the actual scene content — that lives with
the bible/outline. Its job is to make the slot existence non-negotiable
and the archetype + stake explicit, so the writing prompt can not
silently smooth over the scene into a flavorless transition chapter.

The default archetype rotation is calibrated against榜单 books'
historical patterns: revelation/oath/sacrifice/confrontation dominate
early; betrayal/apotheosis/farewell anchor later. Callers can override
with a custom ``archetype_rotation``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from bestseller.domain.signature_scene import (
    SignatureSceneArchetype,
    SignatureSceneMandate,
    SignatureScenePlan,
    SignatureSceneStake,
)

logger = logging.getLogger(__name__)


_DEFAULT_CADENCE = 10

# Golden Three mandates — pre-cadence positions that MUST have signature
# scenes. The original signature_scene_planner skipped chapters 1/2/3
# entirely, leaving the most retention-critical positions without any
# memorable-scene mandate. This is the fix.
#
# Archetypes chosen to mirror what top-tier serial fiction actually does
# in its golden three: chapter 1 subverts an expectation (revelation),
# chapter 2 proves the protagonist's agency (apotheosis-lite — a small
# but unmistakable demonstration of capability), chapter 3 plants a
# stake that the next 10 chapters will pay off (oath-bound).
_GOLDEN_THREE_DEFAULTS: tuple[tuple[int, SignatureSceneArchetype, SignatureSceneStake, float], ...] = (
    (1, SignatureSceneArchetype.REVELATION, SignatureSceneStake.IDENTITY_TRUTH, 0.85),
    (2, SignatureSceneArchetype.APOTHEOSIS, SignatureSceneStake.POWER_AUTHORITY, 0.88),
    (3, SignatureSceneArchetype.OATH_BOUND, SignatureSceneStake.LOYALTY_HONOR, 0.92),
)

_DEFAULT_ARCHETYPE_ROTATION: tuple[SignatureSceneArchetype, ...] = (
    SignatureSceneArchetype.REVELATION,
    SignatureSceneArchetype.OATH_BOUND,
    SignatureSceneArchetype.CONFRONTATION,
    SignatureSceneArchetype.SACRIFICE,
    SignatureSceneArchetype.BETRAYAL,
    SignatureSceneArchetype.UNVEILING_NAME,
    SignatureSceneArchetype.DEFIANCE,
    SignatureSceneArchetype.REUNION,
    SignatureSceneArchetype.APOTHEOSIS,
    SignatureSceneArchetype.FAREWELL,
)

_DEFAULT_STAKE_ROTATION: tuple[SignatureSceneStake, ...] = (
    SignatureSceneStake.IDENTITY_TRUTH,
    SignatureSceneStake.LOYALTY_HONOR,
    SignatureSceneStake.LIFE_DEATH,
    SignatureSceneStake.LOVE_LOSS,
    SignatureSceneStake.POWER_AUTHORITY,
    SignatureSceneStake.IDENTITY_TRUTH,
    SignatureSceneStake.FREEDOM_BONDAGE,
    SignatureSceneStake.LOVE_LOSS,
    SignatureSceneStake.LIFE_DEATH,
    SignatureSceneStake.LOYALTY_HONOR,
)

_ARCHETYPE_IMAGE_HINTS: dict[SignatureSceneArchetype, tuple[str, ...]] = {
    SignatureSceneArchetype.REVELATION: ("被揭开的封印", "灯下旧账", "尘封玉牌"),
    SignatureSceneArchetype.OATH_BOUND: ("血印一笔", "断箭为誓", "焚契立誓"),
    SignatureSceneArchetype.CONFRONTATION: ("剑指对方鼻尖", "崖头对峙", "一步不退"),
    SignatureSceneArchetype.SACRIFICE: ("为人挡剑", "替死投火", "最后一笑"),
    SignatureSceneArchetype.BETRAYAL: ("背后冷光", "熟悉的脸庞陌生的眼", "杯中毒"),
    SignatureSceneArchetype.UNVEILING_NAME: ("揭面/卸冠", "真名出口", "印玺现"),
    SignatureSceneArchetype.DEFIANCE: ("一人立于万军前", "断头钉地", "拒诏不跪"),
    SignatureSceneArchetype.REUNION: ("雨中重逢", "万人之中一眼", "断巷尽头"),
    SignatureSceneArchetype.APOTHEOSIS: ("登临破界", "天地为之让位", "一念成神"),
    SignatureSceneArchetype.FAREWELL: ("背影远去", "最后一次回望", "钟声三响"),
}

_ARCHETYPE_LINE_HINTS: dict[SignatureSceneArchetype, tuple[str, ...]] = {
    SignatureSceneArchetype.REVELATION: ("原来如此", "你早就知道", "我等了多久"),
    SignatureSceneArchetype.OATH_BOUND: ("此誓不破", "纵死不悔", "以血为证"),
    SignatureSceneArchetype.CONFRONTATION: ("再前一步，杀无赦", "你我之间，一步不让"),
    SignatureSceneArchetype.SACRIFICE: ("替我活下去", "这一剑，我替你接"),
    SignatureSceneArchetype.BETRAYAL: ("我从未把你当兄弟", "原来你也是"),
    SignatureSceneArchetype.UNVEILING_NAME: ("我才是真正的", "记住这个名字"),
    SignatureSceneArchetype.DEFIANCE: ("我偏不", "你们谁动一下试试"),
    SignatureSceneArchetype.REUNION: ("一别经年", "你还活着"),
    SignatureSceneArchetype.APOTHEOSIS: ("从此天地间，我自有道", "界破也是道"),
    SignatureSceneArchetype.FAREWELL: ("送君千里", "不必送了"),
}


def plan_signature_scenes(
    *,
    total_chapters: int,
    cadence: int = _DEFAULT_CADENCE,
    archetype_rotation: Sequence[SignatureSceneArchetype] | None = None,
    stake_rotation: Sequence[SignatureSceneStake] | None = None,
    intensity_curve: Sequence[float] | None = None,
    title_hints: Sequence[str] | None = None,
    summary_hints: Sequence[str] | None = None,
    payoff_targets: Sequence[Sequence[str]] | None = None,
    include_golden_three: bool = True,
) -> SignatureScenePlan:
    """Plan signature-scene mandates across a book.

    By default chapters 1, 2, 3 are *always* signature-scene mandate
    positions (the "golden three" — the chapters that decide whether a
    reader sticks past the first session). After the golden three, one
    mandate is scheduled every ``cadence`` chapters.

    Set ``include_golden_three=False`` to opt out (legacy behavior:
    first mandate at chapter ``cadence``).
    """

    if total_chapters < 1:
        raise ValueError("total_chapters must be >= 1")
    if cadence < 1:
        raise ValueError("cadence must be >= 1")

    archetypes = list(archetype_rotation or _DEFAULT_ARCHETYPE_ROTATION)
    stakes = list(stake_rotation or _DEFAULT_STAKE_ROTATION)

    golden_three_positions = _resolve_golden_three(
        total_chapters, include_golden_three=include_golden_three
    )
    cadence_positions = _slot_positions(total_chapters, cadence)
    # Remove cadence positions that collide with golden-three (e.g. cadence=2)
    cadence_positions = [p for p in cadence_positions if p not in {gt[0] for gt in golden_three_positions}]

    slot_positions = [gt[0] for gt in golden_three_positions] + cadence_positions
    intensity_values = _resolve_intensity_curve(intensity_curve, len(slot_positions))

    mandates: list[SignatureSceneMandate] = []
    golden_three_lookup = {gt[0]: gt for gt in golden_three_positions}
    cadence_idx = 0
    for idx, position in enumerate(slot_positions):
        if position in golden_three_lookup:
            _, archetype, stake, base_intensity = golden_three_lookup[position]
            intensity = max(intensity_values[idx], base_intensity)
        else:
            archetype = archetypes[cadence_idx % len(archetypes)]
            stake = stakes[cadence_idx % len(stakes)]
            intensity = intensity_values[idx]
            cadence_idx += 1

        image_hints = list(_ARCHETYPE_IMAGE_HINTS.get(archetype, ()))[:3]
        line_hints = list(_ARCHETYPE_LINE_HINTS.get(archetype, ()))[:3]

        title_hint = ""
        if title_hints and idx < len(title_hints):
            title_hint = title_hints[idx]
        summary = ""
        if summary_hints and idx < len(summary_hints):
            summary = summary_hints[idx]
        targets: list[str] = []
        if payoff_targets and idx < len(payoff_targets):
            targets = list(payoff_targets[idx])

        mandates.append(
            SignatureSceneMandate(
                chapter_position=position,
                archetype=archetype,
                stake=stake,
                title_hint=title_hint,
                summary=summary,
                must_include_image=image_hints,
                must_include_line=line_hints,
                must_invert=[],
                payoff_targets=targets,
                intensity_target=intensity,
                shareability_target=_shareability_target_for(archetype),
            )
        )

    return SignatureScenePlan(
        total_chapters=total_chapters,
        cadence=cadence,
        mandates=mandates,
    )


def render_signature_scene_block(
    mandate: SignatureSceneMandate | Mapping[str, Any] | None,
    *,
    language: str = "zh-CN",
) -> str:
    """Render an in-prompt mandate for the current chapter's signature scene."""

    payload = _to_payload(mandate)
    if not payload:
        return ""

    archetype = payload.get("archetype", "")
    stake = payload.get("stake", "")
    chapter_position = payload.get("chapter_position")
    title_hint = payload.get("title_hint", "")
    summary = payload.get("summary", "")
    images = list(payload.get("must_include_image") or [])
    lines_required = list(payload.get("must_include_line") or [])
    invert = list(payload.get("must_invert") or [])
    targets = list(payload.get("payoff_targets") or [])
    intensity = payload.get("intensity_target")
    shareability = payload.get("shareability_target")

    if language.lower().startswith("zh"):
        lines = ["【招牌场景指令 — 本章质检硬指标】"]
        if chapter_position is not None:
            lines.append(f"- 章节位置: 第 {int(chapter_position)} 章（招牌场景节点）")
        lines.append(f"- 场景原型: {archetype}（情感赌注: {stake}）")
        if intensity is not None:
            lines.append(f"- 强度目标: {float(intensity):.2f} / 1.0")
        if shareability is not None:
            lines.append(f"- 可传播度目标: {float(shareability):.2f} / 1.0")
        if title_hint:
            lines.append(f"- 标题方向: {title_hint}")
        if summary:
            lines.append(f"- 场景内核: {summary}")
        if images:
            lines.append("- 必须呈现的视觉意象（≥1 个）: " + "; ".join(images))
        if lines_required:
            lines.append("- 必须出现的台词/句式（择一改写出现）: " + "; ".join(lines_required))
        if invert:
            lines.append("- 必须反转的预期: " + "; ".join(invert))
        if targets:
            lines.append("- 必须兑现的伏笔: " + "; ".join(targets))
        lines.append(
            "- 本章不能写成'又一个过渡章'。读者要在此章产生'我要截图'的冲动；"
            "若达不到，会被招牌场景门退回重写。"
        )
        return "\n".join(lines)

    return f"[Signature Scene Mandate] Chapter {chapter_position}: {archetype} / {stake}"


# ---------- internals ----------


def _resolve_golden_three(
    total: int, *, include_golden_three: bool
) -> list[tuple[int, SignatureSceneArchetype, SignatureSceneStake, float]]:
    if not include_golden_three:
        return []
    return [
        (pos, arch, stake, intensity)
        for pos, arch, stake, intensity in _GOLDEN_THREE_DEFAULTS
        if pos <= total
    ]


def _slot_positions(total: int, cadence: int) -> list[int]:
    positions: list[int] = []
    position = cadence
    while position <= total:
        positions.append(position)
        position += cadence
    # If the book ends with a long tail not covered by cadence,
    # add one final signature scene at the last chapter.
    if positions and positions[-1] < total:
        positions.append(total)
    if not positions and total > 0:
        positions.append(total)
    return positions


def _resolve_intensity_curve(
    curve: Sequence[float] | None, slot_count: int
) -> list[float]:
    if curve is None:
        if slot_count <= 0:
            return []
        # Rising intensity: first slot 0.65, final slot 0.95
        if slot_count == 1:
            return [0.85]
        step = (0.95 - 0.65) / (slot_count - 1)
        return [round(0.65 + i * step, 3) for i in range(slot_count)]

    explicit = [_clamp01(float(v)) for v in curve]
    if len(explicit) == slot_count:
        return explicit
    if len(explicit) == 0:
        return _resolve_intensity_curve(None, slot_count)
    # If user-provided curve is shorter, extend with the last value;
    # if longer, truncate.
    if len(explicit) < slot_count:
        last = explicit[-1]
        return explicit + [last] * (slot_count - len(explicit))
    return explicit[:slot_count]


def _shareability_target_for(archetype: SignatureSceneArchetype) -> float:
    return {
        SignatureSceneArchetype.REVELATION: 0.85,
        SignatureSceneArchetype.OATH_BOUND: 0.80,
        SignatureSceneArchetype.CONFRONTATION: 0.85,
        SignatureSceneArchetype.SACRIFICE: 0.90,
        SignatureSceneArchetype.BETRAYAL: 0.80,
        SignatureSceneArchetype.UNVEILING_NAME: 0.85,
        SignatureSceneArchetype.DEFIANCE: 0.80,
        SignatureSceneArchetype.REUNION: 0.75,
        SignatureSceneArchetype.APOTHEOSIS: 0.92,
        SignatureSceneArchetype.FAREWELL: 0.85,
    }.get(archetype, 0.75)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _to_payload(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, SignatureSceneMandate):
        return value.to_prompt_card()
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    return {}


__all__ = [
    "plan_signature_scenes",
    "render_signature_scene_block",
]
