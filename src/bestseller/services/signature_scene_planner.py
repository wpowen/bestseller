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
    MANDATE_STATUS_SKELETON,
    SignatureSceneArchetype,
    SignatureSceneMandate,
    SignatureScenePlan,
    SignatureSceneStake,
)

logger = logging.getLogger(__name__)


_DEFAULT_CADENCE = 10

# R25 — outline-derived mandate targets. When a chapter outline exists, each
# mandate gets at most this many chapter-specific signature images and a
# summary trimmed to this many chars, derived deterministically (no LLM).
_OUTLINE_IMAGE_LIMIT = 2
_OUTLINE_SUMMARY_MAX_CHARS = 120

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

# Genre-NEUTRAL archetype guidance — concept descriptions only, never anchor
# phrases. The framework provides the *mechanism* (archetype slots, verbatim
# anchor protocol, validation); concrete anchor content must come from the
# book itself (imagery system / premise) via ``anchor_images``/``anchor_lines``.
# The previous hardcoded image/line dictionaries were xianxia/detective
# flavored and made every other genre's signature mandate unsatisfiable noise.
_ARCHETYPE_GUIDANCE: dict[SignatureSceneArchetype, str] = {
    SignatureSceneArchetype.REVELATION: "一件被长期遮蔽的事实/身份/物件在本章被当场揭开，读者与角色同时看见",
    SignatureSceneArchetype.OATH_BOUND: "角色以可见的代价或仪式立下不可反悔的承诺",
    SignatureSceneArchetype.CONFRONTATION: "双方在不可退让的立场上正面对峙，张力推到顶点",
    SignatureSceneArchetype.SACRIFICE: "角色为他人/目标当场付出重大可见代价",
    SignatureSceneArchetype.BETRAYAL: "信任关系在读者眼前当场断裂，背叛以具体动作呈现",
    SignatureSceneArchetype.UNVEILING_NAME: "真实身份/名号当众揭晓，在场者的反应可见",
    SignatureSceneArchetype.DEFIANCE: "角色公开违抗压倒性的权威/规则并承担后果",
    SignatureSceneArchetype.REUNION: "重要关系在长久分离后重逢，场面具体可感",
    SignatureSceneArchetype.APOTHEOSIS: "角色完成质变/登顶时刻，世界对其态度可见地改变",
    SignatureSceneArchetype.FAREWELL: "重要角色以具体的动作与场景完成离别",
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
    anchor_images: Sequence[str] | None = None,
    anchor_lines: Sequence[str] | None = None,
    chapter_outline: Mapping[int | str, Mapping[str, Any]] | None = None,
) -> SignatureScenePlan:
    """Plan signature-scene mandates across a book.

    By default chapters 1, 2, 3 are *always* signature-scene mandate
    positions (the "golden three" — the chapters that decide whether a
    reader sticks past the first session). After the golden three, one
    mandate is scheduled every ``cadence`` chapters.

    Set ``include_golden_three=False`` to opt out (legacy behavior:
    first mandate at chapter ``cadence``).

    ``anchor_images`` / ``anchor_lines`` are BOOK-DERIVED verbatim anchor
    phrases (e.g. from the book's imagery system); they become
    ``must_include_image`` / ``must_include_line`` on every mandate so the
    same signature motifs recur across slots — book identity by
    construction. Without them mandates carry no literal anchors and the
    signature gate validates purely semantically. The framework never
    supplies genre-flavored anchor content itself.

    ``chapter_outline`` (R25) maps chapter position → outline hints
    (``title`` / ``goal`` or ``chapter_goal`` / ``signature_images``). When an
    entry exists for a mandate position, concrete targets are derived
    deterministically: ``must_include_image`` from the chapter's own scene
    signature images (first 2), ``summary`` from the chapter goal
    (first 120 chars), ``title_hint`` from the chapter title. Mandates
    that end up with no concrete target at all are marked
    ``status="skeleton"`` and are never rendered into the writer prompt —
    an empty shell is not an acceptance standard.
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

        outline_title, outline_summary, outline_images = _outline_hints_for(
            chapter_outline, position
        )

        image_hints = [
            str(s).strip() for s in (anchor_images or ()) if str(s).strip()
        ][:3]
        # Chapter-specific signature images beat book-global anchors: the
        # outline already committed THIS chapter to those concrete images.
        if outline_images:
            image_hints = outline_images
        line_hints = [
            str(s).strip() for s in (anchor_lines or ()) if str(s).strip()
        ][:3]

        title_hint = ""
        if title_hints and idx < len(title_hints):
            title_hint = title_hints[idx]
        if not title_hint:
            title_hint = outline_title
        summary = ""
        if summary_hints and idx < len(summary_hints):
            summary = summary_hints[idx]
        if not summary:
            summary = outline_summary
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


def _outline_hints_for(
    chapter_outline: Mapping[int | str, Mapping[str, Any]] | None,
    position: int,
) -> tuple[str, str, list[str]]:
    """Deterministically derive (title_hint, summary, images) for one slot.

    Tolerates ``str`` chapter keys (JSON round-trips) and both ``goal`` /
    ``chapter_goal`` field spellings. Returns empty values when the outline
    has no entry for ``position``.
    """

    if not chapter_outline:
        return "", "", []
    entry = chapter_outline.get(position)
    if entry is None:
        entry = chapter_outline.get(str(position))
    if not isinstance(entry, Mapping):
        return "", "", []
    title = str(entry.get("title") or "").strip()
    goal = str(entry.get("goal") or entry.get("chapter_goal") or "").strip()
    raw_images = entry.get("signature_images") or entry.get("signature_image") or ()
    if isinstance(raw_images, str):
        raw_images = [raw_images]
    images = [
        str(item).strip()
        for item in raw_images
        if str(item or "").strip()
    ][:_OUTLINE_IMAGE_LIMIT]
    return title, goal[:_OUTLINE_SUMMARY_MAX_CHARS], images


def _archetype_guidance_for(archetype: Any) -> str:
    """Resolve genre-neutral guidance for an archetype enum or its value."""

    if isinstance(archetype, SignatureSceneArchetype):
        return _ARCHETYPE_GUIDANCE.get(archetype, "")
    raw = str(archetype or "").strip().lower()
    for key, guidance in _ARCHETYPE_GUIDANCE.items():
        if key.value == raw:
            return guidance
    return ""


def render_signature_scene_block(
    mandate: SignatureSceneMandate | Mapping[str, Any] | None,
    *,
    language: str = "zh-CN",
) -> str | None:
    """Render an in-prompt mandate for the current chapter's signature scene.

    Returns ``None`` for a skeleton mandate (R25): an empty shell carries no
    concrete target, so it must not be handed to the writer — the writer can
    not be examined against a standard that was never delivered.
    """

    payload = _to_payload(mandate)
    if not payload:
        return ""
    if str(payload.get("status") or "").strip().lower() == MANDATE_STATUS_SKELETON:
        return None

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
            # Verbatim requirement: the signature gate validates by exact
            # substring match — a paraphrased image scores zero. Keep the
            # anchor phrase intact and build the surrounding prose freely.
            lines.append(
                "- 必须呈现的本书核心意象（以下短语至少 1 个【原词完整出现】，"
                "前后文自由发挥，不得改写或拆散短语本身）: " + "; ".join(images)
            )
        if lines_required:
            lines.append(
                "- 必须出现的台词（以下至少 1 句【原句完整出现】在对白或心声中，"
                "可在其前后自然衔接，但句子本身不得改写）: "
                + "; ".join(lines_required)
            )
        if not images and not lines_required:
            # No book-derived anchors available — give the genre-neutral
            # archetype concept instead. Validation falls to the semantic
            # judge in this case, so no verbatim demand is made.
            guidance = _archetype_guidance_for(archetype)
            if guidance:
                lines.append(f"- 场景概念要求（按本书自身的世界观具象化）: {guidance}")
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
