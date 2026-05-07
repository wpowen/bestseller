"""Memory-recall scheduler — produce per-chapter cues that prompt the
writer to surface a brief memory beat for a deceased character.

Why this exists
---------------

A long-running novel that loses 50 chapters of grief space after a
beloved character dies reads as ``OOC``: the cast appears to forget
the death happened. Conversely, ten chapters in a row of mournful
flashbacks slows the pacing to a halt. The middle ground is a
**decaying schedule**: anchor cues spaced as ``[+3, +10, +30, +80]``
chapters after the death, and only for characters whose relationship
strength to the deceased was high enough to make the memory
naturalistic.

What this returns
-----------------

A list of :class:`MemoryRecallCue` describing for the chapter being
written:

* who should remember whom,
* the relationship type that frames the memory,
* the cue intensity (``"acute"`` for short-after deaths, ``"settled"``
  for long-after).

The chapter prompt assembler renders these into a soft constraint
block — the writer is asked to weave one or two of them in, but it is
not a hard contract; not every chapter near a recall window has space
for a memory beat.

Pure read-only — the scheduler never mutates DB state. It is safe to
call on every chapter and the result is deterministic for a given
(project, chapter) pair.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    CharacterModel,
    RelationshipModel,
)

logger = logging.getLogger(__name__)


# Anchor offsets relative to the deceased's death chapter. The first
# three are tight clusters covering acute / mid / settled grief; the
# fourth is a long-tail anniversary that keeps the dead alive in
# memory across whole arcs.
_RECALL_ANCHORS: tuple[int, ...] = (3, 10, 30, 80)

# Relationships at or above this strength produce recall cues. Mirrors
# the ``GRIEF_THRESHOLD`` in ``death_ripple`` so the same set of close
# survivors also receives memory cues.
RECALL_STRENGTH_THRESHOLD: float = 0.4

# How wide a window each anchor covers. Anchor +10 with width 1 means
# a cue fires in chapter ``death+10`` exactly. We allow a 1-chapter
# window so a single missed chapter (skipped, written out of order,
# or rewritten) doesn't lose the cue.
_ANCHOR_WINDOW: int = 1


@dataclass(frozen=True)
class MemoryRecallCue:
    """One per-chapter directive: ``survivor`` should think of
    ``deceased`` in this chapter."""

    survivor_name: str
    deceased_name: str
    deceased_role: str | None
    relationship_type: str
    relationship_strength: float
    chapters_since_death: int
    intensity: str  # "acute" | "fresh" | "settled" | "anniversary"


def _intensity_for_offset(offset: int) -> str:
    """Map the chapter offset to a qualitative intensity tier."""
    if offset <= 3:
        return "acute"
    if offset <= 10:
        return "fresh"
    if offset <= 30:
        return "settled"
    return "anniversary"


def _on_anchor(offset: int) -> bool:
    """Return True when an offset is within the window of any anchor."""
    return any(abs(offset - a) <= _ANCHOR_WINDOW for a in _RECALL_ANCHORS)


async def compute_memory_recall_cues(
    session: AsyncSession,
    project_id: UUID,
    *,
    chapter_number: int,
    max_cues: int = 4,
) -> list[MemoryRecallCue]:
    """Compute the memory-recall cues active for ``chapter_number``.

    Walks every deceased character in the project, finds their close
    surviving relationships, and emits a cue when the chapter offset
    from the death lands on one of the anchor windows.

    The returned list is capped at ``max_cues`` to prevent prompt
    bloat — when more cues would fire, the closest-to-anchor ones
    win. Most chapters produce zero or one cue.
    """

    # 1. All deceased characters with a death chapter strictly less
    #    than the current chapter — they died at some point in the
    #    past and may be remembered now.
    deceased_rows = list(
        await session.scalars(
            select(CharacterModel).where(
                CharacterModel.project_id == project_id,
                CharacterModel.alive_status == "deceased",
                CharacterModel.death_chapter_number.is_not(None),
                CharacterModel.death_chapter_number < chapter_number,
            )
        )
    )
    if not deceased_rows:
        return []

    cues: list[tuple[int, MemoryRecallCue]] = []

    for deceased in deceased_rows:
        death_ch = getattr(deceased, "death_chapter_number", None)
        if death_ch is None:
            continue
        offset = int(chapter_number) - int(death_ch)
        if offset <= 0 or not _on_anchor(offset):
            continue

        # 2. For each deceased that lands on an anchor window, list
        #    their high-strength relationships.
        rels = list(
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
        for rel in rels:
            try:
                strength = float(rel.strength) if rel.strength is not None else 0.0
            except (TypeError, ValueError):
                strength = 0.0
            if strength < RECALL_STRENGTH_THRESHOLD:
                continue

            survivor_id = (
                rel.character_b_id if rel.character_a_id == deceased.id
                else rel.character_a_id
            )
            survivor = await session.get(CharacterModel, survivor_id)
            if survivor is None or not survivor.name:
                continue
            # Skip if the survivor is themselves dead by this chapter.
            survivor_death = getattr(survivor, "death_chapter_number", None)
            if survivor_death is not None and survivor_death < chapter_number:
                continue

            # Distance to nearest anchor — used for ranking when
            # we have to drop excess cues.
            anchor_distance = min(
                abs(offset - a) for a in _RECALL_ANCHORS
            )
            cues.append((
                anchor_distance,
                MemoryRecallCue(
                    survivor_name=survivor.name,
                    deceased_name=deceased.name,
                    deceased_role=deceased.role,
                    relationship_type=rel.relationship_type,
                    relationship_strength=strength,
                    chapters_since_death=offset,
                    intensity=_intensity_for_offset(offset),
                ),
            ))

    # Closest-to-anchor first; ties broken by stronger relationship.
    cues.sort(key=lambda x: (x[0], -x[1].relationship_strength))
    return [cue for _, cue in cues[:max(0, max_cues)]]


def render_memory_recall_block(
    cues: list[MemoryRecallCue],
    *,
    language: str = "zh-CN",
) -> str:
    """Pretty-print the cue list as a prompt block.

    The block is framed as a soft suggestion — the writer is asked to
    weave a brief memory beat in if narratively natural. Hard scolding
    would push the writer to insert clumsy "随后他想起 X" passages on
    every chapter, which is a different OOC failure than forgetting.
    """

    if not cues:
        return ""

    is_en = language.lower().startswith("en")
    lines: list[str] = []
    if is_en:
        lines.append(
            "[Memory recall cues — weave at most one or two of these "
            "in as a brief memory beat (a half-line of remembrance, an "
            "object that prompts the thought, a silence that asks for "
            "their voice). Do NOT force a memory beat where it would "
            "stall pacing — these are suggestions tied to the natural "
            "rhythm of grief, not contracts]:"
        )
        for c in cues:
            lines.append(
                f"- {c.survivor_name} could think of {c.deceased_name} "
                f"({c.deceased_role or 'departed'}, {c.chapters_since_death} "
                f"chapters since death, intensity={c.intensity}, "
                f"relation={c.relationship_type})"
            )
    else:
        lines.append(
            "【死后记忆提示 — 本章可自然地插入一两笔回忆（半句缅怀、"
            "一件触景生情的旧物、一段对沉默中缺席之人的留白），"
            "不必每条都写、也不必整段独立场景；如不顺，宁可省去也不要硬凑】："
        )
        intensity_zh = {
            "acute": "新丧之痛",
            "fresh": "余痛未消",
            "settled": "渐成静悼",
            "anniversary": "周年遥念",
        }
        for c in cues:
            lines.append(
                f"- {c.survivor_name} 可念及 {c.deceased_name}"
                f"（{c.deceased_role or '已故'}，距死亡已 "
                f"{c.chapters_since_death} 章，强度="
                f"{intensity_zh.get(c.intensity, c.intensity)}，"
                f"关系={c.relationship_type}）"
            )

    return "\n".join(lines)
