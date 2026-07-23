from __future__ import annotations

import copy
import logging
import re
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import delete as _sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bestseller.domain.enums import ArtifactType, ChapterStatus, SceneStatus, WorkflowStatus
from bestseller.domain.narrative import NarrativeGraphMaterializationResult
from bestseller.domain.narrative_tree import NarrativeTreeMaterializationResult
from bestseller.domain.project import ChapterCreate, SceneCardCreate, VolumeCreate
from bestseller.domain.story_bible import StoryBibleMaterializationResult
from bestseller.domain.workflow import (
    ChapterOutlineBatchInput,
    ChapterOutlineInput,
    WorkflowMaterializationResult,
)
from bestseller.infra.db.models import (
    ChapterModel,
    CharacterModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    SceneCardModel,
    SceneDraftVersionModel,
    WorkflowRunModel,
    WorkflowStepRunModel,
)
from bestseller.services.bible_gate import (
    build_draft_from_materialization_content,
    validate_bible_completeness,
)
from bestseller.services.chapter_causality_gate import (
    ChapterCausalityResult,
    chapter_causality_report_to_dict,
    evaluate_chapter_causality_contract,
    is_methodology_causality_finding,
)
from bestseller.services.ensemble_arc_progress_gate import (
    EnsembleArcReport,
    scan_ensemble_arc_progress,
)
from bestseller.services.hook_ledger import is_methodology_v2_enabled
from bestseller.services.invariants import invariants_from_dict
from bestseller.services.methodology_lineage import attach_methodology_lineage
from bestseller.services.methodology_overlay import (
    methodology_contract_blocks,
    methodology_contract_requires_checks,
    normalize_chapter_overlay,
    normalize_scene_overlay,
    resolve_methodology_contract_mode,
)
from bestseller.services.methodology_selection_engine import select_lineage_for_chapter_outline
from bestseller.services.narrative import rebuild_narrative_graph
from bestseller.services.narrative_contracts import (
    _extract_purpose_character_names,
    _identity_index_from_manifest,
    _is_generic_time_label,
    _normalize_identity_token,
    build_identity_manifest,
    repair_legacy_foundation_identity_locks,
    validate_chapter_plan_contract,
    validate_foundation_identity_contract,
)
from bestseller.services.narrative_tree import rebuild_narrative_tree
from bestseller.services.planning_readiness_gate import (
    evaluate_chapter_outline_batch_planning_readiness,
)
from bestseller.services.prewrite_quality_profile import strict_blocks
from bestseller.services.projects import (
    create_chapter,
    create_or_get_volume,
    create_scene_card,
    get_project_by_slug,
)
from bestseller.services.quality_gates_config import get_quality_gates_config
from bestseller.services.retrieval import refresh_story_bible_retrieval_index
from bestseller.services.story_bible import (
    apply_book_spec,
    parse_cast_spec_input,
    upsert_cast_spec,
    upsert_volume_plan,
    upsert_world_spec,
)
from bestseller.services.truth_version import truth_metadata_for_workflow
from bestseller.services.word_targets import (
    normalize_chapter_word_target,
    scene_word_target_for_chapter,
)
from bestseller.services.world_expansion import refresh_world_expansion_boundaries
from bestseller.settings import load_settings

logger = logging.getLogger(__name__)


WORKFLOW_TYPE_MATERIALIZE_CHAPTER_OUTLINE = "materialize_chapter_outline_batch"
WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE = "materialize_story_bible"
WORKFLOW_TYPE_MATERIALIZE_NARRATIVE_GRAPH = "materialize_narrative_graph"
WORKFLOW_TYPE_MATERIALIZE_NARRATIVE_TREE = "materialize_narrative_tree"

# Max rounds of outline commercial-judge feedback fed back into regeneration
# before giving up and leaving the block for human review (bounds token spend).
_MAX_OUTLINE_COMMERCIAL_REPAIR_ROUNDS = 3

_MATERIALIZATION_MUTABLE_CHAPTER_STATUSES = {
    ChapterStatus.PLANNED.value,
    ChapterStatus.OUTLINING.value,
}
_MATERIALIZATION_MUTABLE_SCENE_STATUSES = {
    SceneStatus.PLANNED.value,
}

# ── R19: scene capacity matching ────────────────────────────────────────────
# Average prose words a single narrative obligation (a story-purpose sentence,
# an exit-state commitment, a dialogue beat, a participant to stage) costs to
# honor. Deliberately coarse — the goal is catching gross density/word-target
# mismatch, not precise budgeting.
_SCENE_CAPACITY_WORDS_PER_OBLIGATION = 120
# A scene is flagged only when the estimated word demand exceeds its target by
# this ratio — small overshoot is normal writer headroom.
_SCENE_CAPACITY_OVERFLOW_RATIO = 1.3
# Platform bandwidth: a chapter's total scene budget never gets raised above
# this many words by the capacity pass.
_SCENE_CAPACITY_CHAPTER_WORD_CAP = 3500

# ── R21: metadata-narrative coherence ───────────────────────────────────────
# Deep chapter metadata fields that can survive outline rewrites as stale
# residue completely disconnected from the narrative layer (goal/conflict/
# hook/scenes) — and then get fed verbatim into writer prompts.
_METADATA_COHERENCE_FIELDS = ("key_reveals", "world_state_deltas", "location_refs")
# Minimum character-2-gram overlap between a metadata field's text and the
# chapter's narrative text. Below this, the field is treated as residue.
_METADATA_COHERENCE_MIN_OVERLAP = 0.05

# ── R14: forced materialization active-draft guard ─────────────────────────
# A chapter with an is-current scene draft created/updated within this window
# is considered actively being written and is never force-overwritten.
_FORCE_MATERIALIZE_ACTIVE_DRAFT_WINDOW_SECONDS = 300

_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")


def _count_sentences(text: Any) -> int:
    if not isinstance(text, str) or not text.strip():
        return 0
    return sum(1 for part in _SENTENCE_SPLIT_RE.split(text) if part.strip())


def _estimate_scene_obligation_points(scene: Any) -> int:
    """Coarse, deterministic count of narrative obligations packed into a scene.

    Counts only structural facts (sentence/entry counts), never vocabulary, so
    the estimate is genre-agnostic by construction.
    """
    purpose = getattr(scene, "purpose", None)
    story_text = purpose.get("story") if isinstance(purpose, dict) else purpose
    points = _count_sentences(story_text if isinstance(story_text, str) else None)
    exit_state = getattr(scene, "exit_state", None)
    if isinstance(exit_state, dict):
        points += len(exit_state)
    elif exit_state:
        points += 1
    points += len(getattr(scene, "key_dialogue_beats", None) or [])
    points += len(getattr(scene, "participants", None) or [])
    return points


def _apply_scene_capacity_normalization(
    batch: ChapterOutlineBatchInput,
    *,
    words_per_obligation: int = _SCENE_CAPACITY_WORDS_PER_OBLIGATION,
    overflow_ratio: float = _SCENE_CAPACITY_OVERFLOW_RATIO,
    chapter_word_cap: int = _SCENE_CAPACITY_CHAPTER_WORD_CAP,
) -> list[dict[str, Any]]:
    """R19: match scene information density against scene word targets.

    For every scene, estimate the word demand of its obligations. Scenes whose
    demand exceeds ``target_word_count * overflow_ratio`` get a non-blocking
    capacity warning and their target raised toward the estimate, bounded by
    the chapter target and the platform bandwidth (``chapter_word_cap``).
    Multiple overflowing scenes share the available headroom proportionally.
    Targets are never reduced. Returns the warning records.
    """
    warnings: list[dict[str, Any]] = []
    words_per_obligation = max(1, int(words_per_obligation))
    for chapter in batch.chapters:
        scenes = chapter.scenes
        if not scenes:
            continue
        estimates = [
            _estimate_scene_obligation_points(scene) * words_per_obligation
            for scene in scenes
        ]
        overflow_indexes = {
            idx
            for idx, scene in enumerate(scenes)
            if estimates[idx] > int(scene.target_word_count) * float(overflow_ratio)
        }
        if not overflow_indexes:
            continue
        chapter_target = int(chapter.target_word_count or 0)
        non_overflow_total = sum(
            int(scenes[idx].target_word_count)
            for idx in range(len(scenes))
            if idx not in overflow_indexes
        )
        overflow_desired_total = sum(
            max(int(scenes[idx].target_word_count), estimates[idx])
            for idx in overflow_indexes
        )
        total_desired = non_overflow_total + overflow_desired_total
        # The scene budget may grow toward the estimated demand, but never
        # beyond the platform bandwidth; an already-larger chapter target is
        # honored as-is (this pass never shrinks anything).
        allowed_total = max(chapter_target, min(int(chapter_word_cap), total_desired))
        budget = max(0, allowed_total - non_overflow_total)
        scale = 1.0
        if overflow_desired_total > budget:
            scale = budget / overflow_desired_total if overflow_desired_total else 0.0
        for idx in sorted(overflow_indexes):
            scene = scenes[idx]
            old_target = int(scene.target_word_count)
            desired = max(old_target, estimates[idx])
            adjusted = max(old_target, int(desired * scale))
            warnings.append(
                {
                    "chapter_number": chapter.chapter_number,
                    "scene_number": scene.scene_number,
                    "obligation_points": estimates[idx] // words_per_obligation,
                    "estimated_words": estimates[idx],
                    "target_word_count": old_target,
                    "adjusted_target_word_count": adjusted,
                }
            )
            if adjusted != old_target:
                scene.target_word_count = adjusted
        new_total = sum(int(scene.target_word_count) for scene in scenes)
        if new_total > chapter_target:
            chapter.target_word_count = min(
                new_total,
                max(int(chapter_word_cap), chapter_target),
            )
    return warnings


