"""Read-only character-state provenance, independent of notes and draft pointers.

Unbound historical rows stay unknown. Only eligible evidence participates in
current state, temporal fallback and change history. No read upgrades a row.
"""

# ruff: noqa: ANN401
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    CharacterModel,
    CharacterStateSnapshotModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    SceneCardModel,
    SceneDraftVersionModel,
)
from bestseller.services.feedback import KNOWLEDGE_SOURCE_KEYS, validate_adopted_knowledge_source

SCHEMA = "character-state-source.v1"
_PAYLOAD_FIELDS = (
    "project_id",
    "character_id",
    "chapter_id",
    "scene_card_id",
    "chapter_number",
    "scene_number",
    "arc_state",
    "emotional_state",
    "physical_state",
    "power_tier",
    "alive_status",
    "stance",
    "trust_map",
    "beliefs",
    "notes",
)


def seal_snapshot_binding(
    snapshot: CharacterStateSnapshotModel, binding: dict | None
) -> dict | None:
    if binding is None:
        return None
    evidence = {k: deepcopy(v) for k, v in binding.items() if k != "payload_sha256"}
    values = {
        k: (str(v) if isinstance(v, UUID) else v)
        for k in _PAYLOAD_FIELDS
        for v in [getattr(snapshot, k)]
    }
    return {**evidence, "payload_sha256": digest({"evidence": evidence, "fields": values})}


def snapshot_binding_matches(snapshot: CharacterStateSnapshotModel, binding: dict | None) -> bool:
    return bool(
        binding is not None and snapshot.source_binding == seal_snapshot_binding(snapshot, binding)
    )


def snapshot_field_value(snapshot: CharacterStateSnapshotModel, field: str) -> Any:
    if field in {"alive_status", "stance"}:
        accepted = (snapshot.source_binding or {}).get("accepted_fields", {})
        if accepted.get(field) is not True:
            return None
    return getattr(snapshot, field)


@dataclass(frozen=True)
class CharacterSnapshotView:
    """Eligible read projection; rejected raw claims remain available in .raw."""

    raw: CharacterStateSnapshotModel

    def __getattr__(self, field: str) -> Any:
        return snapshot_field_value(self.raw, field)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def snapshot_binding(
    kind: str, *, project_id: UUID, character_id: UUID, chapter_number: int, **evidence: Any
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "kind": kind,
        "project_id": str(project_id),
        "character_id": str(character_id),
        "chapter_number": chapter_number,
        "accepted_fields": {"alive_status": True, "stance": True},
        **evidence,
    }


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def _same_source(left: Any, right: Any) -> bool:
    return (
        isinstance(left, Mapping)
        and isinstance(right, Mapping)
        and all(
            left.get(k) is not None and type(left[k]) is type(right.get(k)) and left[k] == right[k]
            for k in KNOWLEDGE_SOURCE_KEYS
        )
    )


async def _adopted_source(
    session: AsyncSession, project_id: UUID, source: Any, memo: dict | None = None
) -> ChapterModel | None:
    if not isinstance(source, Mapping) or source.get("project_id") != str(project_id):
        return None
    draft_id, chapter_id = (
        _uuid(source.get(k)) for k in ("source_chapter_draft_version_id", "chapter_id")
    )
    if draft_id is None or chapter_id is None:
        return None
    key = ("adopted", project_id, tuple(repr(source.get(k)) for k in KNOWLEDGE_SOURCE_KEYS))
    if memo is not None and key in memo:
        return memo[key]
    if memo is not None:
        memo[key] = None
    draft = await session.scalar(
        select(ChapterDraftVersionModel)
        .where(
            ChapterDraftVersionModel.id == draft_id,
            ChapterDraftVersionModel.project_id == project_id,
            ChapterDraftVersionModel.chapter_id == chapter_id,
        )
        .execution_options(populate_existing=True, autoflush=False)
    )
    if draft is None:
        return None
    from bestseller.services.adopted_chapter_source import select_adopted_chapter_source

    chapter = await session.scalar(
        select(ChapterModel)
        .where(ChapterModel.id == chapter_id, ChapterModel.project_id == project_id)
        .execution_options(populate_existing=True, autoflush=False)
    )
    if chapter is None:
        return None
    adopted, _ = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
    if (
        adopted is None
        or adopted.id != draft_id
        or body_hash(adopted.content_md) != source.get("source_body_sha256")
    ):
        return None
    lineage, rejection = await validate_adopted_knowledge_source(
        session,
        project_id=project_id,
        chapter_id=chapter_id,
        source_chapter_draft_version_id=draft.id,
        chapter_md=draft.content_md,
        lock=False,
    )
    if rejection or not _same_source(source, lineage):
        return None
    if memo is not None:
        memo[key] = chapter
    return chapter


