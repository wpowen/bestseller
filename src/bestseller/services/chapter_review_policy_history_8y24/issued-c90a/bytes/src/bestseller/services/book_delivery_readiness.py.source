"""Read-only, exact-source book readiness and legacy closure-promotion inventory.

Use ``read_book_delivery_readiness(..., lock=False)`` for dry-run inspection;
``blockers`` includes exact audit IDs and sources requiring normal re-adoption.
It never changes shared selectors, promotes, extracts knowledge, or exports.
Known closure-created adoptions remain in history and require a new legitimate
adopted version; a status/metadata rewrite cannot cleanse their audit trail.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    DraftPromotionDecisionModel,
    ProjectModel,
)
from bestseller.services.adopted_chapter_source import select_adopted_chapter_source
from bestseller.services.adopted_knowledge_resume import read_adopted_knowledge
from bestseller.services.book_closure import BookClosureVerdict, evaluate_book_closure
from bestseller.services.reviews import chapter_review_body_fingerprint


@dataclass(frozen=True)
class BookDeliveryReadiness:
    verdict: BookClosureVerdict
    sources: tuple[dict, ...] = ()
    blockers: tuple[dict, ...] = ()
    payloads: tuple[tuple[ChapterModel, ChapterDraftVersionModel], ...] = ()


async def closure_promotion_evidence(
    session: AsyncSession, draft: ChapterDraftVersionModel
) -> dict | None:
    """Identify known unsafe origin from persisted metadata/codes AND audit rows.

    Absence is not invented proof of a review; normal source/knowledge and export
    contracts still apply. Any DB error propagates, never a clean dry-run result.
    """
    metadata = draft.promotion_metadata or {}
    markers = {
        key: deepcopy(metadata[key])
        for key in ("decision_source", "source")
        if metadata.get(key) == "book_closure"
    }
    if metadata.get("closure_promotion"):
        markers["closure_promotion"] = deepcopy(metadata["closure_promotion"])
    if "settled_at_closure" in (draft.promotion_reason_codes or []):
        markers["reason_code"] = "settled_at_closure"
    decisions = list(
        await session.scalars(
            select(DraftPromotionDecisionModel)
            .where(
                DraftPromotionDecisionModel.project_id == draft.project_id,
                DraftPromotionDecisionModel.chapter_draft_version_id == draft.id,
                DraftPromotionDecisionModel.decision_source == "book_closure",
            )
            .execution_options(populate_existing=True, autoflush=False)
        )
    )
    if not markers and not decisions:
        return None
    return {
        "reason": "legacy_closure_promotion_requires_readoption",
        "draft_id": str(draft.id),
        "promotion_markers": markers,
        "promotion_decision_ids": [str(row.id) for row in decisions],
    }


async def read_book_delivery_readiness(
    session: AsyncSession,
    project: ProjectModel,
    *,
    lock: bool = False,
) -> BookDeliveryReadiness:
    """Check census, adopted source, old closure origin and exact closed knowledge.

    Caller flushes before a locking check. Read-only callers never flush pending
    work as a side effect; no completion fields or historical receipts are edited.
    """
    # Existing source readers deliberately populate_existing. Give them their own
    # identity map on the caller's connection so that unflushed caller objects
    # cannot be overwritten. rollback_only close does not end the outer transaction.
    async with AsyncSession(
        bind=await session.connection(),
        join_transaction_mode="rollback_only",
        autoflush=False,
        expire_on_commit=False,
    ) as reader:
        return await _read_book_delivery_readiness(reader, project, lock=lock)


async def _read_book_delivery_readiness(
    session: AsyncSession,
    project: ProjectModel,
    *,
    lock: bool,
) -> BookDeliveryReadiness:
    with session.no_autoflush:
        query = (
            select(ChapterModel)
            .where(ChapterModel.project_id == project.id)
            .order_by(ChapterModel.chapter_number)
            .execution_options(populate_existing=True, autoflush=False)
        )
        chapters = list(await session.scalars(query.with_for_update() if lock else query))
        verdict = evaluate_book_closure(chapters, expected_chapters=project.target_chapters)
        if verdict.reason != "adoption_knowledge_required":
            return BookDeliveryReadiness(
                verdict,
                blockers=(
                    {"reason": verdict.reason, "chapter_numbers": list(verdict.unsettled_chapters)},
                ),
            )
        blockers, sources, payloads = [], [], []
        debt = set(verdict.debt_chapters)
        for chapter in chapters:
            source, _adoption = await select_adopted_chapter_source(
                session, chapter=chapter, lock=lock
            )
            blocker = {"chapter_id": str(chapter.id), "chapter_number": chapter.chapter_number}
            if source is None:
                blockers.append({**blocker, "reason": "adopted_source_missing"})
                continue
            origin = await closure_promotion_evidence(session, source)
            if origin:
                blockers.append({**blocker, **origin})
                continue
            lineage, knowledge = await read_adopted_knowledge(
                session,
                project_id=project.id,
                chapter_id=chapter.id,
                source_chapter_draft_version_id=source.id,
                chapter_md=source.content_md,
                require_bible=True,
            )
            if not knowledge.get("closed"):
                blockers.append(
                    {
                        **blocker,
                        "reason": "adopted_knowledge_incomplete",
                        "source_lineage": deepcopy(lineage),
                        "knowledge": knowledge,
                    }
                )
                continue
            item = {
                "chapter_id": str(chapter.id),
                "chapter_number": chapter.chapter_number,
                "draft_id": str(source.id),
                "version_no": source.version_no,
                "promotion_state": source.promotion_state,
                "body_fingerprint": chapter_review_body_fingerprint(source.content_md),
                "body_sha256": hashlib.sha256(source.content_md.encode("utf-8")).hexdigest(),
                "adoption_state": lineage["adoption_state"],
            }
            if lineage["adoption_state"] == "accepted_with_debt":
                item["adoption_receipt"] = deepcopy(lineage["adoption_receipt"])
                debt.add(chapter.chapter_number)
            sources.append(item)
            payloads.append((chapter, source))
        verdict = replace(
            verdict,
            body_complete=not blockers,
            reason="adopted_knowledge_incomplete" if blockers else "delivery_pending",
            debt_chapters=tuple(sorted(debt)),
            unsettled_chapters=tuple(sorted({item["chapter_number"] for item in blockers})),
        )
        return BookDeliveryReadiness(verdict, tuple(sources), tuple(blockers), tuple(payloads))


async def read_book_delivery_closure(
    session: AsyncSession,
    project: ProjectModel,
    *,
    lock: bool = False,
) -> BookDeliveryReadiness:
    """Read-only delivery predicate for repair convergence and historical dry runs.

    A previous normal exporter return must have a v2 completion receipt. Legacy
    status-only completion stays unverified until settlement exports/readbacks it.
    All DB/filesystem exceptions remain visible as blockers, never fake success.
    """
    async with AsyncSession(
        bind=await session.connection(),
        join_transaction_mode="rollback_only",
        autoflush=False,
        expire_on_commit=False,
    ) as reader:
        return await _read_book_delivery_closure(reader, project, lock=lock)


async def _read_book_delivery_closure(
    session: AsyncSession,
    project: ProjectModel,
    *,
    lock: bool,
) -> BookDeliveryReadiness:
    from bestseller.infra.db.models import ExportArtifactModel
    from bestseller.services.book_closure import DELIVERY_SCHEMA, _verify_export

    result = BookDeliveryReadiness(
        evaluate_book_closure([], expected_chapters=project.target_chapters)
    )
    with session.no_autoflush:
        try:
            # Read the persisted target/certificate without refreshing over the
            # caller's unflushed ORM changes or trusting its stale identity map.
            stored = (
                await session.execute(
                    select(
                        ProjectModel.id,
                        ProjectModel.slug,
                        ProjectModel.title,
                        ProjectModel.genre,
                        ProjectModel.language,
                        ProjectModel.project_type,
                        ProjectModel.target_chapters,
                        ProjectModel.metadata_json,
                    )
                    .where(ProjectModel.id == project.id)
                    .execution_options(autoflush=False)
                )
            ).one_or_none()
            if stored is None:
                raise ValueError("completion_project_missing")
            project = ProjectModel(**dict(stored._mapping))
            result = await _read_book_delivery_readiness(session, project, lock=lock)
            if not result.verdict.body_complete:
                return result
            if project.project_type == "fanqie_short":
                from bestseller.services.fanqie_short_source_export import (
                    read_fanqie_delivery_evidence,
                )

                await read_fanqie_delivery_evidence(session, project.id, lock=lock)
                return replace(
                    result,
                    verdict=replace(
                        result.verdict,
                        is_complete=True,
                        reason="all_chapters_delivered_with_debt"
                        if result.verdict.debt_chapters
                        else "all_chapters_delivered",
                    ),
                )
            metadata = project.metadata_json or {}
            receipt = metadata.get("book_delivery") or {}
            if receipt.get("schema") != DELIVERY_SCHEMA or receipt.get("state") != "completed":
                raise ValueError("completion_delivery_receipt_missing")
            if receipt.get("sources") != list(result.sources):
                raise ValueError("completion_delivery_receipt_source_mismatch")
            artifact_id = UUID(str(metadata.get("completion_export_artifact_id")))
            artifact = await session.scalar(
                select(ExportArtifactModel)
                .where(
                    ExportArtifactModel.id == artifact_id,
                    ExportArtifactModel.project_id == project.id,
                )
                .execution_options(populate_existing=True, autoflush=False)
            )
            if artifact is None:
                raise ValueError("completion_export_artifact_missing")
            checksum = _verify_export(project, artifact, metadata["completion_export_path"], result)
            if checksum != metadata.get("completion_export_checksum"):
                raise ValueError("completion_delivery_receipt_checksum_mismatch")
            return replace(
                result,
                verdict=replace(
                    result.verdict,
                    is_complete=True,
                    reason="all_chapters_delivered_with_debt"
                    if result.verdict.debt_chapters
                    else "all_chapters_delivered",
                ),
            )
        except Exception as exc:
            return replace(
                result,
                verdict=replace(result.verdict, is_complete=False, reason="delivery_unverified"),
                blockers=(
                    *result.blockers,
                    {
                        "reason": "delivery_unverified",
                        "error_type": type(exc).__name__,
                        "detail": str(exc)[:500],
                    },
                ),
            )
