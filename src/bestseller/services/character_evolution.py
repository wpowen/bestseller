from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.contradiction import (
    CharacterKnowledgeState,
    CharacterStagnationWarning,
)
from bestseller.infra.db.models import (
    CharacterModel,
    CharacterStateSnapshotModel,
    RelationshipEventModel,
)

logger = logging.getLogger(__name__)

_TRACKED_FIELDS = ("arc_state", "emotional_state", "physical_state", "power_tier")


async def _find_character(
    session: AsyncSession,
    project_id: UUID,
    character_name: str,
) -> CharacterModel | None:
    """Look up a character by project and name."""
    stmt = select(CharacterModel).where(
        CharacterModel.project_id == project_id,
        CharacterModel.name == character_name,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_character_knowledge_state(
    session: AsyncSession,
    project_id: UUID,
    character_name: str,
    as_of_chapter: int,
) -> CharacterKnowledgeState:
    """Build the accumulated knowledge state for a character up to a chapter.

    Starts from the base ``knowledge_state_json`` on the character record, then
    merges in any ``beliefs`` captured in per-scene snapshots.
    """
    character = await _find_character(session, project_id, character_name)
    if character is None:
        return CharacterKnowledgeState(
            character_name=character_name,
            as_of_chapter=as_of_chapter,
        )

    base: dict = character.knowledge_state_json or {}
    knows: list[str] = list(base.get("knows", []))
    falsely_believes: list[str] = list(base.get("falsely_believes", []))
    unaware_of: list[str] = list(base.get("unaware_of", []))

    stmt = (
        select(CharacterStateSnapshotModel)
        .where(
            CharacterStateSnapshotModel.character_id == character.id,
            CharacterStateSnapshotModel.project_id == project_id,
            CharacterStateSnapshotModel.chapter_number <= as_of_chapter,
        )
        .order_by(
            CharacterStateSnapshotModel.chapter_number,
            CharacterStateSnapshotModel.scene_number,
        )
    )
    result = await session.execute(stmt)
    snapshots = result.scalars().all()

    for snap in snapshots:
        beliefs: list = snap.beliefs or []
        for belief in beliefs:
            if isinstance(belief, str) and belief not in knows:
                knows.append(belief)

    return CharacterKnowledgeState(
        character_name=character_name,
        as_of_chapter=as_of_chapter,
        knows=knows,
        falsely_believes=falsely_believes,
        unaware_of=unaware_of,
    )


async def get_character_evolution_timeline(
    session: AsyncSession,
    project_id: UUID,
    character_name: str,
) -> list[dict]:
    """Return an ordered timeline of a character's state snapshots."""
    character = await _find_character(session, project_id, character_name)
    if character is None:
        return []

    stmt = (
        select(CharacterStateSnapshotModel)
        .where(
            CharacterStateSnapshotModel.character_id == character.id,
            CharacterStateSnapshotModel.project_id == project_id,
        )
        .order_by(
            CharacterStateSnapshotModel.chapter_number,
            CharacterStateSnapshotModel.scene_number,
        )
    )
    result = await session.execute(stmt)
    snapshots = result.scalars().all()

    return [
        {
            "chapter_number": snap.chapter_number,
            "scene_number": snap.scene_number,
            "arc_state": snap.arc_state,
            "emotional_state": snap.emotional_state,
            "physical_state": snap.physical_state,
            "power_tier": snap.power_tier,
        }
        for snap in snapshots
    ]


async def detect_character_stagnation(
    session: AsyncSession,
    project_id: UUID,
    current_chapter_number: int,
    stagnation_threshold_chapters: int = 5,
) -> list[CharacterStagnationWarning]:
    """Flag characters whose snapshots haven't been updated recently.

    For each character with at least two snapshots, the latest two are compared
    to identify which tracked fields are stagnant.
    """
    char_stmt = select(CharacterModel).where(CharacterModel.project_id == project_id)
    char_result = await session.execute(char_stmt)
    characters = char_result.scalars().all()

    warnings: list[CharacterStagnationWarning] = []

    for character in characters:
        snap_stmt = (
            select(CharacterStateSnapshotModel)
            .where(
                CharacterStateSnapshotModel.character_id == character.id,
                CharacterStateSnapshotModel.project_id == project_id,
            )
            .order_by(desc(CharacterStateSnapshotModel.chapter_number))
            .limit(2)
        )
        snap_result = await session.execute(snap_stmt)
        recent = snap_result.scalars().all()

        if not recent:
            continue

        latest = recent[0]
        chapters_since = current_chapter_number - latest.chapter_number

        if chapters_since <= stagnation_threshold_chapters:
            continue

        stagnant_fields: list[str] = []
        if len(recent) >= 2:
            previous = recent[1]
            for field in _TRACKED_FIELDS:
                if getattr(latest, field) == getattr(previous, field):
                    stagnant_fields.append(field)
        else:
            stagnant_fields = list(_TRACKED_FIELDS)

        warnings.append(
            CharacterStagnationWarning(
                character_name=character.name,
                last_update_chapter=latest.chapter_number,
                chapters_since_update=chapters_since,
                stagnant_fields=stagnant_fields,
            )
        )

    return warnings


async def get_relationship_evolution(
    session: AsyncSession,
    project_id: UUID,
    character_a_name: str,
    character_b_name: str,
) -> list[dict]:
    """Return the timeline of relationship events between two characters.

    ``RelationshipEventModel`` stores character labels (names) directly, so the
    query matches both orderings (A-B and B-A).
    """
    stmt = (
        select(RelationshipEventModel)
        .where(
            RelationshipEventModel.project_id == project_id,
            (
                (
                    (RelationshipEventModel.character_a_label == character_a_name)
                    & (RelationshipEventModel.character_b_label == character_b_name)
                )
                | (
                    (RelationshipEventModel.character_a_label == character_b_name)
                    & (RelationshipEventModel.character_b_label == character_a_name)
                )
            ),
        )
        .order_by(RelationshipEventModel.chapter_number)
    )
    result = await session.execute(stmt)
    events = result.scalars().all()

    return [
        {
            "chapter_number": evt.chapter_number,
            "scene_number": evt.scene_number,
            "character_a_label": evt.character_a_label,
            "character_b_label": evt.character_b_label,
            "event_description": evt.event_description,
            "relationship_change": evt.relationship_change,
            "is_milestone": evt.is_milestone,
        }
        for evt in events
    ]