async def _planning_source(session: AsyncSession, project: ProjectModel) -> dict | None:
    from bestseller.services.prompt_source_policy import content_hash

    selection = (project.metadata_json or {}).get("active_foundation_selection")
    if (
        not isinstance(selection, dict)
        or selection.get("schema") != "foundation-selection.v1"
        or selection.get("project_id") != str(project.id)
    ):
        return None
    artifact_ids = selection.get("artifact_ids")
    hashes = selection.get("artifact_content_hashes")
    if not isinstance(artifact_ids, dict) or not isinstance(hashes, dict):
        return None
    artifact_id = _uuid(artifact_ids.get("cast_spec"))
    artifact = (
        await session.scalar(
            select(PlanningArtifactVersionModel)
            .where(
                PlanningArtifactVersionModel.id == artifact_id,
                PlanningArtifactVersionModel.project_id == project.id,
                PlanningArtifactVersionModel.artifact_type == "cast_spec",
            )
            .execution_options(autoflush=False)
        )
        if artifact_id
        else None
    )
    if artifact is None or artifact.status not in {"approved", "canonical"}:
        return None
    sha = content_hash(artifact.content)
    if (
        hashes.get("cast_spec") != sha
        or content_hash((project.metadata_json or {}).get("cast_spec")) != sha
    ):
        return None
    return {
        "artifact_id": str(artifact.id),
        "artifact_type": "cast_spec",
        "artifact_content_sha256": sha,
        "selection_sha256": digest(selection),
    }


async def initial_snapshot_binding(
    session: AsyncSession, *, project_id: UUID, character: CharacterModel, knowledge_state: dict
) -> dict | None:
    # Called during materialization; don't flush or refresh staged authored data.
    with session.no_autoflush:
        project = await session.get(ProjectModel, project_id)
        source = await _planning_source(session, project) if project else None
    if source is None or character.project_id != project_id:
        return None
    return snapshot_binding(
        "initial",
        project_id=project_id,
        character_id=character.id,
        chapter_number=0,
        planning_source=source,
        knowledge_state=deepcopy(knowledge_state),
    )


async def _scene_chapter_source(
    session: AsyncSession, *, project_id: UUID, chapter: ChapterModel, scene_draft_id: UUID
) -> dict | None:
    """Only a verifiable assembly proves late adoption; copied ID lists do not."""
    from bestseller.services.adopted_chapter_source import select_adopted_chapter_source
    from bestseller.services.drafts import render_chapter_draft_markdown

    adopted, _ = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
    if adopted is None:
        return None
    ids = adopted.assembled_from_scene_draft_ids or []
    parsed = [_uuid(v) for v in ids]
    if (
        not parsed
        or None in parsed
        or len(set(parsed)) != len(parsed)
        or scene_draft_id not in parsed
    ):
        return None
    drafts = list(
        await session.scalars(
            select(SceneDraftVersionModel)
            .join(
                SceneCardModel,
                SceneDraftVersionModel.scene_card_id == SceneCardModel.id,
            )
            .where(
                SceneDraftVersionModel.id.in_(parsed),
                SceneDraftVersionModel.project_id == project_id,
                SceneCardModel.project_id == project_id,
                SceneCardModel.chapter_id == chapter.id,
            )
            .execution_options(populate_existing=True, autoflush=False)
        )
    )
    by_id = {draft.id: draft for draft in drafts}
    project = await session.get(ProjectModel, project_id)
    if len(by_id) != len(parsed) or project is None:
        return None
    rendered = render_chapter_draft_markdown(
        chapter, [by_id[v] for v in parsed], language=project.language
    )
    if rendered != adopted.content_md:
        return None
    source, rejection = await validate_adopted_knowledge_source(
        session,
        project_id=project_id,
        chapter_id=chapter.id,
        source_chapter_draft_version_id=adopted.id,
        chapter_md=adopted.content_md,
        lock=False,
    )
    return {key: source[key] for key in KNOWLEDGE_SOURCE_KEYS} if not rejection else None


