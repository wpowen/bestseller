from __future__ import annotations

import logging
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bestseller.domain.enums import ArtifactType, ChapterStatus, SceneStatus, WorkflowStatus
from bestseller.domain.narrative import NarrativeGraphMaterializationResult
from bestseller.domain.narrative_tree import NarrativeTreeMaterializationResult
from bestseller.domain.project import ChapterCreate, SceneCardCreate, VolumeCreate
from bestseller.domain.story_bible import StoryBibleMaterializationResult
from bestseller.domain.workflow import (
    ChapterOutlineInput,
    ChapterOutlineBatchInput,
    WorkflowMaterializationResult,
)
from bestseller.infra.db.models import (
    CharacterModel,
    ChapterModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    SceneCardModel,
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
from bestseller.services.invariants import invariants_from_dict
from bestseller.services.projects import create_chapter, create_or_get_volume, create_scene_card, get_project_by_slug
from bestseller.services.narrative import rebuild_narrative_graph
from bestseller.services.narrative_tree import rebuild_narrative_tree
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
from bestseller.services.methodology_overlay import (
    methodology_contract_blocks,
    methodology_contract_requires_checks,
    normalize_chapter_overlay,
    normalize_scene_overlay,
    resolve_methodology_contract_mode,
)
from bestseller.services.quality_gates_config import get_quality_gates_config
from bestseller.services.retrieval import refresh_story_bible_retrieval_index
from bestseller.services.story_bible import (
    apply_book_spec,
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

_MATERIALIZATION_MUTABLE_CHAPTER_STATUSES = {
    ChapterStatus.PLANNED.value,
    ChapterStatus.OUTLINING.value,
}
_MATERIALIZATION_MUTABLE_SCENE_STATUSES = {
    SceneStatus.PLANNED.value,
}


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
        for scene in chapter.scenes:
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
                purpose["emotion"] = "保持本章压力递进，并把选择、代价或线索推到下一拍。"
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
) -> bool:
    """Update an existing planned/outlining chapter from the latest outline."""
    if chapter.status not in _MATERIALIZATION_MUTABLE_CHAPTER_STATUSES:
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
    setattr(chapter, "metadata_json", metadata)


def _sync_existing_scene_from_outline(scene: SceneCardModel, scene_outline: Any) -> bool:
    if scene.status not in _MATERIALIZATION_MUTABLE_SCENE_STATUSES:
        return False
    scene.scene_type = scene_outline.scene_type
    scene.title = scene_outline.title
    scene.time_label = scene_outline.time_label
    scene.participants = scene_outline.participants
    scene.purpose = scene_outline.purpose
    scene.entry_state = scene_outline.entry_state
    scene.exit_state = scene_outline.exit_state
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
) -> WorkflowRunModel:
    workflow_run = WorkflowRunModel(
        project_id=project_id,
        workflow_type=workflow_type,
        status=status.value,
        scope_type=scope_type,
        scope_id=scope_id,
        requested_by=requested_by,
        current_step=current_step,
        metadata_json=metadata or {},
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
            if (
                _blocking_findings
                and getattr(settings.pipeline, "chapter_causality_gate_block_on_failure", True)
            ):
                raise ValueError(
                    "Chapter outline batch blocked by chapter_causality_contract: "
                    f"{len(_blocking_findings)} blocking finding(s)."
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
            if (
                existing_chapter is not None
                and existing_chapter.status not in _MATERIALIZATION_MUTABLE_CHAPTER_STATUSES
            ):
                chapters_skipped_immutable += 1
                continue

            should_sync_causality_metadata = False
            if existing_chapter is not None:
                chapter = existing_chapter
                if await _sync_existing_chapter_from_outline(
                    session,
                    project_id=project.id,
                    chapter=chapter,
                    chapter_outline=chapter_outline,
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
                    if _sync_existing_scene_from_outline(scene, scene_outline):
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
            # normalization above.
            _num_scenes = len(chapter_outline.scenes)
            if _num_scenes > 0:
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
    _audit_bible_completeness(
        project=project,
        project_slug=project_slug,
        book_spec_content=book_spec_content,
        world_spec_content=world_spec_content,
        cast_spec_content=cast_spec_content,
    )

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
