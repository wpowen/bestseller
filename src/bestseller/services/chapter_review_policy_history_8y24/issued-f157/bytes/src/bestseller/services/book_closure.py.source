"""Close delivery only after exact adopted knowledge and verified export bytes.

The synchronous evaluator is a progress/census check, never adoption authority.
Call ``settle_project_status_on_closure`` for delivery, or the read-only readiness
reader for a dry run. No function in this module promotes a draft.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Final
from uuid import UUID

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from bestseller.infra.db.models import ExportArtifactModel, ProjectModel
    from bestseller.services.book_delivery_readiness import BookDeliveryReadiness
    from bestseller.services.pipelines import FinalQualityGateResult
    from bestseller.settings import AppSettings

SETTLED_PRODUCTION_STATES: Final = frozenset(
    {
        "ok",
        "quality_debt",
        "repair_exhausted",
        "quality_reviewed",
        "needs_human_review",
    }
)
_DEBT_STATES = SETTLED_PRODUCTION_STATES - {"ok"}
DELIVERY_SCHEMA = "book_delivery.v2"


@dataclass(frozen=True)
class BookClosureVerdict:
    is_complete: bool
    reason: str
    settled_chapters: int
    expected_chapters: int
    debt_chapters: tuple[int, ...]
    unsettled_chapters: tuple[int, ...]
    body_complete: bool = False

    @property
    def is_clean(self) -> bool:
        return self.is_complete and not self.debt_chapters


def _chapter_number(chapter: object) -> int:
    try:
        return int(getattr(chapter, "chapter_number", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _production_state(chapter: object) -> str:
    return str(getattr(chapter, "production_state", "") or "").strip().lower()


def evaluate_book_closure(
    chapters: Iterable[object],
    *,
    expected_chapters: int | None = None,
) -> BookClosureVerdict:
    """Check the complete planned census without granting delivery authority.

    State-only legacy callers now fail closed. A positive target is necessary:
    inferring a plan from existing rows could call a partially planned book done.
    Extra rows cannot be ignored because the project exporter includes them.
    """
    rows = [row for row in chapters if row is not None]
    counts = Counter(_chapter_number(row) for row in rows)
    try:
        planned = max(0, int(expected_chapters or 0))
    except (TypeError, ValueError):
        planned = 0
    unsettled = set(range(1, planned + 1)) - set(counts)
    debt, settled = [], 0
    for row in rows:
        number = _chapter_number(row)
        if (
            counts[number] != 1
            or not 1 <= number <= planned
            or _production_state(row) not in SETTLED_PRODUCTION_STATES
            or str(getattr(row, "status", "")).strip().lower() not in {"complete", "revision"}
        ):
            unsettled.add(number)
        else:
            settled += 1
            if _production_state(row) in _DEBT_STATES:
                debt.append(number)
    if not rows:
        reason = "no_chapters"
    elif not planned:
        reason = "chapter_target_missing"
    elif any(count > 1 for count in counts.values()):
        reason = "duplicate_chapters"
    elif any(not 1 <= number <= planned for number in counts):
        reason = "unexpected_chapters"
    elif unsettled:
        reason = "chapters_unsettled"
    else:
        reason = "adoption_knowledge_required"
    return BookClosureVerdict(
        False, reason, settled, planned, tuple(sorted(debt)), tuple(sorted(unsettled))
    )


async def _promote_settled_chapter_drafts(session: AsyncSession, project: ProjectModel) -> None:
    """Compatibility tombstone: closure can no longer confer adoption authority."""
    raise ValueError("book_closure_promotion_removed: use normal reviewed adoption")


def _closure_quality_gate(
    debt_chapters: tuple[int, ...], conceded: list[str]
) -> Callable[..., FinalQualityGateResult]:
    """Compatibility adapter only; no failure, including evaluator errors, is waived.

    Closure uses exports' default terminal gate, which supplies chapter metadata.
    Numeric adoption debt belongs to the adoption receipt and is not permission
    to bypass an unrelated terminal failure or mutate the adopted text.
    """

    def gate(**kwargs: object) -> FinalQualityGateResult:
        from bestseller.services.pipelines import run_final_quality_gates

        return run_final_quality_gates(**kwargs)

    return gate


async def settle_outstanding_rewrite_tasks(
    session: AsyncSession,
    *,
    project_id: UUID,
    reason: str,
) -> int:
    """Legacy explicit task-settlement API; call only after verified delivery.

    The closure path invokes this inside the successful export savepoint. Failed
    delivery never cancels pending/queued work or changes historical task rows.
    """
    from sqlalchemy import select

    from bestseller.infra.db.models import RewriteTaskModel

    rows = list(
        (
            await session.execute(
                select(RewriteTaskModel).where(
                    RewriteTaskModel.project_id == project_id,
                    RewriteTaskModel.status.in_(("pending", "queued")),
                )
            )
        )
        .scalars()
        .all()
    )
    settled = 0
    for row in rows:
        if row.status not in {"pending", "queued"}:
            continue
        row.status = "cancelled"
        row.metadata_json = {**(row.metadata_json or {}), "cancelled_reason": reason}
        settled += 1
    if settled:
        await session.flush()
    return settled


def _supersede_completion(metadata: dict, *, old_status: str, now_iso: str, reason: str) -> dict:
    """Preserve old completion claims before replacing the current materialization.

    Generic lazy migration, replayable for every project: dry-run with the
    readiness reader first, then call settlement. Never deletes drafts, audits,
    knowledge, export artifacts, or the original completion receipt.
    """
    metadata = deepcopy(metadata)
    keys = [
        key
        for key in metadata
        if key == "completed_at" or (key.startswith("completion_") and key != "completion_history")
    ]
    if keys or old_status == "completed":
        previous = {key: metadata.pop(key) for key in keys}
        previous.update(project_status=old_status, book_delivery=metadata.get("book_delivery"))
        history = list(metadata.get("completion_history") or [])
        history.append(
            {
                "schema": DELIVERY_SCHEMA,
                "superseded_at": now_iso,
                "reason": reason,
                "previous": previous,
            }
        )
        metadata["completion_history"] = history
    return metadata


def _verify_export(
    project: ProjectModel,
    artifact: ExportArtifactModel,
    path: str | Path,
    readiness: BookDeliveryReadiness,
) -> str:
    """Reopen both immutable artifact and returned UTF-8 mirror, binding all sources."""
    from bestseller.services.exports import build_project_markdown

    if (
        not artifact.id
        or artifact.project_id != project.id
        or artifact.source_id != project.id
        or artifact.source_scope != "project"
        or artifact.export_type != "markdown"
    ):
        raise ValueError("completion_export_artifact_identity_mismatch")
    metadata = artifact.metadata_json or {}
    if metadata.get("source_drafts") != list(readiness.sources):
        raise ValueError("completion_export_source_mismatch")
    expected = build_project_markdown(project, list(readiness.payloads)).encode("utf-8")
    for filename in (Path(artifact.storage_uri), Path(path)):
        data = filename.read_bytes()
        if not data or data.decode("utf-8").encode("utf-8") != expected:
            raise ValueError("completion_export_content_mismatch")
        if hashlib.sha256(data).hexdigest() != artifact.checksum:
            raise ValueError("completion_export_checksum_mismatch")
    return str(artifact.checksum)


async def settle_project_status_on_closure(
    session: AsyncSession,
    project: ProjectModel,
    *,
    settings: AppSettings,
    fallback_status: str,
    now_iso: str,
) -> BookClosureVerdict:
    """Verify delivery atomically in the caller's transaction; retry failed exports.

    ``is_complete`` is true only for verified delivery. ``body_complete`` records
    adopted knowledge readiness separately, so an I/O failure does not require
    rewriting/rejudging already adopted prose. No commit or model call is made.
    """
    from bestseller.services.book_delivery_readiness import (
        read_book_delivery_closure,
        read_book_delivery_readiness,
    )
    from bestseller.services.exports import export_project_markdown

    await session.flush()
    await session.refresh(project, with_for_update=True)
    old_status = project.status
    previous_metadata = deepcopy(project.metadata_json or {})
    verdict = evaluate_book_closure([], expected_chapters=project.target_chapters)
    blockers: list[dict] = []
    sources: list[dict] = []
    try:
        async with session.begin_nested():
            if old_status == "completed":
                verified = await read_book_delivery_closure(session, project, lock=True)
                if verified.verdict.is_complete:
                    return verified.verdict
            readiness = await read_book_delivery_readiness(session, project, lock=True)
            verdict = readiness.verdict
            blockers = list(readiness.blockers)
            sources = list(readiness.sources)
            if verdict.body_complete:
                if project.project_type == "fanqie_short":
                    # Platform completion binds its own package and whole-source
                    # receipt. A generic project.md must never substitute for it.
                    raise ValueError("fanqie_delivery_requires_platform_export")
                # The default exporter already checks structure, terminal quality,
                # source identity, and frozen bytes. Do not wrap away its failures.
                artifact, path = await export_project_markdown(session, settings, project.slug)
                await session.flush()
                await session.refresh(artifact)
                current = await read_book_delivery_readiness(session, project, lock=True)
                if not current.verdict.body_complete or current.sources != readiness.sources:
                    raise ValueError("completion_source_changed_during_export")
                checksum = _verify_export(project, artifact, path, current)
                settled_tasks = await settle_outstanding_rewrite_tasks(
                    session,
                    project_id=project.id,
                    reason="book_delivery_verified",
                )
                verdict = replace(
                    current.verdict,
                    is_complete=True,
                    reason="all_chapters_delivered_with_debt"
                    if current.verdict.debt_chapters
                    else "all_chapters_delivered",
                )
                export_receipt = {
                    "completion_export_artifact_id": str(artifact.id),
                    "completion_export_path": str(path),
                    "completion_export_storage_uri": str(artifact.storage_uri),
                    "completion_export_checksum": checksum,
                    "completion_settled_rewrite_tasks": settled_tasks,
                }
    except Exception as exc:
        # A savepoint restores task/artifact writes after an I/O or DB failure;
        # caller-owned work outside it remains intact. CancelledError propagates.
        await session.refresh(project)
        verdict = replace(
            verdict,
            is_complete=False,
            reason="export_pending" if verdict.body_complete else "readiness_error",
        )
        blockers.append(
            {"reason": verdict.reason, "error_type": type(exc).__name__, "detail": str(exc)[:500]}
        )

    metadata = _supersede_completion(
        previous_metadata, old_status=old_status, now_iso=now_iso, reason=verdict.reason
    )
    metadata["book_delivery"] = {
        "schema": DELIVERY_SCHEMA,
        "checked_at": now_iso,
        "state": "completed"
        if verdict.is_complete
        else "export_pending"
        if verdict.body_complete
        else "readiness_blocked",
        "reason": verdict.reason,
        "body_complete": verdict.body_complete,
        "sources": sources,
        "debt_chapters": list(verdict.debt_chapters),
        "blockers": blockers,
    }
    if verdict.is_complete:
        metadata.update(
            export_receipt,
            completed_at=now_iso,
            completion_reason=verdict.reason,
            completion_debt_chapters=list(verdict.debt_chapters),
            completion_is_clean=verdict.is_clean,
        )
        project.status = "completed"
    else:
        # A caller retrying a legacy completed book may pass that status back.
        project.status = fallback_status if fallback_status != "completed" else "revising"
        if verdict.body_complete:
            metadata["completion_export_error"] = blockers[-1]["detail"]
    project.metadata_json = metadata
    await session.flush()
    return verdict


__all__ = [
    "SETTLED_PRODUCTION_STATES",
    "BookClosureVerdict",
    "evaluate_book_closure",
    "settle_outstanding_rewrite_tasks",
    "settle_project_status_on_closure",
]