async def scene_snapshot_binding(
    session: AsyncSession,
    *,
    project_id: UUID,
    character: CharacterModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
    draft: SceneDraftVersionModel | None,
    state_payload: dict,
) -> dict | None:
    if (
        draft is None
        or character.project_id != project_id
        or chapter.project_id != project_id
        or scene.project_id != project_id
        or scene.chapter_id != chapter.id
        or draft.project_id != project_id
        or draft.scene_card_id != scene.id
    ):
        return None
    scene_source = {
        "chapter_id": str(chapter.id),
        "scene_card_id": str(scene.id),
        "scene_number": scene.scene_number,
        "source_scene_draft_version_id": str(draft.id),
        "source_version_no": draft.version_no,
        "source_body_sha256": body_hash(draft.content_md),
    }
    known = await _scene_chapter_source(
        session, project_id=project_id, chapter=chapter, scene_draft_id=draft.id
    )
    # An explicit epoch detects a rewrite before a new chapter is adopted.
    frontier = list(
        await session.scalars(
            select(ChapterDraftVersionModel.id)
            .where(
                ChapterDraftVersionModel.project_id == project_id,
                ChapterDraftVersionModel.chapter_id == chapter.id,
            )
            .order_by(ChapterDraftVersionModel.version_no)
            .execution_options(autoflush=False)
        )
    )
    return snapshot_binding(
        "scene",
        project_id=project_id,
        character_id=character.id,
        chapter_number=chapter.chapter_number,
        scene_source=scene_source,
        chapter_source=known,
        chapter_frontier=[str(v) for v in frontier],
        knowledge_state=deepcopy(state_payload.get("knowledge_state")),
        knowledge_delta={"beliefs_gained": list(state_payload.get("beliefs") or [])},
    )


def _trigger_digest(row: CharacterStateSnapshotModel) -> str:
    return digest(
        {
            "id": str(row.id),
            "project_id": str(row.project_id),
            "character_id": str(row.character_id),
            "chapter_number": row.chapter_number,
            "alive_status": row.alive_status,
            "source_binding": row.source_binding,
        }
    )


async def death_snapshot_binding(
    session: AsyncSession,
    *,
    project_id: UUID,
    character_id: UUID,
    deceased_id: UUID,
    chapter_number: int,
    trigger_snapshot_id: UUID | None,
) -> dict | None:
    trigger = (
        await session.get(CharacterStateSnapshotModel, trigger_snapshot_id)
        if trigger_snapshot_id
        else None
    )
    if (
        trigger is None
        or trigger.project_id != project_id
        or trigger.character_id != deceased_id
        or trigger.chapter_number != chapter_number
        or trigger.alive_status != "deceased"
        or (trigger.source_binding or {}).get("kind") != "adopted_feedback"
        or (trigger.source_binding or {}).get("accepted_alive_status") != "deceased"
    ):
        return None
    status = await snapshot_source_status(
        session, trigger, project_id=project_id, character_id=deceased_id
    )
    if status["status"] != "eligible":
        return None
    return snapshot_binding(
        "death_derived",
        project_id=project_id,
        character_id=character_id,
        chapter_number=chapter_number,
        trigger={
            "snapshot_id": str(trigger.id),
            "character_id": str(deceased_id),
            "evidence_sha256": _trigger_digest(trigger),
        },
    )