def _char_bigrams(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", text or "")
    return {compact[i : i + 2] for i in range(len(compact) - 1)}


def _flatten_metadata_field_text(value: Any) -> str:
    """Flatten a metadata field (str / list / dict trees) to comparison text.

    Dict keys are schema labels rather than story content, so only values are
    included.
    """
    parts: list[str] = []
    if isinstance(value, str):
        parts.append(value)
    elif isinstance(value, dict):
        parts.extend(
            _flatten_metadata_field_text(item) for item in value.values()
        )
    elif isinstance(value, (list, tuple)):
        parts.extend(_flatten_metadata_field_text(item) for item in value)
    elif value is not None:
        parts.append(str(value))
    return " ".join(part for part in parts if part)


def _chapter_narrative_text(chapter: Any) -> str:
    """Concatenate the narrative-layer text of a chapter outline."""
    parts: list[Any] = [
        getattr(chapter, "chapter_goal", None),
        getattr(chapter, "opening_situation", None),
        getattr(chapter, "main_conflict", None),
        getattr(chapter, "hook_description", None),
        getattr(chapter, "tail_hook", None),
        getattr(chapter, "opening_pressure", None),
        getattr(chapter, "required_payoff", None),
    ]
    for scene in getattr(chapter, "scenes", None) or []:
        parts.extend(
            (
                getattr(scene, "title", None),
                getattr(scene, "time_label", None),
                getattr(scene, "concrete_goal", None),
                getattr(scene, "protagonist_state", None),
            )
        )
        purpose = getattr(scene, "purpose", None)
        if isinstance(purpose, dict):
            parts.extend(value for value in purpose.values() if value)
        for state_field in ("entry_state", "exit_state"):
            state = getattr(scene, state_field, None)
            if isinstance(state, dict):
                parts.extend(value for value in state.values() if value)
        parts.extend(getattr(scene, "key_dialogue_beats", None) or [])
        parts.extend(getattr(scene, "information_introduced", None) or [])
        parts.extend(getattr(scene, "participants", None) or [])
    return " ".join(str(part) for part in parts if part)


def _apply_metadata_narrative_coherence(
    batch: ChapterOutlineBatchInput,
    *,
    min_overlap: float = _METADATA_COHERENCE_MIN_OVERLAP,
) -> tuple[list[dict[str, Any]], int]:
    """R21: detect deep-metadata residue disconnected from the narrative layer.

    Compares the character-2-gram set of each deep metadata field against the
    chapter's narrative text. A field whose overlap ratio falls below
    ``min_overlap`` is treated as suspected residue: a non-blocking warning is
    recorded and the field is cleared — feeding the writer nothing beats
    feeding it stale facts (same principle as the P0-2 enrichment pass).
    Returns ``(warnings, cleared_field_count)``.
    """
    warnings: list[dict[str, Any]] = []
    cleared_fields = 0
    for chapter in batch.chapters:
        narrative_grams = _char_bigrams(_chapter_narrative_text(chapter))
        if not narrative_grams:
            continue
        for field_name in _METADATA_COHERENCE_FIELDS:
            value = getattr(chapter, field_name, None)
            if not value:
                continue
            field_grams = _char_bigrams(_flatten_metadata_field_text(value))
            if not field_grams:
                continue
            overlap = len(field_grams & narrative_grams) / len(field_grams)
            if overlap >= float(min_overlap):
                continue
            warnings.append(
                {
                    "chapter_number": chapter.chapter_number,
                    "field": field_name,
                    "overlap_ratio": round(overlap, 4),
                    "item_count": len(value) if isinstance(value, (list, tuple)) else 1,
                    "action": "cleared",
                }
            )
            setattr(chapter, field_name, [])
            cleared_fields += 1
    return warnings, cleared_fields


def _is_recent_draft_timestamp(
    timestamp: datetime | None,
    *,
    window_seconds: int = _FORCE_MATERIALIZE_ACTIVE_DRAFT_WINDOW_SECONDS,
    now: datetime | None = None,
) -> bool:
    if timestamp is None:
        return False
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (reference - timestamp).total_seconds() <= float(window_seconds)


async def _chapter_has_recent_active_draft(
    session: AsyncSession,
    *,
    chapter_id: UUID,
    window_seconds: int = _FORCE_MATERIALIZE_ACTIVE_DRAFT_WINDOW_SECONDS,
    now: datetime | None = None,
) -> bool:
    """R14 guard: True when the chapter has a fresh is-current scene draft."""
    latest_created_at = await session.scalar(
        select(SceneDraftVersionModel.created_at)
        .join(SceneCardModel, SceneCardModel.id == SceneDraftVersionModel.scene_card_id)
        .where(
            SceneCardModel.chapter_id == chapter_id,
            SceneDraftVersionModel.is_current.is_(True),
        )
        .order_by(SceneDraftVersionModel.created_at.desc())
        .limit(1)
    )
    return _is_recent_draft_timestamp(
        latest_created_at,
        window_seconds=window_seconds,
        now=now,
    )


def _outline_fingerprint_scan_inputs(
    batch: ChapterOutlineBatchInput,
    existing_chapters: list[ChapterModel],
) -> tuple[list[ChapterOutlineInput], list[ChapterModel]]:
    """Return chapter outlines that should participate in a blocking re-plan scan.

    Progressive resume artifacts can contain already-written chapters plus the
    newly planned range. Existing non-mutable chapters should remain available
    for cross-chapter comparison, but they must not be compared against each
    other as if the current materialization run had generated them.
    """
    existing_by_number = {
        chapter.chapter_number: chapter
        for chapter in existing_chapters
    }
    scan_outlines: list[ChapterOutlineInput] = []
    scan_outline_numbers: set[int] = set()

    for outline in batch.chapters:
        existing = existing_by_number.get(outline.chapter_number)
        if existing is None or (
            (existing.status or "") in _MATERIALIZATION_MUTABLE_CHAPTER_STATUSES
        ):
            scan_outlines.append(outline)
            scan_outline_numbers.add(outline.chapter_number)

    scan_existing = [
        chapter
        for chapter in existing_chapters
        if chapter.chapter_number not in scan_outline_numbers
    ]
    return scan_outlines, scan_existing


def _outline_materialization_validation_batch(
    batch: ChapterOutlineBatchInput,
    existing_chapters: list[ChapterModel],
) -> tuple[ChapterOutlineBatchInput, int]:
    """Return the outline slice that this materialization run may still change."""
    validation_chapters, _ = _outline_fingerprint_scan_inputs(batch, existing_chapters)
    skipped_count = len(batch.chapters) - len(validation_chapters)
    if skipped_count <= 0:
        return batch, 0
    return (
        ChapterOutlineBatchInput(
            batch_name=batch.batch_name,
            chapters=validation_chapters,
        ),
        skipped_count,
    )


def _project_identity_manifest(project: ProjectModel) -> list[dict[str, Any]]:
    metadata = getattr(project, "metadata_json", None) or {}
    manifest = metadata.get("identity_manifest") if isinstance(metadata, dict) else None
    if not isinstance(manifest, list):
        return []
    return [item for item in manifest if isinstance(item, dict)]


def _identity_token(value: Any) -> str:
    if value is None:
        return ""
    return "".join(str(value).strip().lower().split())


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for item in value:
            if item is None:
                continue
            stripped = str(item).strip()
            if stripped:
                items.append(stripped)
        return items
    return []


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _non_empty_text(value: Any, default: str = "") -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _has_truthy_payload(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_truthy_payload(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_truthy_payload(item) for item in value)
    return True


def _materialization_role_quirk_min(role: str) -> int:
    role_lower = role.lower()
    if "protagonist" in role_lower:
        return 3
    if "antagonist" in role_lower:
        return 2
    return 0


def _materialization_character_basis(character: dict[str, Any], *, is_en: bool) -> str:
    for key in ("fear", "secret", "goal", "background", "flaw"):
        value = character.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:120]
    return "the central wound behind the story" if is_en else "故事核心伤口"


def _materialization_min_strings(
    current: Any,
    additions: list[str],
    minimum: int,
) -> list[str]:
    values = _string_list(current)
    for addition in additions:
        if len(values) >= minimum:
            break
        if addition and addition not in values:
            values.append(addition)
    return values


def _materialization_tag_memory(name: str, basis: str, *, is_en: bool) -> str:
    if is_en:
        return f"When {basis} comes up, {name} taps one knuckle twice before speaking."
    return f"一提到「{basis}」，{name}会先用指节轻敲两下再开口。"


def _materialization_independent_life(name: str, basis: str, *, is_en: bool) -> str:
    if is_en:
        return (
            f"Before the main plot, {name} still had a private obligation that "
            f"interrupts them whenever {basis} escalates."
        )
    return f"被卷入主线前，{name}还有一桩自己的日常牵挂；每当「{basis}」升级，这件事都会打断ta。"


def _materialization_quirks(
    character: dict[str, Any],
    *,
    required: int,
    is_en: bool,
) -> list[str]:
    name = _non_empty_text(character.get("name"), "the character" if is_en else "角色")
    basis = _materialization_character_basis(character, is_en=is_en)
    if is_en:
        candidates = [
            f"{name} checks exits before answering difficult questions.",
            f"{name} touches a worn personal object when {basis} is mentioned.",
            f"{name} repeats the last factual detail aloud before a risky choice.",
        ]
    else:
        candidates = [
            f"{name}进入陌生空间会先确认退路和窗位。",
            f"提到「{basis}」相关线索时，{name}会下意识停顿半拍。",
            f"{name}做危险决定前会把最后一个事实低声复述一遍。",
        ]
    return _materialization_min_strings(
        _as_mapping(character.get("ip_anchor")).get("quirks"),
        candidates,
        required,
    )


def _synthesize_materialization_character_bible_fields(
    character: dict[str, Any],
    *,
    is_en: bool,
) -> dict[str, Any]:
    repaired = copy.deepcopy(character)
    name = _non_empty_text(repaired.get("name"), "the character" if is_en else "角色")
    role = _non_empty_text(repaired.get("role"), "supporting")
    role_lower = role.lower()
    basis = _materialization_character_basis(repaired, is_en=is_en)
    anchor = copy.deepcopy(_as_mapping(repaired.get("ip_anchor")))

    if not _non_empty_text(anchor.get("tag_memory")):
        anchor["tag_memory"] = _materialization_tag_memory(name, basis, is_en=is_en)
    if (
        "protagonist" not in role_lower
        and "antagonist" not in role_lower
        and not _non_empty_text(anchor.get("independent_life"))
    ):
        anchor["independent_life"] = _materialization_independent_life(
            name,
            basis,
            is_en=is_en,
        )

    required_quirks = _materialization_role_quirk_min(role)
    if required_quirks:
        anchor["quirks"] = _materialization_quirks(
            repaired,
            required=required_quirks,
            is_en=is_en,
        )
        anchor["sensory_signatures"] = _materialization_min_strings(
            anchor.get("sensory_signatures"),
            (
                [f"a restrained pause before {name} speaks"]
                if is_en
                else [f"{name}开口前那一瞬克制的停顿"]
            ),
            1,
        )
        anchor["signature_objects"] = _materialization_min_strings(
            anchor.get("signature_objects"),
            (
                [f"{name}'s worn notebook"]
                if is_en
                else [f"{name}随身带着的旧册"]
            ),
            1,
        )
        if not _non_empty_text(anchor.get("core_wound")):
            anchor["core_wound"] = (
                f"{name} once trusted the wrong version of events around {basis}, "
                "and someone else paid the price."
                if is_en
                else f"{name}曾在「{basis}」上相信过错误叙事，结果让无法补偿的人替自己付出代价。"
            )
    repaired["ip_anchor"] = anchor

    if "protagonist" in role_lower or "antagonist" in role_lower:
        surface_fields = [
            key
            for key in ("background", "goal", "strength")
            if _non_empty_text(repaired.get(key))
        ]
        if len(surface_fields) < 2:
            if not _non_empty_text(repaired.get("background")):
                repaired["background"] = (
                    f"{name} is publicly shaped by the unresolved cost of {basis}."
                    if is_en
                    else f"{name}的外在身份一直被「{basis}」留下的代价塑形。"
                )
                surface_fields.append("background")
            if len(surface_fields) < 2 and not _non_empty_text(repaired.get("goal")):
                repaired["goal"] = (
                    f"Resolve {basis} before it destroys everyone tied to it."
                    if is_en
                    else f"在「{basis}」毁掉所有被牵连的人之前，把它彻底解决。"
                )
                surface_fields.append("goal")
            if len(surface_fields) < 2 and not _non_empty_text(repaired.get("strength")):
                repaired["strength"] = (
                    "Stays precise under pressure and turns contradictions into leverage."
                    if is_en
                    else "能在压力下保持精确，把细小矛盾变成反击支点。"
                )
        if not any(_non_empty_text(repaired.get(key)) for key in ("secret", "fear", "flaw")):
            repaired["secret" if "antagonist" in role_lower else "fear"] = (
                f"{name} knows their solution to {basis} repeats the original harm."
                if is_en and "antagonist" in role_lower
                else (
                    f"{name}害怕证明「{basis}」真相的同时，也暴露自己曾经判断失误。"
                    if "antagonist" not in role_lower
                    else f"{name}知道自己解决「{basis}」的方法正在重演最初的伤害。"
                )
            )

    if "protagonist" in role_lower:
        psych = copy.deepcopy(_as_mapping(repaired.get("psych_profile")))
        if not _has_truthy_payload(psych):
            psych = (
                {
                    "mbti": "INTJ",
                    "enneagram": "6w5",
                    "temperament": "guarded analytical",
                }
                if is_en
                else {
                    "mbti": "INTJ",
                    "enneagram": "6w5",
                    "temperament": "克制的分析型",
                }
            )
        repaired["psych_profile"] = psych

        history = copy.deepcopy(_as_mapping(repaired.get("life_history")))
        if not _has_truthy_payload(history):
            history = (
                {
                    "formative_events": [
                        {
                            "title": f"The cost of {basis}",
                            "summary": f"{name} learned that one wrong conclusion can ruin another life.",
                        }
                    ],
                    "defining_moments": ["Chose the harder truth over the safer official story."],
                }
                if is_en
                else {
                    "formative_events": [
                        {
                            "title": f"围绕「{basis}」付出的代价",
                            "summary": f"{name}第一次明白，错误判断会让别人替自己承受后果。",
                        }
                    ],
                    "defining_moments": ["选择更痛的真相，而不是更安全的官方说法。"],
                }
            )
        repaired["life_history"] = history

        family = copy.deepcopy(_as_mapping(repaired.get("family_imprint")))
        if not _has_truthy_payload(family):
            family = (
                {
                    "parenting_style": "love expressed through demands and silence",
                    "inherited_values": ["protect first, explain later"],
                }
                if is_en
                else {
                    "parenting_style": "以要求和沉默表达爱的家庭模式",
                    "inherited_values": ["先保护，再解释"],
                }
            )
        repaired["family_imprint"] = family

        beliefs = copy.deepcopy(_as_mapping(repaired.get("beliefs")))
        if not _has_truthy_payload(beliefs):
            beliefs = (
                {"philosophical_stance": "truth is a duty, not a comfort"}
                if is_en
                else {"philosophical_stance": "真相不是安慰，而是一种责任"}
            )
        repaired["beliefs"] = beliefs

    if "antagonist" in role_lower:
        charisma = copy.deepcopy(_as_mapping(repaired.get("villain_charisma")))
        if not _non_empty_text(charisma.get("noble_motivation")):
            charisma["noble_motivation"] = (
                f"{name} believes harsh control can prevent a larger collapse."
                if is_en
                else f"{name}相信残酷控制可以阻止更大范围的崩塌。"
            )
        if not _non_empty_text(charisma.get("pain_origin")):
            charisma["pain_origin"] = (
                f"A past failure around {basis} convinced {name} that mercy creates victims."
                if is_en
                else f"围绕「{basis}」的一次失败让{name}相信，仁慈只会制造更多未来受害者。"
            )
        if not _has_truthy_payload(charisma.get("redeeming_qualities")):
            charisma["redeeming_qualities"] = (
                ["keeps promises to dependents"]
                if is_en
                else ["会兑现对依附者的承诺"]
            )
        if not _non_empty_text(charisma.get("philosophical_appeal")):
            charisma["philosophical_appeal"] = (
                "Order can look merciful when everyone remembers chaos."
                if is_en
                else "当所有人都记得混乱的代价时，秩序看起来也会像一种仁慈。"
            )
        if not _has_truthy_payload(charisma.get("personal_code")):
            charisma["personal_code"] = (
                ["does not betray written bargains"]
                if is_en
                else ["不会背弃明文交易"]
            )
        if not _non_empty_text(charisma.get("tragic_irony")):
            charisma["tragic_irony"] = (
                f"To prevent another {basis}, {name} makes others repeat the same wound."
                if is_en
                else f"为了阻止「{basis}」重演，{name}反而让更多人承受同类伤口。"
            )
        if not _non_empty_text(charisma.get("protagonist_mirror")):
            charisma["protagonist_mirror"] = (
                f"Both {name} and the protagonist want to stop loss; they differ on sacrifice."
                if is_en
                else f"{name}和主角都想阻止失去，只是对谁可以被牺牲给出相反答案。"
            )
        repaired["villain_charisma"] = charisma

    return repaired


def _synthesize_materialization_cast_bible_fields(
    project: ProjectModel,
    cast_spec_content: dict[str, Any],
) -> dict[str, Any]:
    """Fill missing character personhood fields before the final L2 bible gate.

    Planner repair is the preferred path. This materialization fallback prevents
    already-approved CastSpec artifacts from failing only because legacy/minimal
    character rows omitted deterministic anchors such as tag_memory or quirks.
    """

    cast_spec = parse_cast_spec_input(cast_spec_content)
    normalized = cast_spec.model_dump(mode="json")
    repaired = copy.deepcopy(cast_spec_content)
    is_en = str(getattr(project, "language", "") or "").lower().startswith("en")

    if isinstance(normalized.get("protagonist"), dict):
        repaired["protagonist"] = _synthesize_materialization_character_bible_fields(
            normalized["protagonist"],
            is_en=is_en,
        )
    if isinstance(normalized.get("antagonist"), dict):
        repaired["antagonist"] = _synthesize_materialization_character_bible_fields(
            normalized["antagonist"],
            is_en=is_en,
        )
    repaired["supporting_cast"] = [
        _synthesize_materialization_character_bible_fields(character, is_en=is_en)
        for character in normalized.get("supporting_cast") or []
        if isinstance(character, dict)
    ]
    if "antagonist_forces" in normalized:
        repaired["antagonist_forces"] = normalized.get("antagonist_forces") or []
    if "conflict_map" in normalized:
        repaired["conflict_map"] = normalized.get("conflict_map") or []
    return repaired


def _identity_entry_tokens(entry: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    for value in [entry.get("name"), *_string_list(entry.get("aliases"))]:
        token = _identity_token(value)
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def _normalized_identity_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    name = str(entry.get("name") or "").strip()
    if not name:
        return None
    aliases = [
        alias
        for alias in _string_list(entry.get("aliases"))
        if _identity_token(alias) != _identity_token(name)
    ]
    return {
        "name": name,
        "role": str(entry.get("role") or "").strip(),
        "gender": str(entry.get("gender") or "").strip() or "unknown",
        "pronoun_set_zh": str(entry.get("pronoun_set_zh") or "").strip(),
        "pronoun_set_en": str(entry.get("pronoun_set_en") or "").strip(),
        "aliases": list(dict.fromkeys(aliases)),
    }


def _merge_identity_manifest_entries(
    *sources: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge cast-derived identities with persisted character-row identities."""

    merged: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}

    for source in sources:
        for raw_entry in source:
            if not isinstance(raw_entry, dict):
                continue
            entry = _normalized_identity_entry(raw_entry)
            if entry is None:
                continue
            tokens = _identity_entry_tokens(entry)
            existing = next((index[token] for token in tokens if token in index), None)
            if existing is None:
                merged.append(entry)
                for token in tokens:
                    index[token] = entry
                continue

            for key in ("role", "pronoun_set_zh", "pronoun_set_en"):
                if not existing.get(key) and entry.get(key):
                    existing[key] = entry[key]
            if (
                (not existing.get("gender") or existing.get("gender") == "unknown")
                and entry.get("gender")
                and entry.get("gender") != "unknown"
            ):
                existing["gender"] = entry["gender"]
            aliases = list(existing.get("aliases") or [])
            for alias in [entry.get("name"), *(entry.get("aliases") or [])]:
                alias_text = str(alias or "").strip()
                if (
                    alias_text
                    and _identity_token(alias_text) != _identity_token(existing.get("name"))
                    and alias_text not in aliases
                ):
                    aliases.append(alias_text)
            existing["aliases"] = aliases
            for token in _identity_entry_tokens(existing):
                index[token] = existing

    return merged


def _apply_identity_manifest_to_characters(
    characters: list[CharacterModel],
    manifest: list[dict[str, Any]],
) -> None:
    manifest_by_token: dict[str, dict[str, Any]] = {}
    for entry in manifest:
        for token in _identity_entry_tokens(entry):
            manifest_by_token[token] = entry

    for character in characters:
        entry = manifest_by_token.get(_identity_token(character.name))
        if entry is None:
            continue
        char_meta = dict(getattr(character, "metadata_json", None) or {})
        cast_entry = dict(char_meta.get("cast_entry") or {})
        cast_entry.update(
            {
                "gender": entry.get("gender") or "unknown",
                "pronoun_set_zh": entry.get("pronoun_set_zh") or "",
                "pronoun_set_en": entry.get("pronoun_set_en") or "",
                "aliases": entry.get("aliases") or [],
            }
        )
        char_meta.update(
            {
                "gender": cast_entry["gender"],
                "pronoun_set_zh": cast_entry["pronoun_set_zh"],
                "pronoun_set_en": cast_entry["pronoun_set_en"],
                "aliases": cast_entry["aliases"],
                "cast_entry": cast_entry,
            }
        )
        character.metadata_json = char_meta


def _has_unsupported_identity_default(cast_spec_content: dict[str, Any] | None) -> bool:
    """Detect identity repairs that invented a lock without reliable evidence."""

    if not isinstance(cast_spec_content, dict):
        return False

    def iter_character_dicts(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, dict):
            results: list[dict[str, Any]] = []
            if "name" in value:
                results.append(value)
            for item in value.values():
                results.extend(iter_character_dicts(item))
            return results
        if isinstance(value, list):
            results = []
            for item in value:
                results.extend(iter_character_dicts(item))
            return results
        return []

    for character in iter_character_dicts(cast_spec_content):
        metadata = character.get("metadata")
        if isinstance(metadata, dict) and metadata.get("identity_lock_repair") == "legacy_resume_default":
            return True
    return False


async def ensure_project_identity_manifest(
    session: AsyncSession,
    project: ProjectModel,
    *,
    project_slug: str,
) -> list[dict[str, Any]]:
    """Ensure a project has a locked identity manifest before writing resumes.

    Historical projects may have completed story-bible materialization before
    the identity contract existed. Resume paths must not treat that completed
    workflow as sufficient unless the project metadata now contains a locked
    manifest, or the latest CastSpec can pass the new identity contract.
    """

    existing_manifest = _project_identity_manifest(project)
    metadata = getattr(project, "metadata_json", None) or {}
    characters = list(
        await session.scalars(
            select(CharacterModel).where(CharacterModel.project_id == project.id)
        )
    )
    if (
        existing_manifest
        and isinstance(metadata, dict)
        and metadata.get("identity_manifest_status") == "locked"
    ):
        merged_manifest = _merge_identity_manifest_entries(
            existing_manifest,
            _identity_hints_from_characters(characters),
        )
        if merged_manifest != existing_manifest:
            project.metadata_json = {
                **metadata,
                "identity_manifest": merged_manifest,
                "identity_manifest_status": "locked",
            }
            _apply_identity_manifest_to_characters(characters, merged_manifest)
            await session.flush()
            return merged_manifest
        return existing_manifest

    artifact = await get_latest_planning_artifact(
        session,
        project_id=project.id,
        artifact_type=ArtifactType.CAST_SPEC,
    )
    if artifact is None:
        raise ValueError(
            f"Project '{project_slug}' is missing a locked identity manifest and has no CastSpec artifact."
        )

    identity_hints = [*existing_manifest, *_identity_hints_from_characters(characters)]
    artifact_content = artifact.content
    repaired_content, repair_count = repair_legacy_foundation_identity_locks(
        artifact_content,
        identity_hints=identity_hints,
    )
    if repair_count and repaired_content is not None:
        if _has_unsupported_identity_default(repaired_content):
            raise ValueError(
                "foundation_identity_contract: CastSpec is missing reliable identity locks; "
                "resume repair refused to invent gender/pronoun defaults."
            )
        artifact.content = repaired_content
        artifact.notes = _append_note(
            artifact.notes,
            f"legacy identity lock repair applied ({repair_count} field updates)",
        )
        artifact_content = repaired_content

    report = validate_foundation_identity_contract(artifact_content)
    report.raise_for_blocks(project_slug=project_slug, artifact="cast_spec")
    manifest = _merge_identity_manifest_entries(
        build_identity_manifest(artifact_content),
        _identity_hints_from_characters(characters),
    )
    if not manifest:
        raise ValueError(
            f"Project '{project_slug}' CastSpec produced an empty identity manifest."
        )

    project.metadata_json = {
        **(metadata if isinstance(metadata, dict) else {}),
        "identity_manifest": manifest,
        "identity_manifest_status": "locked",
    }

    _apply_identity_manifest_to_characters(characters, manifest)

    await session.flush()
    return manifest


def _append_note(existing: str | None, note: str) -> str:
    if not existing:
        return note
    if note in existing:
        return existing
    return f"{existing}\n{note}"


def _identity_hints_from_characters(
    characters: list[CharacterModel],
) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for character in characters:
        metadata = getattr(character, "metadata_json", None) or {}
        cast_entry = metadata.get("cast_entry") if isinstance(metadata, dict) else None
        if not isinstance(cast_entry, dict):
            cast_entry = {}
        aliases: list[str] = []
        for raw_aliases in (
            metadata.get("aliases") if isinstance(metadata, dict) else None,
            cast_entry.get("aliases"),
        ):
            aliases.extend(_string_list(raw_aliases))
        hint = {
            "name": character.name,
            "role": character.role,
            "gender": metadata.get("gender") or cast_entry.get("gender"),
            "pronoun_set_zh": metadata.get("pronoun_set_zh")
            or cast_entry.get("pronoun_set_zh"),
            "pronoun_set_en": metadata.get("pronoun_set_en")
            or cast_entry.get("pronoun_set_en"),
            "aliases": list(dict.fromkeys(aliases)),
        }
        if hint["gender"] or hint["pronoun_set_zh"] or hint["pronoun_set_en"]:
            hints.append(hint)
    return hints


def _repair_chapter_outline_contract_inputs(
    batch: ChapterOutlineBatchInput,
    *,
    identity_manifest: list[dict[str, Any]],
) -> int:
    """Backfill deterministic scene contract fields before outline validation."""

    protagonist_name = _outline_default_protagonist(identity_manifest)
    identity_index = _identity_index_from_manifest(identity_manifest)
    repaired = 0

    generic_contract_markers = (
        "推动剧情",
        "推动本章",
        "推进剧情",
        "推进",
        "承接上章",
        "承接上一章",
        "继续处理",
        "主线",
        "目标",
        "目标达成",
        "目的",
        "继续",
        "具体事件",
    )

    def _has_value(value: Any, *, generic: bool = True) -> bool:
        text = _text_value(value)
        if not text:
            return False
        if generic and any(token in text for token in generic_contract_markers):
            return False
        if len(text) < 6:
            return False
        return True

    def _clean_list(values: list[str]) -> list[str]:
        return [item for item in values if _has_value(item)]

    def _first_non_generic(*candidates: Any, fallback: str) -> str:
        for value in candidates:
            text = _text_value(value)
            if text and not any(token in text for token in generic_contract_markers) and len(text) >= 6:
                return text
        return fallback

    def _derive_chapter_hooks(chapter: ChapterOutlineInput, label: str) -> list[str]:
        hooks: list[str] = []
        source = _first_non_generic(
            chapter.hook_description,
            chapter.main_conflict,
            chapter.chapter_goal,
            chapter.title,
            fallback=f"{protagonist_name}在下一阶段需要立刻作出关键取舍。",
        )
        if chapter.scenes:
            story_purposes = [
                _first_non_generic(
                    getattr(scene.purpose, "get", lambda _k: None)("story") if isinstance(scene.purpose, dict) else None,
                    fallback="",
                )
                for scene in chapter.scenes
            ]
            scene_hook_parts = _clean_list(story_purposes)
            if scene_hook_parts:
                hooks.append(f"{label}中最先要落地的问题：{scene_hook_parts[0]}")
        hooks.append(
            f"{source}{'，' if source and not source.endswith('。') else ''}若未及时收束，"
            f"将直接影响下一步{label}选择。"
        )
        return _clean_list(list(dict.fromkeys(hooks)))

    def _derive_chapter_methodology_contract(
        chapter: ChapterOutlineInput,
    ) -> tuple[dict[str, Any], int]:
        raw_contract = dict(chapter.methodology_contract or {})
        existing_contract = normalize_chapter_overlay(raw_contract)
        contract = {**existing_contract, **raw_contract}
        repairs = 0

        protagonist = protagonist_name
        scenes = chapter.scenes or []
        first_scene = scenes[0] if scenes else None
        first_participants = first_scene.participants if first_scene is not None else []
        opponent = ""
        for participant in first_participants:
            if _text_value(participant) != protagonist_name:
                opponent = _text_value(participant)
                break
        if not opponent and scenes:
            opponent = "对手"
        base_conflict = _first_non_generic(
            chapter.main_conflict,
            chapter.chapter_goal,
            chapter.hook_description,
            chapter.title,
            fallback=f"{chapter.title}的核心冲突推进。",
        )
        hook_summary = _first_non_generic(
            chapter.hook_description,
            chapter.opening_situation,
            chapter.main_conflict,
            chapter.chapter_goal,
            chapter.title,
            fallback=f"{chapter.title}中的关键推进。",
        )
        hook_pieces = _derive_chapter_hooks(chapter, "本章")
        if not _has_value(contract.get("conflict_stakes")):
            contract["conflict_stakes"] = (
                f"若{protagonist}不能及时处理「{base_conflict}」，"
                f"{opponent or '对手'}会立即触发更高代价并放大压力。"
            )
            repairs += 1

        conflict_buffs = list(contract.get("conflict_buffs") or [])
        if len([buff for buff in conflict_buffs if _has_value(buff)]) < 2:
            conflict_buffs = [
                f"{protagonist}面对{_text_value(opponent) or '外部势力'}的持续压迫，必须在有限时窗内完成选择。",
                "每一次试探都逼近时间边界，信息延误将导致代价从局部变成整体。",
            ]
            contract["conflict_buffs"] = conflict_buffs
            repairs += 1

        if not _has_value(contract.get("pacing_mode")):
            contract["pacing_mode"] = "推进到关键动作与失误代价之间的高压节奏"
            repairs += 1

        if not _has_value(contract.get("emotion_phase")):
            contract["emotion_phase"] = "从压迫到决断的情绪切换"
            repairs += 1

        if not contract.get("is_climax"):
            contract["is_climax"] = "climax" in (
                _text_value(chapter.title) + _text_value(chapter.hook_description)
            ).lower()

        if not _has_value(contract.get("loop_position"), generic=False):
            if chapter.chapter_number <= 3:
                contract["loop_position"] = "开局压迫"
            elif chapter.chapter_number % 10 == 0:
                contract["loop_position"] = "轮回收束"
            else:
                contract["loop_position"] = "推进"
            repairs += 1

        if not _clean_list(contract.get("hooks_to_resolve") or []):
            contract["hooks_to_resolve"] = hook_pieces[:1]
            repairs += 1

        if not _clean_list(contract.get("hooks_to_plant") or []):
            contract["hooks_to_plant"] = [
                f"{hook_summary}引出下一步可被读者立即追问的延迟后果。"
            ]
            repairs += 1

        if scenes:
            if not _clean_list(contract.get("relationship_debts") or []):
                if opponent:
                    contract["relationship_debts"] = [
                        f"{protagonist}与{opponent}因{base_conflict}形成新的可见债务，必须交换信息或承担后果。"
                    ]
                elif len(first_participants) >= 2:
                    contract["relationship_debts"] = [
                        "本章多人互动会形成可见信息换位，改变下一步信任与行动边界。"
                    ]
                repairs += 1
            if _clean_list(contract.get("relationship_debts") or []):
                if _clean_list(contract.get("relationship_debts")) != _clean_list(
                    contract.get("relationship_debts") or []
                ):
                    contract["relationship_debts"] = _clean_list(contract["relationship_debts"])
                    repairs += 1

        if not _has_value(contract.get("chapter_function"), generic=False):
            contract["chapter_function"] = _first_non_generic(
                chapter.chapter_event_role,
                chapter.hook_type,
                chapter.opening_pressure,
                chapter.main_conflict,
                chapter.chapter_goal,
                fallback=f"第{chapter.chapter_number}章通过「{base_conflict}」推进读者可见行动与转折。",
            )
            repairs += 1

        if not _has_value(contract.get("protagonist_choice"), generic=False):
            contract["protagonist_choice"] = (
                f"{protagonist}在本章必须选择「{hook_summary}」对应的处理路径。"
            )
            repairs += 1

        if not _has_value(contract.get("visible_action"), generic=False):
            contract["visible_action"] = (
                f"{protagonist}在场景里先做出「{base_conflict}」相关可见动作，"
                f"再把结果导向下一层压力。"
            )
            repairs += 1

        if not _has_value(contract.get("cost"), generic=False):
            contract["cost"] = (
                "失败代价是时间窗口、证据完整性或关键关系任一项的即时丢失，"
                "直接决定下一章是否能追上真相线。"
            )
            repairs += 1

        payoff_seed = _first_non_generic(
            chapter.required_payoff,
            chapter.title,
            chapter.chapter_goal,
            chapter.main_conflict,
            chapter.hook_description,
            fallback=f"{protagonist}在本章可兑现「{base_conflict}」相关关键信息。",
        )
        if not _has_value(contract.get("gain_reveal"), generic=False):
            contract["gain_reveal"] = payoff_seed
            repairs += 1
        if not _has_value(contract.get("required_payoff"), generic=False):
            contract["required_payoff"] = payoff_seed
            repairs += 1
        if not _has_value(contract.get("payoff"), generic=False):
            contract["payoff"] = payoff_seed
            repairs += 1

        if not _has_value(contract.get("state_change"), generic=False):
            contract["state_change"] = (
                f"{protagonist}从本章前置状态进入「{base_conflict}」后的新局面，"
                "并被迫承接下一步可见行动。"
            )
            repairs += 1

        if chapter.chapter_number <= 10:
            if not _has_value(contract.get("opening_pressure"), generic=False):
                contract["opening_pressure"] = _first_non_generic(
                    chapter.opening_pressure,
                    chapter.opening_situation,
                    chapter.main_conflict,
                    chapter.chapter_goal,
                    fallback=f"第{chapter.chapter_number}章开局压力：{base_conflict}",
                )
                repairs += 1
            if not _has_value(contract.get("protagonist_flaw"), generic=False):
                contract["protagonist_flaw"] = (
                    f"{protagonist}先把{opponent or '局势中的不确定'}当作可控变量，"
                    "忽略了后续代价会放大到关系与证据位移。"
                )
                repairs += 1
            if not _has_value(contract.get("payoff"), generic=False):
                contract["payoff"] = payoff_seed
                repairs += 1
            if not _has_value(contract.get("tail_hook"), generic=False):
                contract["tail_hook"] = (
                    f"第{chapter.chapter_number}章结尾留出「{chapter.title or chapter.chapter_goal}」的新钩子，"
                    "下一步将直接接上更高代价的压力。"
                )
                repairs += 1

        return contract, 1 if repairs else 0

    def _derive_scene_methodology_contract(
        chapter: ChapterOutlineInput,
        scene: Any,
        *,
        scene_index: int,
        is_final_scene: bool = True,
    ) -> tuple[dict[str, Any], int]:
        raw_contract = dict(scene.methodology_contract or {})
        existing_contract = normalize_scene_overlay(raw_contract)
        contract = {**existing_contract, **raw_contract}
        repairs = 0

        # R22: outline-provided prompt-critical fields must OVERRIDE stale card
        # metadata. The merge above keeps old values whenever the new outline
        # carries them on the scene object (not inside methodology_contract),
        # so a re-plotted outline could never refresh signature imagery —
        # writers consumed three-versions-old images (SIGNATURE_IMAGE_MISSING
        # recurrence, zhaoshen-hr-v3 ch9 evidence, 2026-06-12).
        for _key, _fresh in (
            ("signature_image", getattr(scene, "signature_image", None)),
            ("object_signal", getattr(scene, "object_signal", None)),
            ("cut_point", getattr(scene, "cut_point", None)),
        ):
            if _has_value(_fresh, generic=False):
                contract[_key] = _text_value(_fresh)

        participants = [_text_value(item) for item in scene.participants if _text_value(item)]
        spotlight = participants[0] if participants else protagonist_name
        opponent = participants[1] if len(participants) > 1 else "对手"
        purpose = dict(scene.purpose or {})
        story_purpose = _text_value(purpose.get("story"))
        emotion_purpose = _text_value(purpose.get("emotion"))
        hook_hint = _first_non_generic(
            scene.hook_requirement,
            purpose.get("story"),
            purpose.get("emotion"),
            chapter.hook_description,
            chapter.title,
            fallback="场景内有可见动作与信息代价。",
        )
        signature_hint = _first_non_generic(
            scene.signature_image,
            raw_contract.get("signature_image"),
            story_purpose,
            hook_hint,
            fallback=f"{spotlight}场景中的关键视觉信息。",
        )
        # Chapter-level cut_point fan-out fix (zhaoshen-hr-v3 ch1 incident,
        # 2026-06-12): chapter.hook_description / chapter.title describe the
        # CHAPTER-ENDING climax. Falling back to them for EVERY scene copied
        # the full finale script into each scene card, so s01 pre-enacted the
        # chapter climax and s02 re-staged the exact same beats. Only the
        # FINAL scene of a chapter may inherit chapter-level cut material;
        # non-final scenes fall back to their own exit_state /
        # hook_requirement, or stay EMPTY — an empty cut_point is safer than
        # a wrong one (宽于错).
        scene_level_cut_point = _first_non_generic(
            scene.cut_point,
            raw_contract.get("cut_point"),
            fallback="",
        )
        if scene_level_cut_point:
            cut_point_hint = scene_level_cut_point
        elif is_final_scene:
            cut_point_hint = _first_non_generic(
                chapter.hook_description,
                chapter.title,
                fallback=f"{story_purpose}留下更高一步压力。",
            )
        else:
            exit_state_text = ""
            for _state_value in (scene.exit_state or {}).values():
                _state_text = _text_value(_state_value)
                if _state_text:
                    exit_state_text = _state_text
                    break
            cut_point_hint = _first_non_generic(
                exit_state_text,
                scene.hook_requirement,
                fallback="",
            )

        if not _has_value(contract.get("conflict_stakes"), generic=False):
            contract["conflict_stakes"] = (
                f"若{spotlight}在场景{scene_index}里没有处理好「{story_purpose or hook_hint}」，"
                f"{opponent}将快速把线索拉入不利方向。"
            )
            repairs += 1

        existing_buffs = _clean_list(contract.get("conflict_buffs") or [])
        if len(existing_buffs) < 2:
            contract["conflict_buffs"] = [
                f"{spotlight}被{opponent}的逼近时机持续施压，需尽快给出行动。",
                "停顿会把局面推向更高代价和更窄的选择。",
            ]
            repairs += 1

        if not _has_value(contract.get("hook_type"), generic=False):
            contract["hook_type"] = "行动转折"
            repairs += 1

        if not _has_value(contract.get("spotlight_character"), generic=False):
            contract["spotlight_character"] = spotlight
            repairs += 1

        if not _has_value(contract.get("information_control_mode"), generic=False):
            contract["information_control_mode"] = (
                f"先让读者看到{spotlight}与{opponent}的可见动作，再延后解释部分关联背景。"
            )
            repairs += 1

        if not _has_value(contract.get("camera_distance"), generic=False):
            contract["camera_distance"] = (
                f"镜头保持中近景，贴近{spotlight}视线与手部动作，捕捉{signature_hint}触发点。"
            )
            repairs += 1

        if not _has_value(contract.get("reveal_mode"), generic=False):
            contract["reveal_mode"] = (
                f"通过{signature_hint}的细节变化，在场景末端揭示下一步方向。"
            )
            repairs += 1

        if not _has_value(contract.get("signature_image"), generic=False):
            contract["signature_image"] = signature_hint
            repairs += 1

        if cut_point_hint and not _has_value(contract.get("cut_point"), generic=False):
            contract["cut_point"] = cut_point_hint
            repairs += 1

        if len(participants) >= 2 and not _clean_list(contract.get("relationship_debts") or []):
            contract["relationship_debts"] = [
                f"{spotlight}与{opponent}在场景中被迫建立新借贷/隐瞒协议，关系线继续加压。"
            ]
            repairs += 1

        scene_type = _text_value(scene.scene_type).lower()
        action_like = scene_type in {
            "action",
            "battle",
            "chase",
            "climax",
            "combat",
            "confrontation",
            "fight",
            "reveal",
        }
        if action_like or any(
            contract.get(key)
            for key in ("action_sequence", "fight_objective", "opponent_advantage")
        ):
            if not contract.get("action_sequence"):
                contract["action_sequence"] = [
                    f"{spotlight}先确认{signature_hint}并识别{opponent}的真实意图。",
                    f"{opponent}的下一步迫使{spotlight}改变节奏。",
                    f"{spotlight}做出可见行动，推动局势进入{cut_point_hint or hook_hint}。",
                ]
                repairs += 1
            if not _has_value(contract.get("fight_objective"), generic=False):
                contract["fight_objective"] = (
                    f"{spotlight}要通过场景行动拿到可持续推进下一步的关键证据。"
                )
                repairs += 1
            if not _has_value(contract.get("failure_cost"), generic=False):
                contract["failure_cost"] = (
                    f"处理不当会让{signature_hint}失真，导致下一步误读风险上升。"
                )
                repairs += 1
            if not _has_value(contract.get("opponent_advantage"), generic=False):
                contract["opponent_advantage"] = (
                    f"{opponent}先握有更多线索来源，可先发制人压缩{spotlight}行动窗口。"
                )
                repairs += 1
            if not _has_value(contract.get("tactic_shift"), generic=False):
                contract["tactic_shift"] = (
                    "从观察转为验证，再到主动逼问并调整动作路线。"
                )
                repairs += 1
            if not _has_value(contract.get("emotion_driver"), generic=False):
                contract["emotion_driver"] = (
                    f"{spotlight}因{emotion_purpose or '压力上升'}而保持决断，不能回避。"
                )
                repairs += 1
            if not _has_value(contract.get("turning_point"), generic=False):
                contract["turning_point"] = (
                    f"{opponent}暴露一个关键动作后，{spotlight}不得不在现场重选路径。"
                )
                repairs += 1
            if not _has_value(contract.get("exit_state_delta"), generic=False):
                contract["exit_state_delta"] = (
                    f"场景结束时{spotlight}把{cut_point_hint or hook_hint}推进到下一拍。"
                )
                repairs += 1

        return contract, 1 if repairs else 0

    for chapter in batch.chapters:
        # Story-semantic fields are not repaired here. If chapter goals,
        # openings, or hooks are missing/generic, the plan contract must fail
        # closed so the planner regenerates concrete events instead of
        # materializing synthetic story structure.
        # Do not synthesize missing/generic hooks. A generic hook is a broken
        # story promise, not a missing default; the plan contract must fail
        # closed so the planner regenerates a reader-visible next event.
        chapter_label = (
            chapter.title
            or chapter.chapter_goal
            or chapter.main_conflict
            or f"Chapter {chapter.chapter_number}"
        )
        contract, method_repair_count = _derive_chapter_methodology_contract(chapter)
        if method_repair_count:
            chapter.methodology_contract = contract
            repaired += method_repair_count
        # The chapter-level cut_point/hook may only flow into the FINAL scene
        # of the chapter (see _derive_scene_methodology_contract). Iterate in
        # scene-number order so entry_state can chain off the previous
        # scene's (possibly just-repaired) exit_state deterministically.
        final_scene_number = max(
            (s.scene_number for s in chapter.scenes),
            default=None,
        )
        previous_exit_state: dict[str, Any] | None = None
        for scene in sorted(chapter.scenes, key=lambda s: s.scene_number):
            if not _text_value(scene.time_label) or _is_generic_time_label(scene.time_label):
                scene.time_label = _outline_scene_time_repair(
                    chapter,
                    scene_number=scene.scene_number,
                    chapter_label=chapter_label,
                )
                repaired += 1
            if not scene.participants:
                scene.participants = [protagonist_name]
                repaired += 1
            purpose = dict(scene.purpose or {})
            if not _text_value(purpose.get("emotion")):
                _story_hint = (_text_value(purpose.get("story")) or "")[:40]
                purpose["emotion"] = (
                    f"围绕「{_story_hint}」写出当事人一个具体的怕、要或误判，"
                    "让读者看见情绪来源，并把选择或代价推到下一拍。"
                    if _story_hint
                    else "写出当事人此刻一个具体的怕、要或误判，让读者看见情绪来源。"
                )
                repaired += 1
            if purpose != scene.purpose:
                scene.purpose = purpose
            story_purpose = _text_value(purpose.get("story"))
            if story_purpose and identity_index:
                participant_tokens = {
                    _normalize_identity_token(participant)
                    for participant in scene.participants
                    if _text_value(participant)
                }
                for referenced_name in _extract_purpose_character_names(
                    story_purpose,
                    identity_index,
                ):
                    token = _normalize_identity_token(referenced_name)
                    if token and token not in participant_tokens:
                        scene.participants.append(referenced_name)
                        participant_tokens.add(token)
                        repaired += 1

            if not scene.entry_state:
                # Scene N (N≥2) must enter from where scene N-1 actually
                # ended — a purpose-summary placeholder leaves the writer
                # blind to what the previous scene already resolved
                # (zhaoshen-hr-v3 ch1 s02 re-staged s01's climax because its
                # entry_state was its own purpose recap). Deterministic copy,
                # no LLM call.
                if previous_exit_state:
                    scene.entry_state = dict(previous_exit_state)
                else:
                    scene.entry_state = {
                        "reader": (
                            f"{chapter.title or f'第{chapter.chapter_number}章'}场景{scene.scene_number}起始，"
                            f"核心任务仍是推进「{story_purpose or chapter.chapter_goal or chapter.title}」的可见结果。"
                        )
                    }
                repaired += 1
            if not scene.exit_state:
                scene.exit_state = {
                    "reader": (
                        f"{chapter.title or f'第{chapter.chapter_number}章'}场景{scene.scene_number}结束，"
                        f"读者已看到「{story_purpose or chapter.chapter_goal or chapter.title}」的关键变化。"
                    )
                }
                repaired += 1

            scene_method_contract, scene_method_repair_count = (
                _derive_scene_methodology_contract(
                    chapter,
                    scene,
                    scene_index=scene.scene_number,
                    is_final_scene=scene.scene_number == final_scene_number,
                )
            )
            if scene_method_repair_count:
                scene.methodology_contract = scene_method_contract
                repaired += scene_method_repair_count
                if not _text_value(scene.signature_image):
                    scene.signature_image = scene_method_contract.get("signature_image")
                if not _text_value(scene.cut_point):
                    scene.cut_point = scene_method_contract.get("cut_point")
                if not _text_value(scene.information_control_mode):
                    scene.information_control_mode = scene_method_contract.get(
                        "information_control_mode"
                    )
            previous_exit_state = dict(scene.exit_state) if scene.exit_state else None
    return repaired


def _normalize_outline_chapter_numbers(
    batch: ChapterOutlineBatchInput,
) -> dict[str, Any] | None:
    """Force a materialization batch onto a contiguous chapter-number range."""

    if not batch.chapters:
        return None
    numbers = [chapter.chapter_number for chapter in batch.chapters]
    if len(numbers) != len(set(numbers)):
        return None
    start = min(numbers)
    expected = list(range(start, start + len(numbers)))
    ordered_chapters = sorted(
        enumerate(batch.chapters),
        key=lambda item: (item[1].chapter_number, item[0]),
    )
    current_sorted = [chapter.chapter_number for _, chapter in ordered_chapters]
    if current_sorted == expected:
        return None

    renumbered: list[dict[str, int]] = []
    for new_number, (_, chapter) in zip(expected, ordered_chapters, strict=True):
        old_number = chapter.chapter_number
        if old_number == new_number:
            continue
        chapter.chapter_number = new_number
        renumbered.append({"from": old_number, "to": new_number})

    if not renumbered:
        return None
    return {
        "start": start,
        "end": expected[-1],
        "renumbered": renumbered,
    }


def _outline_default_protagonist(identity_manifest: list[dict[str, Any]]) -> str:
    for identity in identity_manifest:
        role = str(identity.get("role") or "").lower()
        name = _text_value(identity.get("name"))
        if name and "protagonist" in role:
            return name
    for identity in identity_manifest:
        name = _text_value(identity.get("name"))
        if name:
            return name
    return "主角"


def _outline_chapter_goal_repair(
    chapter: ChapterOutlineInput,
    *,
    protagonist_name: str,
) -> str:
    base = (
        chapter.main_conflict
        or chapter.hook_description
        or chapter.title
        or f"第{chapter.chapter_number}章核心冲突"
    )
    return (
        f"第{chapter.chapter_number}章围绕「{base}」，迫使{protagonist_name}"
        "完成一次具体选择、付出可见代价，并把压力转入下一章。"
    )


def _outline_opening_situation_repair(
    chapter: ChapterOutlineInput,
    *,
    protagonist_name: str,
) -> str:
    pressure = (
        chapter.main_conflict
        or chapter.hook_description
        or chapter.chapter_goal
        or f"第{chapter.chapter_number}章的新压力"
    )
    location = chapter.title or f"第{chapter.chapter_number}章开场"
    return (
        f"第{chapter.chapter_number}章开场落在「{location}」之后，"
        f"{protagonist_name}必须立刻处理「{pressure}」。"
    )


def _outline_hook_description_repair(
    chapter: ChapterOutlineInput,
    *,
    protagonist_name: str,
) -> str:
    pressure = (
        chapter.main_conflict
        or chapter.chapter_goal
        or chapter.title
        or f"第{chapter.chapter_number}章核心压力"
    )
    return (
        f"第{chapter.chapter_number}章尾钩：围绕「{pressure}」出现新的证据、"
        f"时限或代价，迫使{protagonist_name}下一章立刻行动。"
    )


def _outline_scene_time_repair(
    chapter: ChapterOutlineInput,
    *,
    scene_number: int | float,
    chapter_label: str,
) -> str:
    anchor = chapter.title or chapter.main_conflict or chapter_label
    return f"第{chapter.chapter_number}章「{anchor}」场景{scene_number}"


def _outline_scene_story_repair(
    chapter: ChapterOutlineInput,
    *,
    scene_number: int | float,
    participants: list[str],
) -> str:
    actors = "、".join(participants[:3]) if participants else "主角"
    base = (
        chapter.main_conflict
        or chapter.hook_description
        or chapter.chapter_goal
        or chapter.title
        or f"第{chapter.chapter_number}章核心目标"
    )
    return (
        f"第{chapter.chapter_number}章场景{scene_number}让{actors}"
        f"围绕「{base}」完成一次可见行动、信息交换或代价承担。"
    )


def _outline_scene_story_default(
    chapter: ChapterOutlineInput,
    *,
    scene_number: int,
) -> str:
    base = (
        chapter.chapter_goal
        or chapter.main_conflict
        or chapter.hook_description
        or chapter.title
        or f"第{chapter.chapter_number}章推进"
    )
    if scene_number == 1:
        return f"承接开场局势，围绕「{base}」建立行动目标和即时压力。"
    if scene_number == len(chapter.scenes):
        return f"围绕「{base}」交付本章变化，并留下推动下一章的尾钩。"
    return f"围绕「{base}」推进冲突升级，交付新的线索、代价或关系位移。"


def _text_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


async def _sync_existing_chapter_from_outline(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter: ChapterModel,
    chapter_outline: Any,
    force: bool = False,
) -> bool:
    """Update an existing planned/outlining chapter from the latest outline.

    ``force=True`` (R14) bypasses the mutable-status guard for chapters that
    the caller explicitly requested to re-materialize.
    """
    if not force and chapter.status not in _MATERIALIZATION_MUTABLE_CHAPTER_STATUSES:
        return False

    volume = await create_or_get_volume(
        session,
        project_id,
        VolumeCreate(
            volume_number=chapter_outline.volume_number,
            title=f"Volume {chapter_outline.volume_number}",
        ),
    )
    chapter.volume_id = volume.id
    if chapter_outline.title:
        chapter.title = chapter_outline.title
    chapter.chapter_goal = chapter_outline.chapter_goal
    chapter.opening_situation = chapter_outline.opening_situation
    chapter.main_conflict = chapter_outline.main_conflict
    chapter.hook_type = chapter_outline.hook_type
    chapter.hook_description = chapter_outline.hook_description
    chapter.target_word_count = chapter_outline.target_word_count
    return True


def _sync_chapter_causality_metadata(
    chapter: Any,
    chapter_outline: Any,
    causality_result: ChapterCausalityResult | None = None,
) -> None:
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    causal_contract = getattr(chapter_outline, "causal_contract", None)
    if isinstance(causal_contract, dict) and causal_contract:
        metadata["causal_contract"] = causal_contract
    else:
        metadata.pop("causal_contract", None)
    event_cycle_contract = getattr(chapter_outline, "event_cycle_contract", None)
    if isinstance(event_cycle_contract, dict) and event_cycle_contract:
        metadata["event_cycle_contract"] = event_cycle_contract
    else:
        metadata.pop("event_cycle_contract", None)
    chapter_event_role = str(getattr(chapter_outline, "chapter_event_role", "") or "").strip()
    if chapter_event_role:
        metadata["chapter_event_role"] = chapter_event_role
    else:
        metadata.pop("chapter_event_role", None)
    information_gap_mode = str(getattr(chapter_outline, "information_gap_mode", "") or "").strip()
    if information_gap_mode:
        metadata["information_gap_mode"] = information_gap_mode
    else:
        metadata.pop("information_gap_mode", None)
    if causality_result is not None:
        metadata["chapter_causality_axes"] = causality_result.to_dict()
    methodology_contract = normalize_chapter_overlay(
        getattr(chapter_outline, "methodology_contract", None)
    )
    if methodology_contract:
        metadata["methodology_contract"] = methodology_contract
    else:
        metadata.pop("methodology_contract", None)
    whole_chapter_logic_contract = getattr(
        chapter_outline,
        "whole_chapter_logic_contract",
        None,
    )
    if isinstance(whole_chapter_logic_contract, dict) and whole_chapter_logic_contract:
        metadata["whole_chapter_logic_contract"] = whole_chapter_logic_contract
    else:
        metadata.pop("whole_chapter_logic_contract", None)
    # Book-level story-enhancer cashing: the chapter LLM lands the selected
    # 脑洞/喜剧/爽点 effects into these structured fields. Persist them so the
    # prose writer (drafts.build_scene_draft_prompts → render_story_enhancer_
    # writer_block) can surface this chapter's planned beats — without this the
    # cashed content is dropped at persistence and never reaches the prose.
    brainhole_contract = getattr(chapter_outline, "brainhole_contract", None)
    if isinstance(brainhole_contract, dict) and brainhole_contract:
        metadata["brainhole_contract"] = brainhole_contract
    else:
        metadata.pop("brainhole_contract", None)
    selected_effect_skills = getattr(chapter_outline, "selected_effect_skills", None)
    if isinstance(selected_effect_skills, dict) and selected_effect_skills:
        metadata["selected_effect_skills"] = selected_effect_skills
    else:
        metadata.pop("selected_effect_skills", None)
    for field_name in (
        "world_rule_refs",
        "world_rule_landing",
        "world_state_deltas",
        "world_asset_refs",
        "authority_claim_refs",
        "world_scene_template_ref",
        "location_refs",
        "faction_refs",
        "key_reveals",
    ):
        value = getattr(chapter_outline, field_name, None)
        if value:
            metadata[field_name] = value
        else:
            metadata.pop(field_name, None)

    # ── Outline-v2 executable script fields ──
    for field_name in (
        "protagonist_inner_state",
        "chapter_concrete_actions",
        "chapter_object_uses",
        "chapter_information_introduced",
        "chapter_information_held_back",
    ):
        value = getattr(chapter_outline, field_name, None)
        if value:
            metadata[field_name] = value
        else:
            metadata.pop(field_name, None)

    setattr(chapter, "metadata_json", metadata)


def _sync_chapter_methodology_lineage(
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    chapter_outline: ChapterOutlineInput,
) -> bool:
    lineage = select_lineage_for_chapter_outline(
        project=project,
        chapter_outline=chapter_outline,
        weak_indicators=_project_methodology_weak_indicators(project),
    )
    if lineage is None:
        return False
    chapter.metadata_json = attach_methodology_lineage(
        getattr(chapter, "metadata_json", None),
        lineage,
    )
    return True


def _project_methodology_weak_indicators(project: ProjectModel) -> dict[str, float]:
    metadata = getattr(project, "metadata_json", None)
    if not isinstance(metadata, dict):
        return {}
    raw = metadata.get("methodology_weak_indicators") or metadata.get("weak_indicators")
    if not isinstance(raw, dict):
        return {}
    indicators: dict[str, float] = {}
    for key, value in raw.items():
        try:
            indicators[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return indicators


def _run_ensemble_arc_progress_gate(project: ProjectModel) -> EnsembleArcReport | None:
    if not is_methodology_v2_enabled():
        return None
    metadata = getattr(project, "metadata_json", None)
    if not isinstance(metadata, dict):
        return None
    raw_kernel = (
        metadata.get("ensemble_arc_kernel")
        or metadata.get("ensemble_arc")
        or metadata.get("ensemble_arcs")
    )
    if isinstance(raw_kernel, list):
        raw_kernel = {"arcs": raw_kernel}
    if not isinstance(raw_kernel, dict) or not raw_kernel:
        return None
    return scan_ensemble_arc_progress(
        raw_kernel,
        total_chapters=int(getattr(project, "target_chapters", 0) or 0),
        category=getattr(project, "sub_genre", None) or getattr(project, "genre", None),
    )


def _ensemble_arc_report_to_dict(report: EnsembleArcReport) -> dict[str, Any]:
    return {
        "findings": [
            {
                "code": finding.code,
                "severity": finding.severity,
                "message": finding.message,
                "payload": finding.payload,
            }
            for finding in report.findings
        ],
        "is_critical": report.is_critical,
    }


def _sync_existing_scene_from_outline(
    scene: SceneCardModel,
    scene_outline: Any,
    *,
    force: bool = False,
) -> bool:
    if not force and scene.status not in _MATERIALIZATION_MUTABLE_SCENE_STATUSES:
        return False
    scene.scene_type = scene_outline.scene_type
    scene.title = scene_outline.title
    scene.time_label = scene_outline.time_label
    scene.participants = scene_outline.participants
    scene.purpose = scene_outline.purpose
    scene.entry_state = scene_outline.entry_state
    scene.exit_state = scene_outline.exit_state
    scene.key_dialogue_beats = list(getattr(scene_outline, "key_dialogue_beats", None) or [])
    scene.sensory_anchors = dict(getattr(scene_outline, "sensory_anchors", None) or {})
    scene.forbidden_actions = list(getattr(scene_outline, "forbidden_actions", None) or [])
    scene.hook_requirement = (
        getattr(scene_outline, "hook_requirement", None)
        or scene.hook_requirement
    )
    scene.target_word_count = scene_outline.target_word_count
    _sync_scene_methodology_metadata(scene, scene_outline)
    return True


def _sync_scene_methodology_metadata(scene: SceneCardModel, scene_outline: Any) -> None:
    metadata = dict(getattr(scene, "metadata_json", None) or {})
    methodology_contract = normalize_scene_overlay(
        getattr(scene_outline, "methodology_contract", None)
    )
    if methodology_contract:
        metadata["methodology_contract"] = methodology_contract
    else:
        metadata.pop("methodology_contract", None)
    rich_fields = {
        "signature_image": getattr(scene_outline, "signature_image", None),
        "cut_point": getattr(scene_outline, "cut_point", None),
        "action_sequence": getattr(scene_outline, "action_sequence", None),
        "relationship_debts": getattr(scene_outline, "relationship_debts", None),
        "information_control_mode": getattr(scene_outline, "information_control_mode", None),
        # Outline-v2 executable script fields
        "concrete_goal": getattr(scene_outline, "concrete_goal", None),
        "protagonist_state": getattr(scene_outline, "protagonist_state", None),
        "information_introduced": getattr(scene_outline, "information_introduced", None) or None,
        "information_held_back": getattr(scene_outline, "information_held_back", None) or None,
        "object_signal": getattr(scene_outline, "object_signal", None),
    }
    for key, value in rich_fields.items():
        if value:
            metadata[key] = value
        else:
            metadata.pop(key, None)
    setattr(scene, "metadata_json", metadata)


def _normalize_outline_word_targets(
    batch: ChapterOutlineBatchInput,
    *,
    project: ProjectModel,
    settings: Any,
) -> int:
    """Normalize outline word targets before they enter persisted chapter rows."""

    repaired = 0
    for chapter in batch.chapters:
        normalized_chapter_target = normalize_chapter_word_target(
            chapter.target_word_count,
            project,
            settings,
        )
        if chapter.target_word_count != normalized_chapter_target:
            chapter.target_word_count = normalized_chapter_target
            repaired += 1
        if not chapter.scenes:
            continue
        scene_target = scene_word_target_for_chapter(
            chapter.target_word_count,
            len(chapter.scenes),
            settings,
        )
        for scene in chapter.scenes:
            if scene.target_word_count != scene_target:
                scene.target_word_count = scene_target
                repaired += 1
    return repaired


async def create_workflow_run(
    session: AsyncSession,
    *,
    project_id: UUID | None,
    workflow_type: str,
    status: WorkflowStatus,
    scope_type: str | None,
    scope_id: UUID | None,
    requested_by: str,
    current_step: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> WorkflowRunModel:
    if idempotency_key:
        existing = await session.scalar(
            select(WorkflowRunModel).where(
                WorkflowRunModel.workflow_type == workflow_type,
                WorkflowRunModel.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing
    workflow_run = WorkflowRunModel(
        project_id=project_id,
        workflow_type=workflow_type,
        status=status.value,
        scope_type=scope_type,
        scope_id=scope_id,
        requested_by=requested_by,
        current_step=current_step,
        metadata_json=metadata or {},
        idempotency_key=idempotency_key,
    )
    session.add(workflow_run)
    await session.flush()
    return workflow_run


async def create_workflow_step_run(
    session: AsyncSession,
    *,
    workflow_run_id: UUID,
    step_name: str,
    step_order: int,
    status: WorkflowStatus,
    input_ref: dict[str, Any] | None = None,
    output_ref: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> WorkflowStepRunModel:
    step_run = WorkflowStepRunModel(
        workflow_run_id=workflow_run_id,
        step_name=step_name,
        step_order=step_order,
        status=status.value,
        input_ref=input_ref or {},
        output_ref=output_ref or {},
        error_message=error_message,
    )
    session.add(step_run)
    await session.flush()
    return step_run


async def list_workflow_runs(
    session: AsyncSession,
    project_slug: str,
) -> list[WorkflowRunModel]:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    result = await session.scalars(
        select(WorkflowRunModel)
        .where(WorkflowRunModel.project_id == project.id)
        .order_by(WorkflowRunModel.created_at.desc())
    )
    return list(result)


async def get_workflow_run(
    session: AsyncSession,
    workflow_run_id: UUID,
) -> WorkflowRunModel | None:
    return await session.get(WorkflowRunModel, workflow_run_id)


async def get_latest_completed_workflow_run(
    session: AsyncSession,
    *,
    project_id: UUID,
    workflow_type: str,
) -> WorkflowRunModel | None:
    """Return the most recent completed workflow run of the given type, if any.

    Used by resume paths to detect that a one-shot materialization step has
    already finished successfully so it does not get re-run on every restart —
    re-running L2-gated materializers is non-idempotent (the gate may now
    reject content that was previously accepted) and stalls progress.
    """
    return await session.scalar(
        select(WorkflowRunModel)
        .where(
            WorkflowRunModel.project_id == project_id,
            WorkflowRunModel.workflow_type == workflow_type,
            WorkflowRunModel.status == WorkflowStatus.COMPLETED.value,
        )
        .order_by(WorkflowRunModel.created_at.desc())
        .limit(1)
    )


async def get_latest_planning_artifact(
    session: AsyncSession,
    *,
    project_id: UUID,
    artifact_type: ArtifactType,
) -> PlanningArtifactVersionModel | None:
    return await session.scalar(
        select(PlanningArtifactVersionModel)
        .where(
            PlanningArtifactVersionModel.project_id == project_id,
            PlanningArtifactVersionModel.artifact_type == artifact_type.value,
        )
        .order_by(
            PlanningArtifactVersionModel.version_no.desc(),
            PlanningArtifactVersionModel.created_at.desc(),
        )
        .limit(1)
    )


async def materialize_chapter_outline_batch(
    session: AsyncSession,
    project_slug: str,
    batch: ChapterOutlineBatchInput,
    *,
    requested_by: str = "system",
    source_artifact_id: UUID | None = None,
    prune_missing_planned: bool = False,
    force_chapter_numbers: list[int] | None = None,
) -> WorkflowMaterializationResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")
    settings = load_settings()

    workflow_run = await create_workflow_run(
        session,
        project_id=project.id,
        workflow_type=WORKFLOW_TYPE_MATERIALIZE_CHAPTER_OUTLINE,
        status=WorkflowStatus.RUNNING,
        scope_type="planning_artifact" if source_artifact_id is not None else "project",
        scope_id=source_artifact_id or project.id,
        requested_by=requested_by,
        current_step="validate_outline_batch",
        metadata={
            **truth_metadata_for_workflow(project),
            "batch_name": batch.batch_name,
            "chapter_count": len(batch.chapters),
            "source_artifact_id": str(source_artifact_id) if source_artifact_id else None,
        },
    )

    step_order = 1
    chapters_created = 0
    scenes_created = 0
    chapters_updated = 0
    scenes_updated = 0
    chapters_pruned = 0
    scenes_pruned = 0
    chapters_skipped_immutable = 0
    current_step_name = "validate_outline_batch"
    causality_results_by_chapter: dict[int, ChapterCausalityResult] = {}
    # R14: chapters the caller explicitly asked to re-materialize even though
    # they are no longer in a mutable (planned/outlining) status.
    _force_set: set[int] = {int(number) for number in (force_chapter_numbers or [])}
    _forced_chapters: list[int] = []
    _force_rejected_active_draft: list[int] = []

    # ── Deterministic field enrichment (P0-2) ──────────────────────────────
    # The batch planner systematically omits opening_situation, non-solo scene
    # participants and (sometimes) whole-batch causal contracts, which are hard
    # requirements of the gates below. Derive them from planner-provided
    # content before validating, so missing-field disease never hard-blocks a
    # book at gates two layers away from the producer.
    from bestseller.services.outline_field_enrichment import enrich_outline_batch_fields

    _identity_entries = _project_identity_manifest(project)
    _identity_names = [str(e.get("name") or "") for e in _identity_entries]
    _protagonist_name = next(
        (
            str(e.get("name") or "")
            for e in _identity_entries
            if str(e.get("role") or "").lower() == "protagonist"
        ),
        "",
    )
    _batch_content = batch.model_dump(mode="json")
    _batch_content, _enrich_stats = enrich_outline_batch_fields(
        _batch_content,
        _identity_names,
        protagonist=_protagonist_name,
    )
    if _enrich_stats.get("total"):
        batch = ChapterOutlineBatchInput.model_validate(_batch_content)
        workflow_run.metadata_json = {
            **(workflow_run.metadata_json or {}),
            "outline_field_enrichment": _enrich_stats,
        }

    # ── Metadata-narrative coherence check (R21) ───────────────────────────
    # Deep chapter metadata (key_reveals / world_state_deltas / location_refs)
    # can survive outline rewrites as stale residue the narrative layer no
    # longer supports — and would be fed verbatim into writer prompts. Detect
    # disconnected fields via char-2-gram overlap and clear them (non-blocking).
    _coherence_warnings, _coherence_cleared = _apply_metadata_narrative_coherence(batch)
    if _coherence_warnings:
        logger.warning(
            "Metadata-narrative coherence check flagged %d suspected residue "
            "field(s) in batch '%s' (cleared before materialization): %s",
            len(_coherence_warnings),
            batch.batch_name,
            _coherence_warnings[:10],
        )
        workflow_run.metadata_json = {
            **(workflow_run.metadata_json or {}),
            "metadata_coherence_warnings": _coherence_warnings,
            "metadata_coherence_cleared_fields": _coherence_cleared,
        }

    try:
        methodology_contract_mode = resolve_methodology_contract_mode(
            project,
            settings=settings,
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            input_ref={
                "batch_name": batch.batch_name,
                "chapter_count": len(batch.chapters),
            },
        )
        step_order += 1
        _chapter_number_normalization = _normalize_outline_chapter_numbers(batch)
        if _chapter_number_normalization:
            workflow_run.metadata_json = {
                **(workflow_run.metadata_json or {}),
                "chapter_number_normalization": _chapter_number_normalization,
            }
        outlined_chapter_numbers = {chapter.chapter_number for chapter in batch.chapters}
        _existing_project_chapters = list(
            await session.scalars(
                select(ChapterModel)
                .where(ChapterModel.project_id == project.id)
                .options(selectinload(ChapterModel.scenes))
            )
        )
        _validation_batch, _validation_skipped_count = (
            _outline_materialization_validation_batch(batch, _existing_project_chapters)
        )
        workflow_run.metadata_json = {
            **(workflow_run.metadata_json or {}),
            "methodology_contract_mode": methodology_contract_mode,
            "chapter_contract_validation_scope": {
                "batch_chapter_count": len(batch.chapters),
                "validated_chapter_count": len(_validation_batch.chapters),
                "skipped_existing_immutable_chapters": _validation_skipped_count,
            },
        }

        # ── Plan fingerprint gate: detect near-duplicate chapters before DB write ──
        # Compares each outline in the batch against the others AND against any
        # chapters already persisted for this project. Findings are logged and
        # attached to the workflow run's metadata so the planner can pick them
        # up on the next re-plan cycle.
        try:
            from bestseller.services.plan_fingerprint import scan_batch_for_duplicates

            _fp_batch_chapters, _existing_for_fp = _outline_fingerprint_scan_inputs(
                batch,
                _existing_project_chapters,
            )
            workflow_run.metadata_json = {
                **(workflow_run.metadata_json or {}),
                "plan_fingerprint_scan_scope": {
                    "batch_chapter_count": len(_fp_batch_chapters),
                    "existing_chapter_count": len(_existing_for_fp),
                    "skipped_existing_immutable_chapters": (
                        len(batch.chapters) - len(_fp_batch_chapters)
                    ),
                },
            }
            _fp_report = scan_batch_for_duplicates(
                _fp_batch_chapters,
                _existing_for_fp,
            )
            if _fp_report.findings:
                _project_metadata = getattr(project, "metadata_json", None) or {}
                _fingerprint_warn_only = (
                    _project_metadata.get("plan_fingerprint_gate_warn_only") is True
                )
                _fp_summary = [
                    {
                        "chapter_a": f.chapter_a,
                        "chapter_b": f.chapter_b,
                        "similarity": round(f.similarity, 3),
                        "severity": f.severity,
                        "reason": f.reason,
                    }
                    for f in _fp_report.findings[:20]
                ]
                logger.warning(
                    "Plan fingerprint scan flagged %d chapter pair(s) in batch '%s': %s",
                    len(_fp_report.findings),
                    batch.batch_name,
                    _fp_summary,
                )
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "plan_fingerprint_findings": _fp_summary,
                    "plan_fingerprint_has_critical": _fp_report.has_critical,
                    "plan_fingerprint_gate_warn_only": _fingerprint_warn_only,
                }
                if _fp_report.has_critical:
                    if _fingerprint_warn_only:
                        logger.warning(
                            "Plan fingerprint gate is warn-only for project '%s'; "
                            "continuing despite %d duplicate chapter pair(s).",
                            project_slug,
                            len(_fp_report.findings),
                        )
                    else:
                        raise ValueError(
                            "Chapter outline batch blocked by plan fingerprint gate: "
                            f"{len(_fp_report.findings)} duplicate chapter pair(s) found."
                        )
        except ValueError:
            raise
        except Exception:
            logger.debug(
                "Plan fingerprint scan failed for batch '%s' (non-fatal)",
                batch.batch_name,
                exc_info=True,
            )

        if (
            getattr(settings.pipeline, "require_chapter_plan_contract", True)
            and _validation_batch.chapters
        ):
            identity_manifest = await ensure_project_identity_manifest(
                session,
                project,
                project_slug=project_slug,
            )
            repair_count = _repair_chapter_outline_contract_inputs(
                _validation_batch,
                identity_manifest=identity_manifest,
            )
            if repair_count:
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "chapter_plan_contract_input_repair": {
                        "field_updates": repair_count,
                    },
                }
            _plan_contract = validate_chapter_plan_contract(
                _validation_batch,
                identity_manifest=identity_manifest,
                require_identity_registry=True,
            )
            if _plan_contract.violations or _plan_contract.warnings:
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "chapter_plan_contract": _plan_contract.to_dict(),
                }
            _plan_contract.raise_for_blocks(
                project_slug=project_slug,
                artifact="chapter_outline_batch",
            )

        if (
            getattr(settings.pipeline, "enable_chapter_causality_gate", True)
            and _validation_batch.chapters
        ):
            _causality_report = evaluate_chapter_causality_contract(
                _validation_batch,
                require_methodology_overlay=methodology_contract_requires_checks(
                    methodology_contract_mode
                ),
            )
            causality_results_by_chapter = {
                result.chapter_number: result
                for result in _causality_report.chapter_results
            }
            _causality_payload = chapter_causality_report_to_dict(_causality_report)
            if _causality_report.findings:
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "chapter_causality_contract": _causality_payload,
                }
            _blocking_findings = _causality_report.blocking_findings
            if not methodology_contract_blocks(methodology_contract_mode):
                _blocking_findings = tuple(
                    finding
                    for finding in _blocking_findings
                    if not is_methodology_causality_finding(finding)
                )
            if _blocking_findings and (
                methodology_contract_blocks(methodology_contract_mode)
                or getattr(settings.pipeline, "chapter_causality_gate_block_on_failure", True)
            ):
                raise ValueError(
                    "Chapter outline batch blocked by chapter_causality_contract: "
                    f"{len(_blocking_findings)} blocking finding(s)."
                )

        _ensemble_arc_report = _run_ensemble_arc_progress_gate(project)
        if _ensemble_arc_report is not None:
            _ensemble_payload = _ensemble_arc_report_to_dict(_ensemble_arc_report)
            workflow_run.metadata_json = {
                **(workflow_run.metadata_json or {}),
                "ensemble_arc_progress_gate": _ensemble_payload,
            }
            _metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
            if (
                _ensemble_arc_report.is_critical
                and _metadata.get("ensemble_arc_progress_gate_block_on_failure") is True
            ):
                raise ValueError(
                    "Chapter outline batch blocked by ensemble_arc_progress_gate: "
                    f"{len(_ensemble_arc_report.findings)} finding(s)."
                )

        if (
            getattr(settings.pipeline, "enable_methodology_planning_readiness_gate", True)
            and _validation_batch.chapters
        ):
            _planning_readiness_report = evaluate_chapter_outline_batch_planning_readiness(
                _validation_batch
            )
            workflow_run.metadata_json = {
                **(workflow_run.metadata_json or {}),
                "methodology_planning_readiness_gate": (
                    _planning_readiness_report.model_dump(mode="json")
                ),
            }
            if (
                not _planning_readiness_report.passed
                and strict_blocks(
                    project,
                    settings,
                    "methodology_planning_readiness_block_on_failure",
                )
            ):
                raise ValueError(
                    "Chapter outline batch blocked by methodology_planning_readiness_gate: "
                    f"{len(_planning_readiness_report.blocking_findings)} "
                    "blocking finding(s)."
                )

        from bestseller.services.prompt_packs import resolve_prompt_pack

        _project_metadata = (
            project.metadata_json if isinstance(project.metadata_json, dict) else {}
        )
        _project_brief = {
            "slug": project.slug,
            "title": project.title,
            "genre": project.genre,
            "sub_genre": project.sub_genre,
            "target_chapters": project.target_chapters,
            "reader_contract": project.reader_contract_json or {},
            "hype_scheme": project.hype_scheme_json or {},
            "metadata": _project_metadata,
        }
        _pack = resolve_prompt_pack(
            _project_metadata.get("prompt_pack_name")
            or _project_metadata.get("prompt_pack_key"),
            genre=str(getattr(project, "genre", "general-fiction") or "general-fiction"),
            sub_genre=getattr(project, "sub_genre", None),
        )

        if (
            getattr(settings.pipeline, "enable_outline_llm_commercial_judge", False)
            and _validation_batch.chapters
        ):
            current_step_name = "outline_llm_commercial_judge"
            workflow_run.current_step = current_step_name
            from bestseller.services.outline_llm_judge import (
                judge_outline_commercial_readiness,
            )

            _outline_llm_result = await judge_outline_commercial_readiness(
                session,
                settings,
                outline_payload=_validation_batch.model_dump(mode="json"),
                project_brief=_project_brief,
                threshold=float(
                    getattr(
                        settings.pipeline,
                        "outline_llm_commercial_judge_threshold",
                        0.82,
                    )
                    or 0.82
                ),
                workflow_run_id=workflow_run.id,
                pack=_pack,
            )
            _outline_llm_payload = _outline_llm_result.model_dump(
                mode="json",
                by_alias=True,
            )
            workflow_run.metadata_json = {
                **(workflow_run.metadata_json or {}),
                "outline_llm_commercial_judge": _outline_llm_payload,
            }
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=(
                    WorkflowStatus.MACHINE_BLOCKED
                    if not _outline_llm_result.passed
                    and strict_blocks(
                        project,
                        settings,
                        "outline_llm_commercial_judge_block_on_failure",
                    )
                    else WorkflowStatus.COMPLETED
                ),
                output_ref=_outline_llm_payload,
            )
            step_order += 1
            if _outline_llm_result.passed:
                # Clear any accumulated repair directives once the judge passes.
                _md = dict(project.metadata_json or {})
                if "outline_commercial_repair_directives" in _md or (
                    "outline_commercial_repair_round" in _md
                ):
                    _md.pop("outline_commercial_repair_directives", None)
                    _md.pop("outline_commercial_repair_round", None)
                    project.metadata_json = _md
            elif strict_blocks(
                project,
                settings,
                "outline_llm_commercial_judge_block_on_failure",
            ):
                # Feed the judge's concrete fixes back so the next outline
                # regeneration repairs exactly the flagged scenes (the changed
                # constraints alter the planner input-hash -> forced regen),
                # capped to avoid an unbounded repair/token loop.
                from bestseller.services.outline_llm_judge import (
                    build_outline_repair_directives,
                )

                _md = dict(project.metadata_json or {})
                _round = int(_md.get("outline_commercial_repair_round", 0) or 0)
                if _round < _MAX_OUTLINE_COMMERCIAL_REPAIR_ROUNDS:
                    _md["outline_commercial_repair_directives"] = (
                        build_outline_repair_directives(_outline_llm_result)
                    )
                    _md["outline_commercial_repair_round"] = _round + 1
                    project.metadata_json = _md
                    # Invalidate the stale outline so the retry regenerates it
                    # (otherwise autowrite reuses the existing artifact and just
                    # re-judges the same low-quality outline). With the artifact
                    # gone, the planner regenerates using the injected directives.
                    await session.execute(
                        _sa_delete(PlanningArtifactVersionModel).where(
                            PlanningArtifactVersionModel.project_id == project.id,
                            PlanningArtifactVersionModel.artifact_type.in_(
                                [
                                    ArtifactType.VOLUME_CHAPTER_OUTLINE.value,
                                    ArtifactType.CHAPTER_OUTLINE_BATCH.value,
                                ]
                            ),
                        )
                    )
                    # Persist the directives + invalidation before raising so the
                    # gate-driven regeneration retry can actually consume them.
                    await session.commit()
                issue_summary = "; ".join(
                    f"{issue.code}:{issue.evidence}"
                    for issue in _outline_llm_result.blocking_issues[:3]
                )
                raise ValueError(
                    "Chapter outline batch blocked by outline_llm_commercial_judge: "
                    f"score={_outline_llm_result.overall_score:.3f}, "
                    f"{len(_outline_llm_result.blocking_issues)} blocking issue(s). "
                    f"{issue_summary}"
                )

        if (
            getattr(settings.pipeline, "enable_outline_reader_experience_judge", True)
            and _validation_batch.chapters
        ):
            current_step_name = "outline_reader_experience_judge"
            workflow_run.current_step = current_step_name
            from bestseller.services.outline_reader_experience_judge import (
                judge_outline_reader_experience,
            )

            _reader_result = await judge_outline_reader_experience(
                session,
                settings,
                chapters_payload=[
                    ch.model_dump(mode="json") for ch in _validation_batch.chapters
                ],
                project_brief=_project_brief,
                threshold=float(
                    getattr(
                        settings.pipeline,
                        "outline_reader_experience_judge_threshold",
                        0.78,
                    )
                    or 0.78
                ),
                workflow_run_id=workflow_run.id,
                pack=_pack,
            )
            _reader_payload = _reader_result.model_dump(mode="json", by_alias=True)
            workflow_run.metadata_json = {
                **(workflow_run.metadata_json or {}),
                "outline_reader_experience_judge": _reader_payload,
            }
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=(
                    WorkflowStatus.MACHINE_BLOCKED
                    if not _reader_result.passed
                    and getattr(
                        settings.pipeline,
                        "outline_reader_experience_judge_block_on_failure",
                        True,
                    )
                    else WorkflowStatus.COMPLETED
                ),
                output_ref=_reader_payload,
            )
            step_order += 1
            if (
                not _reader_result.passed
                and getattr(
                    settings.pipeline,
                    "outline_reader_experience_judge_block_on_failure",
                    True,
                )
            ):
                issue_summary = "; ".join(
                    f"{issue.code}:{issue.evidence}"
                    for issue in _reader_result.blocking_issues[:3]
                )
                raise ValueError(
                    "Chapter outline batch blocked by outline_reader_experience_judge: "
                    f"score={_reader_result.overall_score:.3f}, "
                    f"{len(_reader_result.blocking_issues)} blocking issue(s). "
                    f"{issue_summary}"
                )

        _story_principle_cfg = get_quality_gates_config().story_principle
        if (
            getattr(settings.pipeline, "enable_story_principle_gate", True)
            and _story_principle_cfg.enabled
            and _validation_batch.chapters
        ):
            from bestseller.services.story_principle_gate import (
                evaluate_story_principle_contract,
                story_principle_report_to_dict,
            )

            _story_principle_report = evaluate_story_principle_contract(
                _validation_batch,
                min_roles_per_batch=_story_principle_cfg.min_event_cycle_roles_per_batch,
                max_same_role_streak=_story_principle_cfg.max_same_role_streak,
            )
            _story_principle_payload = story_principle_report_to_dict(
                _story_principle_report
            )
            workflow_run.metadata_json = {
                **(workflow_run.metadata_json or {}),
                "story_principle_gate_report": _story_principle_payload,
            }
            if _story_principle_cfg.block_on_failure and not _story_principle_report.passed:
                raise ValueError(
                    "Chapter outline batch blocked by story_principle_gate: "
                    f"{len(_story_principle_report.findings)} finding(s)."
                )

        _word_target_repairs = _normalize_outline_word_targets(
            batch,
            project=project,
            settings=settings,
        )
        if _word_target_repairs:
            workflow_run.metadata_json = {
                **(workflow_run.metadata_json or {}),
                "outline_word_target_normalization": {
                    "field_updates": _word_target_repairs,
                    "chapter_min": int(settings.generation.words_per_chapter.min),
                    "chapter_target": int(settings.generation.words_per_chapter.target),
                    "chapter_max": int(settings.generation.words_per_chapter.max),
                },
            }

        # ── Scene capacity matching (R19) ──────────────────────────────────
        # Estimate each scene's word demand from its obligation density and
        # raise mismatched scene targets (warning-only, never blocks) so dense
        # scenes stop oscillating between SCENE_COMPLETION_INCOMPLETE and
        # LENGTH_OVER at draft time.
        _capacity_warnings = _apply_scene_capacity_normalization(batch)
        _capacity_adjusted_chapters: set[int] = set()
        if _capacity_warnings:
            _capacity_adjusted_chapters = {
                int(warning["chapter_number"]) for warning in _capacity_warnings
            }
            logger.warning(
                "Scene capacity check flagged %d scene(s) in batch '%s' whose "
                "obligation density exceeds the word target: %s",
                len(_capacity_warnings),
                batch.batch_name,
                _capacity_warnings[:10],
            )
            workflow_run.metadata_json = {
                **(workflow_run.metadata_json or {}),
                "scene_capacity_warnings": _capacity_warnings,
            }

        for chapter_outline in batch.chapters:
            current_step_name = f"create_chapter_{chapter_outline.chapter_number}"
            workflow_run.current_step = current_step_name

            # Idempotency: if a chapter row with the same number already exists
            # (e.g. recovery shim or previous partial materialization), reuse it
            # instead of raising. This makes resume safe across re-runs.
            existing_chapter = await session.scalar(
                select(ChapterModel).where(
                    ChapterModel.project_id == project.id,
                    ChapterModel.chapter_number == chapter_outline.chapter_number,
                )
            )
            force_this_chapter = False
            if (
                existing_chapter is not None
                and existing_chapter.status not in _MATERIALIZATION_MUTABLE_CHAPTER_STATUSES
            ):
                if chapter_outline.chapter_number not in _force_set:
                    chapters_skipped_immutable += 1
                    continue
                # R14 guard: never force-overwrite a chapter that is actively
                # being written (an is-current scene draft updated within the
                # protection window).
                if await _chapter_has_recent_active_draft(
                    session,
                    chapter_id=existing_chapter.id,
                ):
                    logger.warning(
                        "Refusing to force-materialize chapter %d of '%s': an "
                        "is-current scene draft was updated within the last %d "
                        "seconds (chapter is actively being written).",
                        chapter_outline.chapter_number,
                        project_slug,
                        _FORCE_MATERIALIZE_ACTIVE_DRAFT_WINDOW_SECONDS,
                    )
                    _force_rejected_active_draft.append(chapter_outline.chapter_number)
                    chapters_skipped_immutable += 1
                    continue
                force_this_chapter = True
                _forced_chapters.append(chapter_outline.chapter_number)

            should_sync_causality_metadata = False
            if existing_chapter is not None:
                chapter = existing_chapter
                if await _sync_existing_chapter_from_outline(
                    session,
                    project_id=project.id,
                    chapter=chapter,
                    chapter_outline=chapter_outline,
                    force=force_this_chapter,
                ):
                    chapters_updated += 1
                    should_sync_causality_metadata = True
            else:
                chapter = await create_chapter(
                    session,
                    project_slug,
                    ChapterCreate(
                        chapter_number=chapter_outline.chapter_number,
                        title=chapter_outline.title,
                        chapter_goal=chapter_outline.chapter_goal,
                        opening_situation=chapter_outline.opening_situation,
                        main_conflict=chapter_outline.main_conflict,
                        hook_type=chapter_outline.hook_type,
                        hook_description=chapter_outline.hook_description,
                        volume_number=chapter_outline.volume_number,
                        target_word_count=chapter_outline.target_word_count,
                    ),
                )
                chapters_created += 1
                should_sync_causality_metadata = True
            if should_sync_causality_metadata:
                _sync_chapter_causality_metadata(
                    chapter,
                    chapter_outline,
                    causality_results_by_chapter.get(chapter_outline.chapter_number),
                )
                _sync_chapter_methodology_lineage(
                    project=project,
                    chapter=chapter,
                    chapter_outline=chapter_outline,
                )
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                input_ref={
                    "chapter_number": chapter_outline.chapter_number,
                    "scene_count": len(chapter_outline.scenes),
                },
                output_ref={
                    "chapter_id": str(chapter.id),
                    "chapter_number": chapter.chapter_number,
                },
            )
            step_order += 1

            existing_scenes = list(
                await session.scalars(
                    select(SceneCardModel)
                    .where(SceneCardModel.chapter_id == chapter.id)
                    .order_by(SceneCardModel.scene_number.asc())
                )
            )
            existing_scenes_by_number = {
                scene.scene_number: scene
                for scene in existing_scenes
            }
            outlined_scene_numbers = {
                scene_outline.scene_number
                for scene_outline in chapter_outline.scenes
            }
            materialized_scenes_for_chapter: list[Any] = []

            for scene_outline in chapter_outline.scenes:
                current_step_name = (
                    f"create_scene_{chapter_outline.chapter_number}_{scene_outline.scene_number}"
                )
                workflow_run.current_step = current_step_name

                existing_scene = existing_scenes_by_number.get(scene_outline.scene_number)
                if existing_scene is not None:
                    scene = existing_scene
                    if _sync_existing_scene_from_outline(
                        scene,
                        scene_outline,
                        force=force_this_chapter,
                    ):
                        scenes_updated += 1
                else:
                    scene = await create_scene_card(
                        session,
                        project_slug,
                        chapter_outline.chapter_number,
                        SceneCardCreate(
                            scene_number=scene_outline.scene_number,
                            scene_type=scene_outline.scene_type,
                            title=scene_outline.title,
                            time_label=scene_outline.time_label,
                            participants=scene_outline.participants,
                            purpose=scene_outline.purpose,
                            entry_state=scene_outline.entry_state,
                            exit_state=scene_outline.exit_state,
                            key_dialogue_beats=scene_outline.key_dialogue_beats,
                            sensory_anchors=scene_outline.sensory_anchors,
                            forbidden_actions=scene_outline.forbidden_actions,
                            # Bug fix: the create path dropped hook_requirement
                            # (the re-sync path at _sync_existing_scene_from_outline
                            # always set it). A new scene card silently lost its
                            # tail-hook contract — restore parity.
                            hook_requirement=scene_outline.hook_requirement,
                            metadata={
                                key: value
                                for key, value in {
                                    # Webnovel method cards: chapter-level
                                    # target emotion threads into every scene
                                    # card so downstream prompts can read it.
                                    "chapter_target_emotion": (
                                        chapter_outline.target_emotion
                                    ),
                                    "signature_image": scene_outline.signature_image,
                                    "cut_point": scene_outline.cut_point,
                                    "action_sequence": scene_outline.action_sequence,
                                    "relationship_debts": scene_outline.relationship_debts,
                                    "information_control_mode": (
                                        scene_outline.information_control_mode
                                    ),
                                    # Outline-v2 executable script fields
                                    "concrete_goal": scene_outline.concrete_goal,
                                    "protagonist_state": scene_outline.protagonist_state,
                                    "information_introduced": (
                                        scene_outline.information_introduced or None
                                    ),
                                    "information_held_back": (
                                        scene_outline.information_held_back or None
                                    ),
                                    "object_signal": scene_outline.object_signal,
                                }.items()
                                if value
                            },
                            target_word_count=scene_outline.target_word_count,
                        ),
                    )
                    _sync_scene_methodology_metadata(scene, scene_outline)
                    scenes_created += 1
                materialized_scenes_for_chapter.append(scene)
                await create_workflow_step_run(
                    session,
                    workflow_run_id=workflow_run.id,
                    step_name=current_step_name,
                    step_order=step_order,
                    status=WorkflowStatus.COMPLETED,
                    input_ref={
                        "chapter_number": chapter_outline.chapter_number,
                        "scene_number": scene_outline.scene_number,
                    },
                    output_ref={
                        "scene_id": str(scene.id),
                        "scene_number": scene.scene_number,
                    },
                )
                step_order += 1

            # ── Normalize chapter + scene target_word_count to the shared budget ──
            # Defensive pass for legacy call sites that may bypass the outline
            # normalization above. Chapters adjusted by the scene capacity pass
            # (R19) keep their differentiated per-scene targets — flattening
            # them back to a uniform value would undo the capacity fix.
            _num_scenes = len(chapter_outline.scenes)
            if (
                _num_scenes > 0
                and chapter_outline.chapter_number not in _capacity_adjusted_chapters
            ):
                chapter.target_word_count = normalize_chapter_word_target(
                    chapter.target_word_count,
                    project,
                    settings,
                )
                _per_scene = scene_word_target_for_chapter(
                    chapter.target_word_count,
                    _num_scenes,
                    settings,
                )

                for _sc in materialized_scenes_for_chapter:
                    _sc.target_word_count = _per_scene

            if prune_missing_planned:
                for existing_scene in existing_scenes:
                    if existing_scene.scene_number in outlined_scene_numbers:
                        continue
                    if existing_scene.status not in _MATERIALIZATION_MUTABLE_SCENE_STATUSES:
                        continue
                    await session.delete(existing_scene)
                    scenes_pruned += 1

        if prune_missing_planned and outlined_chapter_numbers:
            stale_chapters = list(
                await session.scalars(
                    select(ChapterModel).where(
                        ChapterModel.project_id == project.id,
                        ChapterModel.status.in_(tuple(_MATERIALIZATION_MUTABLE_CHAPTER_STATUSES)),
                    )
                )
            )
            for stale_chapter in stale_chapters:
                if stale_chapter.chapter_number in outlined_chapter_numbers:
                    continue
                await session.delete(stale_chapter)
                chapters_pruned += 1

        if _force_set:
            workflow_run.metadata_json = {
                **(workflow_run.metadata_json or {}),
                "force_materialize": {
                    "requested_chapters": sorted(_force_set),
                    "forced_chapters": _forced_chapters,
                    "rejected_active_draft_chapters": _force_rejected_active_draft,
                },
            }

        workflow_run.current_step = "completed"
        workflow_run.status = WorkflowStatus.COMPLETED.value
        workflow_run.metadata_json = {
            **workflow_run.metadata_json,
            **truth_metadata_for_workflow(project),
            "chapters_created": chapters_created,
            "scenes_created": scenes_created,
            "chapters_updated": chapters_updated,
            "scenes_updated": scenes_updated,
            "chapters_pruned": chapters_pruned,
            "scenes_pruned": scenes_pruned,
            "chapters_skipped_immutable": chapters_skipped_immutable,
        }
        await session.flush()

        return WorkflowMaterializationResult(
            workflow_run_id=workflow_run.id,
            project_id=project.id,
            batch_name=batch.batch_name,
            chapters_created=chapters_created,
            scenes_created=scenes_created,
            source_artifact_id=source_artifact_id,
        )
    except Exception as exc:
        workflow_run.status = WorkflowStatus.FAILED.value
        workflow_run.current_step = current_step_name
        workflow_run.error_message = str(exc)
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.FAILED,
            error_message=str(exc),
        )
        await session.flush()
        raise


async def materialize_latest_chapter_outline_batch(
    session: AsyncSession,
    project_slug: str,
    *,
    requested_by: str = "system",
    force_chapter_numbers: list[int] | None = None,
) -> WorkflowMaterializationResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    artifact = await get_latest_planning_artifact(
        session,
        project_id=project.id,
        artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
    )
    if artifact is None:
        raise ValueError(
            f"Project '{project_slug}' does not have a stored chapter outline batch artifact."
        )

    batch = ChapterOutlineBatchInput.model_validate(artifact.content)
    return await materialize_chapter_outline_batch(
        session,
        project_slug,
        batch,
        requested_by=requested_by,
        source_artifact_id=artifact.id,
        prune_missing_planned=True,
        force_chapter_numbers=force_chapter_numbers,
    )


def _audit_bible_completeness(
    *,
    project: ProjectModel,
    project_slug: str,
    book_spec_content: dict[str, Any] | None,
    world_spec_content: dict[str, Any] | None,
    cast_spec_content: dict[str, Any] | None,
) -> None:
    """Run L2 BibleCompletenessGate before persisting story-bible rows.

    Generation-time repair should already have consumed this feedback. If an
    incomplete bible still reaches materialization, fail here rather than
    persisting a known-broken character foundation.
    """

    try:
        gates_cfg = get_quality_gates_config()
    except Exception:  # pragma: no cover - defensive: config load shouldn't block materialization
        logger.debug("failed to load quality gates config; skipping L2 bible audit", exc_info=True)
        return

    l2_cfg = getattr(gates_cfg, "l2", None)
    l2_enabled = bool(getattr(l2_cfg, "enabled", False)) if l2_cfg is not None else False
    if not l2_enabled:
        return

    invariants_payload = getattr(project, "invariants_json", None)
    if not invariants_payload:
        logger.debug(
            "project %s has no invariants payload; skipping L2 bible audit",
            project_slug,
        )
        return

    try:
        invariants = invariants_from_dict(invariants_payload)
    except Exception:
        logger.warning(
            "project %s has invalid invariants payload; skipping L2 bible audit",
            project_slug,
            exc_info=True,
        )
        return

    try:
        draft = build_draft_from_materialization_content(
            book_spec_content=book_spec_content,
            world_spec_content=world_spec_content,
            cast_spec_content=cast_spec_content,
        )
        report = validate_bible_completeness(draft, invariants)
    except Exception:
        logger.warning(
            "L2 bible audit raised for project %s; treating as clean",
            project_slug,
            exc_info=True,
        )
        return

    if report.passes:
        logger.info("L2 bible gate passed for project %s", project_slug)
        return

    # Summarise deficiencies for observability. The full prompt feedback is
    # already wrapped by report.feedback_for_regen() — log it at DEBUG so
    # the info level stays scannable.
    codes = sorted({d.code for d in report.deficiencies})
    feedback = report.feedback_for_regen()
    logger.warning(
        "L2 bible gate blocked materialization with %d deficiencies for project %s: codes=%s",
        len(report.deficiencies),
        project_slug,
        codes,
    )
    logger.debug(
        "L2 bible gate full feedback for project %s:\n%s",
        project_slug,
        feedback,
    )
    raise ValueError(
        f"L2 bible gate failed for project '{project_slug}'. Regenerate the story bible.\n"
        f"{feedback}"
    )


async def materialize_story_bible(
    session: AsyncSession,
    project_slug: str,
    *,
    requested_by: str = "system",
    book_spec_content: dict[str, Any] | None = None,
    world_spec_content: dict[str, Any] | None = None,
    cast_spec_content: dict[str, Any] | None = None,
    volume_plan_content: dict[str, Any] | list[dict[str, Any]] | None = None,
    source_artifact_ids: dict[str, UUID] | None = None,
) -> StoryBibleMaterializationResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    artifact_ids = dict(source_artifact_ids or {})
    requested_payloads = {
        "book_spec": book_spec_content,
        "world_spec": world_spec_content,
        "cast_spec": cast_spec_content,
        "volume_plan": volume_plan_content,
    }
    applied_artifacts = [name for name, payload in requested_payloads.items() if payload is not None]
    if not applied_artifacts:
        raise ValueError("No story bible content was provided.")

    settings = load_settings()
    if (
        cast_spec_content is not None
        and getattr(settings.pipeline, "require_foundation_identity_lock", True)
    ):
        if "cast_spec" in artifact_ids:
            characters = list(
                await session.scalars(
                    select(CharacterModel).where(CharacterModel.project_id == project.id)
                )
            )
            existing_manifest = _project_identity_manifest(project)
            repaired_content, repair_count = repair_legacy_foundation_identity_locks(
                cast_spec_content,
                identity_hints=[
                    *existing_manifest,
                    *_identity_hints_from_characters(characters),
                ],
                allow_unreliable_defaults=bool(existing_manifest),
            )
            if repair_count and repaired_content is not None:
                cast_spec_content = repaired_content
        _identity_contract = validate_foundation_identity_contract(cast_spec_content)
        _identity_contract.raise_for_blocks(
            project_slug=project_slug,
            artifact="cast_spec",
        )

    # L2 Bible Completeness Gate — run pre-persistence so a known-incomplete
    # character/world bible never gets committed. Planner generation gets the
    # first repair attempt; this is the final blocking guard.
    try:
        _audit_bible_completeness(
            project=project,
            project_slug=project_slug,
            book_spec_content=book_spec_content,
            world_spec_content=world_spec_content,
            cast_spec_content=cast_spec_content,
        )
    except ValueError:
        if cast_spec_content is None:
            raise
        synthesized_cast_spec = _synthesize_materialization_cast_bible_fields(
            project,
            cast_spec_content,
        )
        if synthesized_cast_spec == cast_spec_content:
            raise
        logger.warning(
            "Story-bible materialization synthesized missing character "
            "personhood anchors for project %s after initial L2 audit block.",
            project_slug,
        )
        _audit_bible_completeness(
            project=project,
            project_slug=project_slug,
            book_spec_content=book_spec_content,
            world_spec_content=world_spec_content,
            cast_spec_content=synthesized_cast_spec,
        )
        cast_spec_content = synthesized_cast_spec

    workflow_run = await create_workflow_run(
        session,
        project_id=project.id,
        workflow_type=WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE,
        status=WorkflowStatus.RUNNING,
        scope_type="project",
        scope_id=project.id,
        requested_by=requested_by,
        current_step="load_story_bible",
        metadata={
            **truth_metadata_for_workflow(project),
            "project_slug": project_slug,
            "applied_artifacts": applied_artifacts,
            "source_artifact_ids": {key: str(value) for key, value in artifact_ids.items()},
        },
    )

    step_order = 1
    counts = {
        "world_rules_upserted": 0,
        "locations_upserted": 0,
        "factions_upserted": 0,
        "characters_upserted": 0,
        "relationships_upserted": 0,
        "state_snapshots_created": 0,
        "voice_profiles_populated": 0,
        "moral_frameworks_populated": 0,
        "volumes_upserted": 0,
        "world_backbones_upserted": 0,
        "volume_frontiers_upserted": 0,
        "deferred_reveals_upserted": 0,
        "expansion_gates_upserted": 0,
    }
    current_step_name = "load_story_bible"

    try:
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            input_ref={
                "applied_artifacts": applied_artifacts,
                "source_artifact_ids": {key: str(value) for key, value in artifact_ids.items()},
            },
        )
        step_order += 1

        if book_spec_content is not None:
            current_step_name = "apply_book_spec"
            workflow_run.current_step = current_step_name
            await apply_book_spec(session, project, book_spec_content)
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    "artifact_type": ArtifactType.BOOK_SPEC.value,
                    "source_artifact_id": str(artifact_ids["book_spec"]) if "book_spec" in artifact_ids else None,
                },
            )
            step_order += 1

        if world_spec_content is not None:
            current_step_name = "apply_world_spec"
            workflow_run.current_step = current_step_name
            world_counts = await upsert_world_spec(session, project, world_spec_content)
            for key, value in world_counts.items():
                counts[key] = counts.get(key, 0) + value
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    **world_counts,
                    "artifact_type": ArtifactType.WORLD_SPEC.value,
                    "source_artifact_id": str(artifact_ids["world_spec"]) if "world_spec" in artifact_ids else None,
                },
            )
            step_order += 1

        if cast_spec_content is not None:
            current_step_name = "apply_cast_spec"
            workflow_run.current_step = current_step_name
            cast_counts = await upsert_cast_spec(session, project, cast_spec_content)
            characters = list(
                await session.scalars(
                    select(CharacterModel).where(CharacterModel.project_id == project.id)
                )
            )
            identity_manifest = _merge_identity_manifest_entries(
                _project_identity_manifest(project),
                build_identity_manifest(cast_spec_content),
                _identity_hints_from_characters(characters),
            )
            if identity_manifest:
                project.metadata_json = {
                    **(project.metadata_json or {}),
                    "identity_manifest": identity_manifest,
                    "identity_manifest_status": "locked",
                }
                _apply_identity_manifest_to_characters(characters, identity_manifest)
            for key, value in cast_counts.items():
                counts[key] = counts.get(key, 0) + value
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    **cast_counts,
                    "identity_manifest_count": len(identity_manifest),
                    "artifact_type": ArtifactType.CAST_SPEC.value,
                    "source_artifact_id": str(artifact_ids["cast_spec"]) if "cast_spec" in artifact_ids else None,
                },
            )
            step_order += 1

        if volume_plan_content is not None:
            current_step_name = "apply_volume_plan"
            workflow_run.current_step = current_step_name
            volume_counts = await upsert_volume_plan(session, project, volume_plan_content)
            for key, value in volume_counts.items():
                counts[key] = counts.get(key, 0) + value
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    **volume_counts,
                    "artifact_type": ArtifactType.VOLUME_PLAN.value,
                    "source_artifact_id": str(artifact_ids["volume_plan"]) if "volume_plan" in artifact_ids else None,
                },
            )
            step_order += 1

        if any(payload is not None for payload in (book_spec_content, world_spec_content, cast_spec_content, volume_plan_content)):
            current_step_name = "refresh_world_expansion_boundaries"
            workflow_run.current_step = current_step_name
            boundary_counts = await refresh_world_expansion_boundaries(session, project=project)
            for key, value in boundary_counts.items():
                counts[key] = counts.get(key, 0) + value
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref=boundary_counts,
            )
            step_order += 1

        if any(payload is not None for payload in (world_spec_content, cast_spec_content, volume_plan_content)):
            current_step_name = "refresh_story_bible_retrieval"
            workflow_run.current_step = current_step_name
            retrieval_chunk_count = await refresh_story_bible_retrieval_index(session, load_settings(), project.id)
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={"retrieval_chunk_count": retrieval_chunk_count},
            )
            step_order += 1

        workflow_run.current_step = "completed"
        workflow_run.status = WorkflowStatus.COMPLETED.value
        workflow_run.metadata_json = {
            **workflow_run.metadata_json,
            **truth_metadata_for_workflow(project),
            **counts,
        }
        await session.flush()

        return StoryBibleMaterializationResult(
            workflow_run_id=workflow_run.id,
            project_id=project.id,
            applied_artifacts=applied_artifacts,
            source_artifact_ids=artifact_ids,
            **counts,
        )
    except Exception as exc:
        workflow_run.status = WorkflowStatus.FAILED.value
        workflow_run.current_step = current_step_name
        workflow_run.error_message = str(exc)
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.FAILED,
            error_message=str(exc),
        )
        await session.flush()
        raise


async def materialize_latest_story_bible(
    session: AsyncSession,
    project_slug: str,
    *,
    requested_by: str = "system",
) -> StoryBibleMaterializationResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    artifacts: dict[str, PlanningArtifactVersionModel] = {}
    for artifact_type in (
        ArtifactType.BOOK_SPEC,
        ArtifactType.WORLD_SPEC,
        ArtifactType.CAST_SPEC,
        ArtifactType.VOLUME_PLAN,
    ):
        artifact = await get_latest_planning_artifact(
            session,
            project_id=project.id,
            artifact_type=artifact_type,
        )
        if artifact is not None:
            artifacts[artifact_type.value] = artifact

    if not artifacts:
        raise ValueError(f"Project '{project_slug}' does not have any stored story bible artifacts.")

    return await materialize_story_bible(
        session,
        project_slug,
        requested_by=requested_by,
        book_spec_content=artifacts.get(ArtifactType.BOOK_SPEC.value).content
        if ArtifactType.BOOK_SPEC.value in artifacts
        else None,
        world_spec_content=artifacts.get(ArtifactType.WORLD_SPEC.value).content
        if ArtifactType.WORLD_SPEC.value in artifacts
        else None,
        cast_spec_content=artifacts.get(ArtifactType.CAST_SPEC.value).content
        if ArtifactType.CAST_SPEC.value in artifacts
        else None,
        volume_plan_content=artifacts.get(ArtifactType.VOLUME_PLAN.value).content
        if ArtifactType.VOLUME_PLAN.value in artifacts
        else None,
        source_artifact_ids={key: artifact.id for key, artifact in artifacts.items()},
    )


async def materialize_narrative_graph(
    session: AsyncSession,
    project_slug: str,
    *,
    requested_by: str = "system",
    volume_plan_content: dict[str, Any] | list[dict[str, Any]] | None = None,
    source_artifact_ids: dict[str, UUID] | None = None,
) -> NarrativeGraphMaterializationResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    artifact_ids = dict(source_artifact_ids or {})
    workflow_run = await create_workflow_run(
        session,
        project_id=project.id,
        workflow_type=WORKFLOW_TYPE_MATERIALIZE_NARRATIVE_GRAPH,
        status=WorkflowStatus.RUNNING,
        scope_type="project",
        scope_id=project.id,
        requested_by=requested_by,
        current_step="load_narrative_sources",
        metadata={
            **truth_metadata_for_workflow(project),
            "project_slug": project_slug,
            "source_artifact_ids": {key: str(value) for key, value in artifact_ids.items()},
        },
    )

    step_order = 1
    current_step_name = "load_narrative_sources"
    try:
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            input_ref={
                "source_artifact_ids": {key: str(value) for key, value in artifact_ids.items()},
                "uses_volume_plan": volume_plan_content is not None,
            },
        )
        step_order += 1

        current_step_name = "rebuild_narrative_graph"
        workflow_run.current_step = current_step_name
        counts = await rebuild_narrative_graph(
            session,
            project=project,
            volume_plan_content=volume_plan_content,
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref=counts,
        )
        step_order += 1

        workflow_run.current_step = "completed"
        workflow_run.status = WorkflowStatus.COMPLETED.value
        workflow_run.metadata_json = {
            **workflow_run.metadata_json,
            **truth_metadata_for_workflow(project),
            **counts,
        }
        await session.flush()

        return NarrativeGraphMaterializationResult(
            workflow_run_id=workflow_run.id,
            project_id=project.id,
            source_artifact_ids=artifact_ids,
            **counts,
        )
    except Exception as exc:
        workflow_run.status = WorkflowStatus.FAILED.value
        workflow_run.current_step = current_step_name
        workflow_run.error_message = str(exc)
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.FAILED,
            error_message=str(exc),
        )
        await session.flush()
        raise


async def materialize_latest_narrative_graph(
    session: AsyncSession,
    project_slug: str,
    *,
    requested_by: str = "system",
) -> NarrativeGraphMaterializationResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    source_artifact_ids: dict[str, UUID] = {}
    volume_plan_content = None
    volume_plan_artifact = await get_latest_planning_artifact(
        session,
        project_id=project.id,
        artifact_type=ArtifactType.VOLUME_PLAN,
    )
    if volume_plan_artifact is not None:
        volume_plan_content = volume_plan_artifact.content
        source_artifact_ids[ArtifactType.VOLUME_PLAN.value] = volume_plan_artifact.id

    return await materialize_narrative_graph(
        session,
        project_slug,
        requested_by=requested_by,
        volume_plan_content=volume_plan_content,
        source_artifact_ids=source_artifact_ids,
    )


async def materialize_narrative_tree(
    session: AsyncSession,
    project_slug: str,
    *,
    requested_by: str = "system",
) -> NarrativeTreeMaterializationResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    workflow_run = await create_workflow_run(
        session,
        project_id=project.id,
        workflow_type=WORKFLOW_TYPE_MATERIALIZE_NARRATIVE_TREE,
        status=WorkflowStatus.RUNNING,
        scope_type="project",
        scope_id=project.id,
        requested_by=requested_by,
        current_step="rebuild_narrative_tree",
        metadata={"project_slug": project_slug},
    )
    step_order = 1
    current_step_name = "rebuild_narrative_tree"
    try:
        counts = await rebuild_narrative_tree(session, project=project)
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref=counts,
        )
        workflow_run.current_step = "completed"
        workflow_run.status = WorkflowStatus.COMPLETED.value
        workflow_run.metadata_json = {
            **workflow_run.metadata_json,
            **counts,
        }
        await session.flush()
        return NarrativeTreeMaterializationResult(
            workflow_run_id=workflow_run.id,
            project_id=project.id,
            node_count=int(counts.get("node_count", 0)),
            node_type_counts=dict(counts.get("node_type_counts", {})),
        )
    except Exception as exc:
        workflow_run.status = WorkflowStatus.FAILED.value
        workflow_run.current_step = current_step_name
        workflow_run.error_message = str(exc)
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.FAILED,
            error_message=str(exc),
        )
        await session.flush()
        raise


async def materialize_latest_narrative_tree(
    session: AsyncSession,
    project_slug: str,
    *,
    requested_by: str = "system",
) -> NarrativeTreeMaterializationResult:
    return await materialize_narrative_tree(
        session,
        project_slug,
        requested_by=requested_by,
    )
