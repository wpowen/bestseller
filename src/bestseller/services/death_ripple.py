"""Death-ripple service — propagate the consequences of a character's
death across the people who knew them.

When a death is committed (via ``feedback.extract_chapter_feedback`` or
a manual repair script), most other layers of the system happily
update the deceased's row and forget the rest of the cast. In real
fiction — and in real life — a death sends ripples through every close
relationship: the spouse grieves, the master's disciple gains a wound,
the avenger inherits the unfinished oath. Without these ripples the
world reads flat: chapter N+5 has nobody mourning, chapter N+30 has
nobody remembering. This module is the propagation layer.

Behaviour
---------

For each high-strength relationship (``RelationshipModel.strength >=
GRIEF_THRESHOLD``) the deceased had with another character ``Y``, the
service does three things:

1. **Grief snapshot** — emit a ``CharacterStateSnapshotModel`` for ``Y``
   anchored to the death chapter, with ``emotional_state="grieving"``
   and a structured note pointing back to the deceased.
2. **Bereavement event** — write a ``RelationshipEventModel`` row
   (``relationship_change="ended_by_death"``, ``is_milestone=True``)
   so the relationship timeline carries the closure event.
3. **Relationship metadata** — fold ``ended_by_death=True`` and
   ``ended_chapter_number=N`` into ``RelationshipModel.metadata_json``
   so future relationship queries can detect dead-side relationships
   and skip them.

The function is **idempotent**: if any of the three writes already
exist for this (deceased, Y) pair, it does not duplicate them. This
matters because feedback extraction can re-run after a chapter rewrite.

The service is triggered after ``_apply_character_state`` commits a
death. It is intentionally side-effect-only and returns a small report
struct so callers can log what propagated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    CharacterModel,
    CharacterStateSnapshotModel,
    RelationshipEventModel,
    RelationshipModel,
)

logger = logging.getLogger(__name__)


# Relationships at or above this strength produce grief ripples. The
# ``strength`` column ranges [-1, 1]; mid-positive captures close
# allies / family / mentors / lovers without firing on every casual
# acquaintance.
GRIEF_THRESHOLD: float = 0.4

# Strong negative relationships are also load-bearing on death — a
# nemesis dying is a major event for the protagonist. The ripple is
# written but tagged differently so the prompt layer can distinguish
# "grief" from "vengeance closure" / "release".
ENMITY_THRESHOLD: float = -0.4


@dataclass(frozen=True)
class RippleEntry:
    """One propagation result — a single survivor's reaction to the death."""

    survivor_name: str
    deceased_name: str
    relationship_type: str
    relationship_strength: float
    response_kind: str  # "grief" | "vengeance_closure" | "release"
    snapshot_created: bool
    relationship_event_created: bool
    relationship_marked_ended: bool


@dataclass(frozen=True)
class DeathRippleReport:
    """Aggregate report of one death's propagation."""

    deceased_name: str
    deceased_id: UUID
    chapter_number: int
    entries: tuple[RippleEntry, ...] = field(default_factory=tuple)

    @property
    def grief_count(self) -> int:
        return sum(1 for e in self.entries if e.response_kind == "grief")

    @property
    def vengeance_closure_count(self) -> int:
        return sum(1 for e in self.entries if e.response_kind == "vengeance_closure")


def _classify_response(strength: float) -> str | None:
    """Map a relationship strength to a ripple response kind.

    Returns ``None`` when the relationship is too weak to ripple — the
    caller skips writing for those pairs to avoid mass-flooding the
    snapshot table on a death of a widely-known but not-deeply-known
    character.
    """
    if strength >= GRIEF_THRESHOLD:
        return "grief"
    if strength <= ENMITY_THRESHOLD:
        return "vengeance_closure"
    return None


async def _existing_bereavement_event(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_number: int,
    a_label: str,
    b_label: str,
) -> RelationshipEventModel | None:
    """Look up an existing bereavement event for this pair + chapter so
    the ripple stays idempotent across re-runs."""
    stmt = select(RelationshipEventModel).where(
        RelationshipEventModel.project_id == project_id,
        RelationshipEventModel.chapter_number == chapter_number,
        RelationshipEventModel.relationship_change == "ended_by_death",
        (
            (
                (RelationshipEventModel.character_a_label == a_label)
                & (RelationshipEventModel.character_b_label == b_label)
            )
            | (
                (RelationshipEventModel.character_a_label == b_label)
                & (RelationshipEventModel.character_b_label == a_label)
            )
        ),
    ).limit(1)
    return await session.scalar(stmt)