async def _snapshot_source_status(
    session: AsyncSession,
    snapshot: CharacterStateSnapshotModel,
    *,
    project_id: UUID,
    character_id: UUID,
    before_chapter_number: int | None = None,
    before_scene_number: int | None = None,
    memo: dict | None = None,
) -> dict[str, str]:
    """No notes/alias inference, no updates, no legacy attribution on read."""

    def result(status: str, reason: str) -> dict[str, str]:
        return {"status": status, "reason": reason}

    if snapshot.project_id != project_id or snapshot.character_id != character_id:
        return result("rejected", "owner_mismatch")
    if before_chapter_number is not None and (
        snapshot.chapter_number > before_chapter_number
        or (
            snapshot.chapter_number == before_chapter_number
            and before_scene_number is not None
            and (snapshot.scene_number is None or snapshot.scene_number >= before_scene_number)
        )
    ):
        return result("rejected", "outside_cutoff")
    binding = snapshot.source_binding
    if not binding:
        return result("unknown", "unbound_legacy")
    if (
        not isinstance(binding, dict)
        or binding.get("schema") != SCHEMA
        or binding.get("project_id") != str(project_id)
        or binding.get("character_id") != str(character_id)
        or type(binding.get("chapter_number")) is not int
        or binding["chapter_number"] != snapshot.chapter_number
    ):
        return result("rejected", "binding_owner_mismatch")
    kind = binding.get("kind")
    if kind not in {"adopted_feedback", "initial", "scene", "death_derived"}:
        return result("unknown", "unknown_producer")
    if not snapshot_binding_matches(snapshot, binding):
        return result("rejected", "snapshot_payload_changed")
    owner_key = ("owner", project_id, character_id)
    if memo is not None and owner_key in memo:
        owner = memo[owner_key]
    else:
        owner = await session.scalar(
            select(CharacterModel.id)
            .where(
                CharacterModel.id == character_id,
                CharacterModel.project_id == project_id,
            )
            .execution_options(autoflush=False)
        )
        if memo is not None:
            memo[owner_key] = owner
    if owner is None:
        return result("rejected", "owner_mismatch")
    if kind == "adopted_feedback":
        source = binding.get("adopted_source")
        chapter = await _adopted_source(session, project_id, source, memo)
        if (
            chapter is None
            or chapter.id != snapshot.chapter_id
            or chapter.chapter_number != snapshot.chapter_number
            or snapshot.scene_card_id is not None
            or snapshot.scene_number is not None
        ):
            return result("rejected", "adopted_source_changed")
    elif kind == "initial":
        project = await session.get(ProjectModel, project_id)
        current = await _planning_source(session, project) if project else None
        if (
            snapshot.chapter_number != 0
            or snapshot.scene_number != 0
            or snapshot.chapter_id is not None
            or snapshot.scene_card_id is not None
            or current is None
            or current != binding.get("planning_source")
        ):
            return result("rejected", "planning_source_changed")
    elif kind == "scene":
        src = binding.get("scene_source", {})
        if not isinstance(src, Mapping):
            return result("rejected", "scene_source_changed")
        draft_id, scene_id = (
            _uuid(src.get("source_scene_draft_version_id")),
            _uuid(src.get("scene_card_id")),
        )
        draft = (
            await session.scalar(
                select(SceneDraftVersionModel)
                .where(
                    SceneDraftVersionModel.id == draft_id,
                    SceneDraftVersionModel.project_id == project_id,
                )
                .execution_options(populate_existing=True, autoflush=False)
            )
            if draft_id
            else None
        )
        scene = await session.get(SceneCardModel, scene_id) if scene_id else None
        chapter = (
            await session.get(ChapterModel, snapshot.chapter_id) if snapshot.chapter_id else None
        )
        if (
            draft is None
            or scene is None
            or chapter is None
            or chapter.project_id != project_id
            or scene.project_id != project_id
            or scene.chapter_id != chapter.id
            or draft.scene_card_id != scene.id
            or scene.id != snapshot.scene_card_id
            or scene.scene_number != snapshot.scene_number
            or scene.scene_number != src.get("scene_number")
            or str(chapter.id) != src.get("chapter_id")
            or chapter.chapter_number != snapshot.chapter_number
            or not draft.is_current
            or draft.promotion_state != "promoted"
            or type(src.get("source_version_no")) is not int
            or draft.version_no != src["source_version_no"]
            or body_hash(draft.content_md) != src.get("source_body_sha256")
        ):
            return result("rejected", "scene_source_changed")
        known = await _scene_chapter_source(
            session, project_id=project_id, chapter=chapter, scene_draft_id=draft.id
        )
        bound = binding.get("chapter_source")
        if bound is not None:
            if not _same_source(bound, known):
                return result("rejected", "chapter_source_changed")
        elif known is None:
            frontier = [
                str(v)
                for v in await session.scalars(
                    select(ChapterDraftVersionModel.id)
                    .where(
                        ChapterDraftVersionModel.project_id == project_id,
                        ChapterDraftVersionModel.chapter_id == chapter.id,
                    )
                    .order_by(ChapterDraftVersionModel.version_no)
                    .execution_options(autoflush=False)
                )
            ]
            if (
                before_chapter_number != snapshot.chapter_number
                or before_scene_number is None
                or binding.get("chapter_frontier") != frontier
                or frontier
            ):
                return result("unknown", "scene_requires_known_chapter_binding")
    elif kind == "death_derived":
        src = binding.get("trigger", {})
        if not isinstance(src, Mapping):
            return result("rejected", "death_trigger_changed")
        trigger_id, deceased_id = _uuid(src.get("snapshot_id")), _uuid(src.get("character_id"))
        trigger = await session.get(CharacterStateSnapshotModel, trigger_id) if trigger_id else None
        if (
            trigger is None
            or deceased_id is None
            or trigger_id == snapshot.id
            or trigger.project_id != project_id
            or trigger.character_id != deceased_id
            or trigger.chapter_number != snapshot.chapter_number
            or trigger.alive_status != "deceased"
            or (trigger.source_binding or {}).get("kind") != "adopted_feedback"
            or (trigger.source_binding or {}).get("accepted_alive_status") != "deceased"
            or src.get("evidence_sha256") != _trigger_digest(trigger)
        ):
            return result("rejected", "death_trigger_changed")
        status = await _snapshot_source_status(
            session,
            trigger,
            project_id=project_id,
            character_id=deceased_id,
            before_chapter_number=before_chapter_number,
            before_scene_number=before_scene_number,
            memo=memo,
        )
        if status["status"] != "eligible":
            return result("rejected", "death_trigger_changed")
    else:
        return result("unknown", "unknown_producer")
    return result("eligible", "source_current")


