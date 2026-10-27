"""Read the adopted chapter source independently of the working draft pointer."""

from __future__ import annotations

from collections.abc import Iterable
import hashlib
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel


def continuity_source_key(receipt: dict[str, Any]) -> str | None:
    draft_id, digest = receipt.get("draft_id"), receipt.get("body_sha256")
    return f"{draft_id}:{digest}" if draft_id and digest else None


def continuity_admissions_by_source(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Migrate legacy receipts in memory; later checks supersede only their source."""
    sources: dict[str, dict[str, Any]] = {}
    history = metadata.get("continuity_admission_history", [])
    records = list(history) if isinstance(history, list) else []
    records.extend([metadata.get("continuity_adoption_block"), metadata.get("continuity_admission")])
    for receipt in records:
        if isinstance(receipt, dict) and (key := continuity_source_key(receipt)):
            sources[key] = receipt
    ledger = metadata.get("continuity_admission_by_source", {})
    if isinstance(ledger, dict):
        for key, receipt in ledger.items():
            if isinstance(receipt, dict) and continuity_source_key(receipt) == key:
                sources[key] = receipt
    return sources


def explicit_continuity_source_rejection(
    *, chapter: ChapterModel, draft: ChapterDraftVersionModel,
) -> dict[str, Any] | None:
    """Return exact owned negative evidence; unknown admission cannot revoke a source."""
    from copy import deepcopy

    if draft.project_id != chapter.project_id or draft.chapter_id != chapter.id:
        return None
    identity = {
        "draft_id": str(draft.id),
        "body_sha256": hashlib.sha256(
            (draft.content_md or "").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        ).hexdigest(),
    }
    source_key = f"{identity['draft_id']}:{identity['body_sha256']}"
    receipt = continuity_admissions_by_source(chapter.metadata_json or {}).get(source_key)
    if (
        not isinstance(receipt, dict)
        or receipt.get("passed") is not False
        or any(receipt.get(key) != value for key, value in identity.items())
    ):
        return None
    return {
        "project_id": str(chapter.project_id), "chapter_id": str(chapter.id),
        **identity, "source_key": source_key, "receipt": deepcopy(receipt),
    }


def _continuity_source_blocked(chapter: ChapterModel, draft: ChapterDraftVersionModel) -> bool:
    digest = hashlib.sha256(
        (draft.content_md or "").replace("\r\n", "\n").replace("\r", "\n").encode()
    ).hexdigest()
    receipt = continuity_admissions_by_source(chapter.metadata_json or {}).get(f"{draft.id}:{digest}")
    return bool(receipt is not None and receipt.get("passed") is not True)


def choose_promoted_chapter_source(
    drafts: Iterable[ChapterDraftVersionModel],
    *,
    project_id: UUID,
    chapter_id: UUID,
) -> ChapterDraftVersionModel | None:
    """Choose one owned promotion from current or unambiguous adoption dates."""
    promoted = [
        draft for draft in drafts
        if draft.project_id == project_id and draft.chapter_id == chapter_id
        and draft.promotion_state == "promoted"
    ]
    current = [draft for draft in promoted if draft.is_current]
    if len(current) == 1:
        return current[0]
    if len(current) > 1:
        return None
    if len(promoted) == 1:
        return promoted[0]
    if promoted and all(draft.promoted_at is not None for draft in promoted):
        # Dated re-adoption may select an older version while a new candidate
        # owns is_current. Equal/missing dates cannot establish a latest adoption.
        ordered = sorted(promoted, key=lambda draft: draft.promoted_at, reverse=True)
        if ordered[0].promoted_at != ordered[1].promoted_at:
            return ordered[0]
    return None


def choose_usable_promoted_chapter_source(
    *,
    chapter: ChapterModel,
    drafts: Iterable[ChapterDraftVersionModel],
) -> ChapterDraftVersionModel | None:
    """Apply source-bound continuity admission to the exact owned promotion."""
    adopted = choose_promoted_chapter_source(
        drafts, project_id=chapter.project_id, chapter_id=chapter.id,
    )
    return None if adopted is not None and _continuity_source_blocked(chapter, adopted) else adopted


async def select_adopted_chapter_source(
    session: AsyncSession,
    *,
    chapter: ChapterModel,
    lock: bool = True,
) -> tuple[ChapterDraftVersionModel | None, dict[str, Any] | None]:
    """Select one scoped adoption; promotion labels on historical rows are not enough.

    Caller first refreshes/locks the chapter. A verified numeric adoption takes
    precedence. Working candidates do not displace a formal promoted source.
    Ambiguous legacy promoted rows require current selection or dated adoption
    evidence, never a guess based on body text, version number or knowledge facts.
    """
    from bestseller.services.feedback import _validated_numeric_adoption

    base = (
        select(ChapterDraftVersionModel)
        .where(
            ChapterDraftVersionModel.project_id == chapter.project_id,
            ChapterDraftVersionModel.chapter_id == chapter.id,
        )
        .execution_options(populate_existing=True, autoflush=lock)
    )
    debt = (chapter.metadata_json or {}).get("numeric_promotion_debt")
    if isinstance(debt, dict):
        try:
            debt_id = UUID(str(debt.get("draft_id")))
        except (ValueError, TypeError):
            debt_id = None
        if debt_id is not None:
            query = base.where(ChapterDraftVersionModel.id == debt_id)
            adopted = await session.scalar(query.with_for_update() if lock else query)
            if adopted is not None:
                receipt = await _validated_numeric_adoption(session, chapter, adopted, lock=lock)
                if receipt is not None and not _continuity_source_blocked(chapter, adopted):
                    return adopted, receipt

    query = base.where(ChapterDraftVersionModel.promotion_state == "promoted")
    promoted = list(await session.scalars(query.with_for_update() if lock else query))
    return choose_usable_promoted_chapter_source(chapter=chapter, drafts=promoted), None
