"""Interpersonal promise ledger — service for tracking promises /
oaths / debts between characters across the lifetime of a novel.

The ledger answers questions like:

* Which active promises does ``X`` carry (as promisor / promisee) and
  what is their next due chapter?
* When ``X`` dies, which promises must transition to ``inherited``
  (their successor takes the burden) or ``lapsed`` (the obligation
  joins the sea of unfinished business)?
* Which active promises are due in the chapter being written and
  should surface in the prompt as urgency?

This module is the read/write surface; integration points are:

* ``death_ripple`` calls :func:`mark_promises_on_death` after a death
  to roll up ``inherited`` / ``lapsed`` transitions in one step.
* ``story_bible.load_scene_story_bible_context`` calls
  :func:`active_promises_for_chapter` to embed the ledger snapshot
  in the chapter prompt.
* The planner can call :func:`record_promise` to persist a new
  promise as scenes establish them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    CharacterModel,
    InterpersonalPromiseModel,
)

logger = logging.getLogger(__name__)


# Status sentinels — kept in one place so callers don't sprinkle
# magic strings.
PROMISE_STATUS_ACTIVE: str = "active"
PROMISE_STATUS_FULFILLED: str = "fulfilled"
PROMISE_STATUS_BROKEN: str = "broken"
PROMISE_STATUS_INHERITED: str = "inherited"
PROMISE_STATUS_LAPSED: str = "lapsed"
PROMISE_STATUS_CANCELLED: str = "cancelled"


# Optional ``kind`` strings for prompt labelling. Free-form values are
# allowed; this list just provides canonical defaults.
PROMISE_KIND_REVENGE: str = "revenge"
PROMISE_KIND_PROTECTION: str = "protection"
PROMISE_KIND_MESSAGE: str = "message"      # "tell X what happened"
PROMISE_KIND_FEALTY: str = "fealty"
PROMISE_KIND_DEBT: str = "debt"
PROMISE_KIND_QUEST: str = "quest"
PROMISE_KIND_DEATHBED: str = "deathbed"


# How long after a missed due_chapter the prompt block should keep
# surfacing the promise as overdue. After this window we still keep
# it in the ledger (history!) but stop highlighting it.
_OVERDUE_LOOKBACK_CHAPTERS: int = 30


@dataclass(frozen=True)
class PromiseSnapshot:
    """Render-ready view of a promise — used by the prompt assembler."""

    id: UUID
    promisor_label: str
    promisee_label: str
    content: str
    kind: str | None
    made_chapter_number: int | None
    due_chapter_number: int | None
    status: str
    inherited_by_label: str | None
    chapters_until_due: int | None  # negative = overdue
    is_overdue: bool


def _to_snapshot(
    row: InterpersonalPromiseModel,
    *,
    chapter_number: int,
) -> PromiseSnapshot:
    chapters_until_due: int | None = None
    is_overdue = False
    if row.due_chapter_number is not None:
        chapters_until_due = int(row.due_chapter_number) - int(chapter_number)
        is_overdue = chapters_until_due < 0
    return PromiseSnapshot(
        id=row.id,
        promisor_label=row.promisor_label,
        promisee_label=row.promisee_label,
        content=row.content,
        kind=row.kind,
        made_chapter_number=row.made_chapter_number,
        due_chapter_number=row.due_chapter_number,
        status=row.status,
        inherited_by_label=row.inherited_by_label,
        chapters_until_due=chapters_until_due,
        is_overdue=is_overdue,
    )


async def record_promise(
    session: AsyncSession,
    *,
    project_id: UUID,
    promisor: CharacterModel | None,
    promisee: CharacterModel | None,
    promisor_label: str,
    promisee_label: str,
    content: str,
    kind: str | None = None,
    made_chapter_number: int | None = None,
    due_chapter_number: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> InterpersonalPromiseModel:
    """Persist a new active promise. Returns the freshly-created row.

    ``promisor`` and ``promisee`` may be ``None`` — the labels are
    always required so the row stays human-readable even if the
    character rows haven't been upserted yet (early-planning case).
    """

    row = InterpersonalPromiseModel(
        project_id=project_id,
        promisor_id=promisor.id if promisor is not None else None,
        promisee_id=promisee.id if promisee is not None else None,
        promisor_label=promisor_label,
        promisee_label=promisee_label,
        content=content,
        kind=kind,
        made_chapter_number=made_chapter_number,
        due_chapter_number=due_chapter_number,
        status=PROMISE_STATUS_ACTIVE,
        metadata_json=dict(metadata or {}),
    )
    session.add(row)
    await session.flush()
    return row


async def list_promises_for_character(
    session: AsyncSession,
    *,
    project_id: UUID,
    character_id: UUID,
    statuses: tuple[str, ...] = (PROMISE_STATUS_ACTIVE, PROMISE_STATUS_INHERITED),
) -> list[InterpersonalPromiseModel]:
    """All promises in which ``character_id`` is promisor, promisee, or
    inherited bearer — with ``status`` filtered to the supplied tuple.
    """

    stmt = select(InterpersonalPromiseModel).where(
        InterpersonalPromiseModel.project_id == project_id,
        (
            (InterpersonalPromiseModel.promisor_id == character_id)
            | (InterpersonalPromiseModel.promisee_id == character_id)
            | (InterpersonalPromiseModel.inherited_by_id == character_id)
        ),
        InterpersonalPromiseModel.status.in_(statuses),
    )
    return list(await session.scalars(stmt))


async def active_promises_for_chapter(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_number: int,
    limit: int = 12,
) -> list[PromiseSnapshot]:
    """Active or recently-overdue promises to surface in the chapter prompt.

    Selection rules:

    * ``status in (active, inherited)`` — these still need eventual
      resolution.
    * Overdue rows are kept up to ``_OVERDUE_LOOKBACK_CHAPTERS`` past
      their due chapter so the writer is reminded they are slipping.
    * Sorted by urgency: overdue first (most overdue first), then
      due-soon, then ``due_chapter`` ``None`` (open-ended).
    * Capped at ``limit`` to keep the prompt trim.
    """

    rows = list(
        await session.scalars(
            select(InterpersonalPromiseModel).where(
                InterpersonalPromiseModel.project_id == project_id,
                InterpersonalPromiseModel.status.in_((
                    PROMISE_STATUS_ACTIVE,
                    PROMISE_STATUS_INHERITED,
                )),
            )
        )
    )
    snapshots = [_to_snapshot(row, chapter_number=chapter_number) for row in rows]

    # Filter out long-overdue ones the prompt no longer should chase.
    fresh: list[PromiseSnapshot] = []
    for snap in snapshots:
        if snap.is_overdue and snap.chapters_until_due is not None:
            if abs(snap.chapters_until_due) > _OVERDUE_LOOKBACK_CHAPTERS:
                continue
        fresh.append(snap)

    def _sort_key(s: PromiseSnapshot) -> tuple[int, int]:
        # Overdue rows first; among them, more-overdue first (most
        # urgent). Then due-soon (smallest positive remaining). Then
        # open-ended (no due chapter) at the back.
        if s.is_overdue:
            return (0, s.chapters_until_due or 0)
        if s.chapters_until_due is None:
            return (2, 0)
        return (1, s.chapters_until_due)

    fresh.sort(key=_sort_key)
    return fresh[: max(0, limit)]


async def mark_promises_on_death(
    session: AsyncSession,
    *,
    project_id: UUID,
    deceased: CharacterModel,
    chapter_number: int,
    inheritor: CharacterModel | None = None,
) -> dict[str, int]:
    """Roll up a death's effect on outstanding promises.

    Behaviour:

    * Promises where the deceased is the **promisor** transition to
      ``inherited`` if ``inheritor`` is provided (and ``inherited_by``
      is filled); otherwise to ``lapsed``.
    * Promises where the deceased is the **promisee** transition to
      ``inherited`` if the promise's ``metadata_json.passes_to`` is
      set or if an explicit ``inheritor`` is passed; otherwise to
      ``lapsed`` (the recipient is gone — the obligation has nowhere
      to land).
    * Already-resolved statuses (fulfilled / broken / inherited /
      lapsed / cancelled) are left untouched.

    Returns a small report ``{"inherited": N, "lapsed": M}``. Side-
    effect-only otherwise; the caller commits.
    """

    rows = list(
        await session.scalars(
            select(InterpersonalPromiseModel).where(
                InterpersonalPromiseModel.project_id == project_id,
                (
                    (InterpersonalPromiseModel.promisor_id == deceased.id)
                    | (InterpersonalPromiseModel.promisee_id == deceased.id)
                ),
                InterpersonalPromiseModel.status.in_((
                    PROMISE_STATUS_ACTIVE,
                    PROMISE_STATUS_INHERITED,
                )),
            )
        )
    )

    inherited_count = 0
    lapsed_count = 0
    for row in rows:
        # Resolve who, if anyone, can carry this forward. An explicit
        # ``inheritor`` argument wins; otherwise consult the row's
        # ``passes_to`` hint stored at planning time.
        carrier: CharacterModel | None = inheritor
        if carrier is None:
            passes_to_label = (row.metadata_json or {}).get("passes_to_label")
            if passes_to_label:
                carrier = await session.scalar(
                    select(CharacterModel).where(
                        CharacterModel.project_id == project_id,
                        CharacterModel.name == passes_to_label,
                    )
                )

        update_values: dict[str, Any] = {
            "metadata_json": {
                **(row.metadata_json or {}),
                "death_event_chapter_number": chapter_number,
                "deceased_label_at_event": deceased.name,
            }
        }

        if carrier is not None and carrier.id != deceased.id:
            update_values["status"] = PROMISE_STATUS_INHERITED
            update_values["inherited_by_id"] = carrier.id
            update_values["inherited_by_label"] = carrier.name
            inherited_count += 1
        else:
            update_values["status"] = PROMISE_STATUS_LAPSED
            update_values["resolved_chapter_number"] = chapter_number
            update_values["resolution_summary"] = (
                f"{deceased.name} 在第{chapter_number}章死亡，"
                "无指定承担人 — 承诺自动 lapse。"
            )
            lapsed_count += 1

        await session.execute(
            update(InterpersonalPromiseModel)
            .where(InterpersonalPromiseModel.id == row.id)
            .values(**update_values)
        )

    if rows:
        await session.flush()
        logger.info(
            "interpersonal_promises: %s death (ch%d) → inherited=%d lapsed=%d",
            deceased.name, chapter_number, inherited_count, lapsed_count,
        )

    return {"inherited": inherited_count, "lapsed": lapsed_count}


def render_promises_block(
    snapshots: list[PromiseSnapshot],
    *,
    language: str = "zh-CN",
) -> str:
    """Pretty-print the active-promise snapshot list as a prompt block.

    Block framing — these are NOT hard "must resolve this chapter"
    constraints, they are persistent obligations the writer should be
    aware of so the prose acknowledges their weight (a glance, a
    flinch, a half-resolved line) even when the chapter itself does
    not advance them. Overdue rows are highlighted to nudge resolution.
    """

    if not snapshots:
        return ""

    is_en = language.lower().startswith("en")
    lines: list[str] = []
    if is_en:
        lines.append(
            "[Open interpersonal promises — promises / oaths / debts "
            "between characters that are still binding. The chapter "
            "does NOT have to resolve them, but a character carrying "
            "an open vow should let the weight show in their action / "
            "thought / silence. Overdue rows are slipping — close one "
            "or let the cost surface.]:"
        )
    else:
        lines.append(
            "【未了的人际承诺 — 角色之间尚在束缚的诺言、誓言或债。"
            "本章不必逐项兑现，但身负承诺者应让其重量在动作/念头/沉默"
            "之间渗出；下方标注为"
            "「逾期」的承诺已滑过截止章节，本章宜推动其了结，"
            "否则就让代价浮现。】："
        )

    for snap in snapshots:
        kind = f" [{snap.kind}]" if snap.kind else ""
        if snap.is_overdue:
            urgency = (
                f" ⚠️ overdue by {abs(snap.chapters_until_due or 0)} chapters"
                if is_en
                else f" ⚠️ 已逾期 {abs(snap.chapters_until_due or 0)} 章"
            )
        elif snap.chapters_until_due is not None and snap.chapters_until_due <= 5:
            urgency = (
                f" 🔥 due in {snap.chapters_until_due} chapters"
                if is_en
                else f" 🔥 距截止仅余 {snap.chapters_until_due} 章"
            )
        else:
            urgency = ""

        carrier_clause = ""
        if snap.status == PROMISE_STATUS_INHERITED and snap.inherited_by_label:
            carrier_clause = (
                f" (now carried by {snap.inherited_by_label})"
                if is_en
                else f"（现由 {snap.inherited_by_label} 承担）"
            )

        if is_en:
            lines.append(
                f"- {snap.promisor_label} → {snap.promisee_label}: "
                f"{snap.content}{kind}{carrier_clause}{urgency}"
            )
        else:
            lines.append(
                f"- {snap.promisor_label} 对 {snap.promisee_label} 承诺："
                f"{snap.content}{kind}{carrier_clause}{urgency}"
            )

    return "\n".join(lines)