async def snapshot_source_status(
    session: AsyncSession,
    snapshot: CharacterStateSnapshotModel,
    *,
    project_id: UUID,
    character_id: UUID,
    before_chapter_number: int | None = None,
    before_scene_number: int | None = None,
) -> dict[str, str]:
    if isinstance(snapshot, CharacterSnapshotView):
        snapshot = snapshot.raw
    with session.no_autoflush:
        return await _snapshot_source_status(
            session,
            snapshot,
            project_id=project_id,
            character_id=character_id,
            before_chapter_number=before_chapter_number,
            before_scene_number=before_scene_number,
            memo={},
        )


async def load_eligible_character_snapshots(
    session: AsyncSession,
    *,
    project_id: UUID,
    character_id: UUID,
    before_chapter_number: int | None = None,
    before_scene_number: int | None = None,
    limit: int | None = 64,
) -> list[CharacterSnapshotView]:
    if limit is not None and limit <= 0:
        return []
    query = select(CharacterStateSnapshotModel).where(
        CharacterStateSnapshotModel.project_id == project_id,
        CharacterStateSnapshotModel.character_id == character_id,
    )
    if before_chapter_number is not None:
        query = query.where(CharacterStateSnapshotModel.chapter_number <= before_chapter_number)
        if before_scene_number is not None:
            query = query.where(
                or_(
                    CharacterStateSnapshotModel.chapter_number < before_chapter_number,
                    and_(
                        CharacterStateSnapshotModel.scene_number.is_not(None),
                        CharacterStateSnapshotModel.scene_number < before_scene_number,
                    ),
                )
            )
    query = query.order_by(
        CharacterStateSnapshotModel.chapter_number.desc(),
        CharacterStateSnapshotModel.scene_number.desc().nullslast(),
        CharacterStateSnapshotModel.created_at.desc(),
        CharacterStateSnapshotModel.id.desc(),
    )
    eligible = []
    memo: dict = {}
    with session.no_autoflush:
        # Invalid rows never consume the eligible limit.
        for row in await session.scalars(
            query.execution_options(populate_existing=True, autoflush=False)
        ):
            status = await _snapshot_source_status(
                session,
                row,
                project_id=project_id,
                character_id=character_id,
                before_chapter_number=before_chapter_number,
                before_scene_number=before_scene_number,
                memo=memo,
            )
            if status["status"] == "eligible":
                eligible.append(CharacterSnapshotView(row))
                if limit is not None and len(eligible) >= limit:
                    break
    return eligible