async def _existing_grief_snapshot(
    session: AsyncSession,
    *,
    project_id: UUID,
    survivor_id: UUID,
    chapter_number: int,
    deceased_name: str,
) -> CharacterStateSnapshotModel | None:
    """Detect an existing grief snapshot referencing this exact death so
    a re-run does not duplicate snapshots."""
    needle = f"death_ripple:{deceased_name}:ch{chapter_number}"
    stmt = select(CharacterStateSnapshotModel).where(
        CharacterStateSnapshotModel.project_id == project_id,
        CharacterStateSnapshotModel.character_id == survivor_id,
        CharacterStateSnapshotModel.chapter_number == chapter_number,
        CharacterStateSnapshotModel.notes == needle,
    ).limit(1)
    return await session.scalar(stmt)


async def apply_death_ripple(
    session: AsyncSession,
    *,
    project_id: UUID,
    deceased: CharacterModel,
    chapter_number: int,
) -> DeathRippleReport:
    """Propagate the consequences of one death across high-strength ties.

    Caller responsibilities:
    * The death must already be committed on ``deceased`` (alive_status
      and death_chapter_number set). This service does NOT write the
      death itself; it only writes ripples.
    * Pass an active ``AsyncSession``. The service flushes its own
      writes but does not commit — the caller commits in its outer
      transaction.

    Idempotent: the function checks for prior bereavement events /
    grief snapshots before writing, so feedback re-runs after a chapter
    rewrite do not multiply ripples.
    """

    rel_rows = list(
        await session.scalars(
            select(RelationshipModel).where(
                RelationshipModel.project_id == project_id,
                (
                    (RelationshipModel.character_a_id == deceased.id)
                    | (RelationshipModel.character_b_id == deceased.id)
                ),
            )
        )
    )

    entries: list[RippleEntry] = []
    for rel in rel_rows:
        # Skip relationships already closed by an earlier ripple — the
        # metadata flag lives in ``RelationshipModel.metadata_json``.
        rel_meta = dict(rel.metadata_json or {})
        already_ended = bool(rel_meta.get("ended_by_death"))

        survivor_id = (
            rel.character_b_id if rel.character_a_id == deceased.id
            else rel.character_a_id
        )
        survivor = await session.get(CharacterModel, survivor_id)
        if survivor is None or not survivor.name:
            continue

        # Don't ripple onto another deceased — they have their own grief
        # if any. Their downstream ripples would have been emitted when
        # they died.
        if (survivor.alive_status or "alive") == "deceased":
            continue

        try:
            strength = float(rel.strength) if rel.strength is not None else 0.0
        except (TypeError, ValueError):
            strength = 0.0
        response = _classify_response(strength)
        if response is None:
            continue

        # 1. Grief / closure snapshot for the survivor.
        snapshot_created = False
        existing_snap = await _existing_grief_snapshot(
            session,
            project_id=project_id,
            survivor_id=survivor.id,
            chapter_number=chapter_number,
            deceased_name=deceased.name,
        )
        if existing_snap is None:
            emotional_label = (
                "grieving" if response == "grief" else "vengeance_resolved"
            )
            snap = CharacterStateSnapshotModel(
                project_id=project_id,
                character_id=survivor.id,
                chapter_id=None,
                chapter_number=chapter_number,
                scene_number=None,
                arc_state=None,
                emotional_state=emotional_label,
                physical_state=None,
                power_tier=None,
                alive_status=None,
                stance=None,
                trust_map={},
                beliefs=[],
                notes=f"death_ripple:{deceased.name}:ch{chapter_number}",
            )
            session.add(snap)
            snapshot_created = True

        # 2. Bereavement event on the relationship timeline.
        rel_event_created = False
        existing_event = await _existing_bereavement_event(
            session,
            project_id=project_id,
            chapter_number=chapter_number,
            a_label=deceased.name,
            b_label=survivor.name,
        )
        if existing_event is None:
            event_description = (
                f"{deceased.name} 在第{chapter_number}章死亡"
                if response == "grief"
                else f"{deceased.name}（宿敌）于第{chapter_number}章死亡"
            )
            # ``RelationshipEventModel`` is a label-only table — both
            # the deceased and the survivor are stored as their string
            # names. The structured FK linkage is on
            # ``RelationshipModel`` (updated below).
            session.add(
                RelationshipEventModel(
                    project_id=project_id,
                    chapter_number=chapter_number,
                    scene_number=None,
                    character_a_label=deceased.name,
                    character_b_label=survivor.name,
                    event_description=event_description,
                    relationship_change="ended_by_death",
                    is_milestone=True,
                    metadata_json={
                        "response_kind": response,
                        "relationship_type": rel.relationship_type,
                        "strength_at_death": strength,
                        "deceased_id": str(deceased.id),
                        "survivor_id": str(survivor.id),
                    },
                )
            )
            rel_event_created = True

        # 3. Mark the RelationshipModel itself as ended.
        rel_marked = False
        if not already_ended:
            new_meta = {
                **rel_meta,
                "ended_by_death": True,
                "ended_chapter_number": chapter_number,
                "ended_response_kind": response,
            }
            await session.execute(
                update(RelationshipModel)
                .where(RelationshipModel.id == rel.id)
                .values(
                    metadata_json=new_meta,
                    last_changed_chapter_no=chapter_number,
                )
            )
            rel_marked = True

        entries.append(
            RippleEntry(
                survivor_name=survivor.name,
                deceased_name=deceased.name,
                relationship_type=rel.relationship_type,
                relationship_strength=strength,
                response_kind=response,
                snapshot_created=snapshot_created,
                relationship_event_created=rel_event_created,
                relationship_marked_ended=rel_marked,
            )
        )

    if entries:
        await session.flush()
        logger.info(
            "death_ripple: %s (ch%d) → %d ripples (%d grief, %d closure)",
            deceased.name,
            chapter_number,
            len(entries),
            sum(1 for e in entries if e.response_kind == "grief"),
            sum(1 for e in entries if e.response_kind == "vengeance_closure"),
        )

    # Roll up the deceased's outstanding interpersonal promises. When a
    # promisor / promisee dies the obligation either passes to a named
    # heir or lapses; this keeps long-running vendetta / protection /
    # message arcs from silently evaporating. Imported lazily so the
    # core ripple service stays usable in environments where the
    # promises ledger has not been migrated yet.
    try:
        from bestseller.services.interpersonal_promises import (  # noqa: PLC0415
            mark_promises_on_death,
        )
        await mark_promises_on_death(
            session,
            project_id=project_id,
            deceased=deceased,
            chapter_number=chapter_number,
        )
    except Exception:
        logger.debug(
            "interpersonal_promises rollup failed for %s (ch=%d) — non-fatal",
            deceased.name,
            chapter_number,
            exc_info=True,
        )

    return DeathRippleReport(
        deceased_name=deceased.name,
        deceased_id=deceased.id,
        chapter_number=chapter_number,
        entries=tuple(entries),
    )


async def apply_death_ripples_for_chapter(
    session: AsyncSession,
    *,
    project_id: UUID,
    deceased_character_ids: list[UUID],
    chapter_number: int,
) -> list[DeathRippleReport]:
    """Convenience: ripple multiple deaths committed in the same chapter.

    Useful when ``feedback.extract_chapter_feedback`` records several
    deaths from one chapter (a battle, a massacre). The reports come
    back in input order.
    """

    if not deceased_character_ids:
        return []

    reports: list[DeathRippleReport] = []
    for char_id in deceased_character_ids:
        deceased = await session.get(CharacterModel, char_id)
        if deceased is None:
            continue
        try:
            report = await apply_death_ripple(
                session,
                project_id=project_id,
                deceased=deceased,
                chapter_number=chapter_number,
            )
        except Exception:
            logger.exception(
                "death_ripple failed for project=%s deceased_id=%s ch=%d",
                project_id, char_id, chapter_number,
            )
            continue
        reports.append(report)
    return reports
