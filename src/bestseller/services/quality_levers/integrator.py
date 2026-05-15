"""High-level prompt builders for the quality-levers integration layer.

The legacy ``drafts.py`` and ``reviews.py`` modules compose their LLM
prompts from many small render fragments. Rather than spread 11
imports across each call site, this module exposes two facade
helpers that knit every relevant ``render_<lever>_block`` together
into a single string:

* :func:`build_writer_quality_levers_block` — for the writer prompt
* :func:`build_critic_quality_levers_block` — for the critic prompt

Both helpers degrade gracefully (empty input → empty output) so the
pipeline can opt-in lever-by-lever as the project's ``meta.yaml``
populates the relevant fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bestseller.services.distilled_strategy_compiler import (
    render_distilled_strategy_card_block,
)
from bestseller.services.emotion_driven_kernel import (
    render_emotion_driven_kernel_prompt_block,
)
from bestseller.services.quality_levers.chapter_position_profiles import (
    render_chapter_position_block,
)
from bestseller.services.quality_levers.chapter_signature_audit import (
    render_chapter_signature_block,
)
from bestseller.services.quality_levers.character_engine import (
    render_character_engine_profile_block,
    render_character_profile_block,
)
from bestseller.services.quality_levers.emotion_choreography import (
    render_emotion_choreography_block,
)
from bestseller.services.quality_levers.information_choreography import (
    render_information_choreography_block,
)
from bestseller.services.quality_levers.platform_profiles import (
    render_platform_profile_block,
)
from bestseller.services.quality_levers.prose_style_anchors import (
    render_style_anchor_block,
)
from bestseller.services.quality_levers.rejection_repair_playbook import (
    render_repair_actions_block,
)
from bestseller.services.quality_levers.rhythm_engineering import (
    render_rhythm_block,
)
from bestseller.services.quality_levers.sensory_inventory import (
    render_sensory_requirement_block,
)


@dataclass(frozen=True)
class WriterLeverContext:
    """Inputs needed to assemble the writer-side prompt fragment."""

    chapter_number: int
    language: str = "zh-CN"
    platform: str | None = None
    style_anchors: tuple[str, ...] = ()
    chapter_positions: tuple[str, ...] = ()
    chapter_role: str = "ordinary_chapter"
    scene_type: str | None = None
    scene_stimulus: str | None = None
    participating_character_ids: tuple[str, ...] = ()
    participating_character_profiles: tuple[dict[str, Any], ...] = ()
    rejection_cause_ids: tuple[str, ...] = ()
    distilled_strategy_card: dict[str, Any] | None = None
    emotion_driven_kernel: dict[str, Any] | None = None


@dataclass(frozen=True)
class CriticLeverContext:
    """Inputs needed to assemble the critic-side prompt fragment."""

    chapter_number: int
    language: str = "zh-CN"
    platform: str | None = None
    chapter_positions: tuple[str, ...] = ()
    distilled_strategy_card: dict[str, Any] | None = None


def _join(blocks: list[str]) -> str:
    """Drop empty fragments and join with double newlines."""

    return "\n\n".join(block for block in blocks if block)


def _profile_ids(profiles: tuple[dict[str, Any], ...]) -> set[str]:
    ids: set[str] = set()
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        raw_id = profile.get("character_id") or profile.get("id")
        profile_id = str(raw_id).strip() if raw_id is not None else ""
        if profile_id:
            ids.add(profile_id)
    return ids


def build_writer_quality_levers_block(context: WriterLeverContext) -> str:
    """Assemble the writer-side quality-levers prompt fragment.

    Returns an empty string when nothing applies — callers can append
    the result unconditionally:

    >>> fragment = build_writer_quality_levers_block(context)
    >>> if fragment:
    ...     prompt += "\\n\\n" + fragment
    """

    blocks: list[str] = []
    if context.platform:
        blocks.append(
            render_platform_profile_block(
                platform=context.platform,
                chapter_number=context.chapter_number,
                language=context.language,
            )
        )
    if context.chapter_positions:
        blocks.append(
            render_chapter_position_block(
                positions=context.chapter_positions,
                chapter_number=context.chapter_number,
            )
        )
    if context.style_anchors:
        blocks.append(render_style_anchor_block(anchor_ids=context.style_anchors))
    overridden_ids = _profile_ids(context.participating_character_profiles)
    character_ids = tuple(
        char_id
        for char_id in context.participating_character_ids
        if char_id not in overridden_ids
    )
    if character_ids:
        blocks.append(
            render_character_profile_block(
                character_ids=character_ids,
                scene_stimulus=context.scene_stimulus,
            )
        )
    if context.participating_character_profiles:
        blocks.append(
            render_character_engine_profile_block(
                context.participating_character_profiles,
                scene_stimulus=context.scene_stimulus,
            )
        )
    if context.scene_type:
        blocks.append(
            render_sensory_requirement_block(scene_type=context.scene_type)
        )
    # Phase 4 — these renderers do not require additional inputs.
    blocks.append(render_chapter_signature_block(chapter_role=context.chapter_role))
    blocks.append(render_rhythm_block())
    blocks.append(render_emotion_choreography_block())
    blocks.append(
        render_information_choreography_block(chapter_number=context.chapter_number)
    )
    if context.emotion_driven_kernel:
        blocks.append(
            render_emotion_driven_kernel_prompt_block(
                context.emotion_driven_kernel,
                chapter_number=context.chapter_number,
                language=context.language,
            )
        )
    if context.rejection_cause_ids:
        blocks.append(
            render_repair_actions_block(cause_ids=context.rejection_cause_ids)
        )
    if context.distilled_strategy_card:
        strategy_block = render_distilled_strategy_card_block(
            context.distilled_strategy_card,
            phase="craft",
            language=context.language,
        )
        if strategy_block:
            strategy_instruction = (
                "- In prose, express only state change, cost, and reader reward; "
                "do not mention strategy cards, mechanism labels, or planning terms."
                if context.language.lower().startswith("en")
                else (
                    "- 写入正文时只能体现状态变化、代价和读者奖励; "
                    "禁止出现策略卡、机制名或规划术语。"
                )
            )
            blocks.append(
                strategy_block
                + "\n"
                + strategy_instruction
            )
    return _join(blocks)


def build_critic_quality_levers_block(context: CriticLeverContext) -> str:
    """Assemble the critic-side quality-levers prompt fragment.

    Critic gets a slimmer block: it doesn't need character / sensory
    / rhythm / emotion contracts (those are writer obligations) — it
    needs to know the platform + position so it can run the matching
    hard-gate evaluation.
    """

    blocks: list[str] = []
    if context.platform:
        blocks.append(
            render_platform_profile_block(
                platform=context.platform,
                chapter_number=context.chapter_number,
                language=context.language,
            )
        )
    if context.chapter_positions:
        blocks.append(
            render_chapter_position_block(
                positions=context.chapter_positions,
                chapter_number=context.chapter_number,
            )
        )
    if context.distilled_strategy_card:
        strategy_block = render_distilled_strategy_card_block(
            context.distilled_strategy_card,
            phase="chapter_outline",
            language=context.language,
        )
        if strategy_block:
            strategy_instruction = (
                "- During review, check whether the chapter changed strategy state, "
                "paid a cost, and avoided strategy/mechanism vocabulary leakage."
                if context.language.lower().startswith("en")
                else "- 审校时检查章节是否真正改变策略状态变量、付出代价, 并避免泄露策略/机制术语。"
            )
            blocks.append(
                strategy_block
                + "\n"
                + strategy_instruction
            )
    return _join(blocks)