async def read_character_snapshot_knowledge(
    session: AsyncSession,
    *,
    project_id: UUID,
    character: CharacterModel,
    before_chapter_number: int | None = None,
    before_scene_number: int | None = None,
    planning_initial_knowledge_unknown: bool = False,
) -> dict[str, Any]:
    """Fold explicit knowledge, not beliefs-as-knows or an unscoped current row."""
    from bestseller.services.knowledge_epistemic_admission import (
        admit_cognitive_update,
        merge_cognitive_admission,
    )

    if character.project_id != project_id:
        return {}
    snapshots = await load_eligible_character_snapshots(
        session,
        project_id=project_id,
        character_id=character.id,
        before_chapter_number=before_chapter_number,
        before_scene_number=before_scene_number,
        limit=None,
    )
    events = []
    for row in reversed(snapshots):
        if row.chapter_number == 0 and planning_initial_knowledge_unknown:
            continue
        binding = row.source_binding
        events.append((row.chapter_number, binding))
    # Parent's Bible projection history is explicit source-bound evidence.
    # Read deltas within the requested prefix; never copy the whole current row.
    with session.no_autoflush:
        memo: dict = {}
        for entry in (character.metadata_json or {}).get("story_bible_update_history", []):
            if not isinstance(entry, Mapping):
                continue
            source, update = entry.get("source_lineage"), entry.get("update")
            chapter = await _adopted_source(session, project_id, source, memo)
            if chapter is None or not isinstance(update, dict):
                continue
            # Source identity alone proves an adopted body, not that this
            # character delta was successfully extracted and applied. Histories
            # remain audit data even when their receipt is missing or rejected.
            name = update.get("name")
            if not isinstance(name, str) or name.strip() != character.name:
                continue
            receipts = (chapter.metadata_json or {}).get("story_bible_update_receipts", [])
            if not isinstance(receipts, list):
                continue
            latest = next((receipt for receipt in reversed(receipts)
                if isinstance(receipt, Mapping) and receipt.get("status") in {"ok", "empty"}), None)
            if latest is None or not _same_source(latest.get("source_lineage"), source):
                continue
            payload = latest.get("candidate_payload")
            updates = payload.get("character_updates") if isinstance(payload, Mapping) else None
            if not isinstance(updates, list) or update not in updates:
                continue
            if before_chapter_number is not None and (
                chapter.chapter_number > before_chapter_number
                or (
                    chapter.chapter_number == before_chapter_number
                    and before_scene_number is not None
                )
            ):
                continue
            from bestseller.infra.db.models import ChapterDraftVersionModel

            body = await session.scalar(select(ChapterDraftVersionModel.content_md).where(
                ChapterDraftVersionModel.id == UUID(source["source_chapter_draft_version_id"]),
                ChapterDraftVersionModel.project_id == project_id,
                ChapterDraftVersionModel.chapter_id == chapter.id,
            ))
            # Duplicate identical updates lack a persisted original-index witness;
            # cover all exact raw payload locations without choosing a guessed one.
            for update_index, candidate in enumerate(updates):
                if candidate != update:
                    continue
                admission = admit_cognitive_update(
                    update=update, source_lineage=source, source_body=body,
                    application_payload=payload,
                    item_path=f"/character_updates/{update_index}",
                )
                admitted = admission["admitted_update"]
                events.append(
                    (
                        chapter.chapter_number,
                        {
                            "knowledge_delta": {
                                "knowledge_gained": admitted.get("knowledge_gained", []),
                                "false_beliefs": admitted.get("false_beliefs", []),
                            },
                            "epistemic_admission_view": admission,
                        },
                    )
                )
    result: dict[str, Any] = {}
    for _, binding in sorted(events, key=lambda e: e[0]):
        complete = binding.get("knowledge_state")
        if isinstance(complete, dict):
            result = deepcopy(complete)
        if "epistemic_admission_view" in binding:
            result = merge_cognitive_admission(result, binding["epistemic_admission_view"])
        delta = binding.get("knowledge_delta", {})
        if not isinstance(delta, dict):
            continue
        for field, target in (
            ("knowledge_gained", "knows"),
            ("beliefs_gained", "beliefs"),
            ("false_beliefs", "falsely_believes"),
        ):
            incoming = delta.get(field, [])
            if isinstance(incoming, list) and incoming:
                values = list(result.get(target, []))
                result[target] = list(
                    dict.fromkeys([*values, *(v for v in incoming if isinstance(v, str))])
                )
        invalidated = delta.get("beliefs_invalidated", [])
        if isinstance(invalidated, list):
            for field in ("beliefs", "falsely_believes"):
                if field in result:
                    result[field] = [v for v in result[field] if v not in invalidated]
    return result
