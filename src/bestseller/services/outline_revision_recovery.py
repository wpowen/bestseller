"""Fail-closed recovery of an exhausted outline replan.

Applying a derived story revision and retrying an outline are two different
operations.  This module only performs the small, auditable bridge between
them: after an *applied* revision is read back, it clears the exhausted retry
marker so one bounded planner attempt can be made.  It deliberately leaves
the project paused, keeps ``needs_replan`` visible, and never queues work.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import datetime as _dt
from typing import Any
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ProjectStatus
from bestseller.infra.db.models import (
    BookProductionControlModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    ReviewReportModel,
    WorkflowRunModel,
)

__all__ = [
    "OutlineRecoveryError",
    "OutlineRecoveryResult",
    "rearm_outline_recovery_for_revision",
    "rearm_outline_recovery_metadata",
]


class OutlineRecoveryError(RuntimeError):
    """The revision is not a safe basis for an outline recovery attempt."""


@dataclass(frozen=True)
class OutlineRecoveryResult:
    project_slug: str
    revision_report_id: str
    source_revision: str
    changed_sources: tuple[str, ...]
    rearmed: bool
    reason: str


_ACTIVE_WORKFLOW_STATUSES = frozenset(
    {"pending", "queued", "running", "machine_blocked"}
)
_APPLIED_MARKER_STATUSES = frozenset({"applied", "committed"})


def _as_text(value: object) -> str:
    return str(value or "").strip()


def _revision_marker(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    """Read the canonical marker written by ``derived_revision_apply``."""

    marker = metadata.get("derived_input_revision")
    return marker if isinstance(marker, Mapping) else {}


def _marker_report_id(marker: Mapping[str, Any]) -> str:
    return _as_text(marker.get("report_id"))


def _marker_source_revision(marker: Mapping[str, Any]) -> str:
    return _as_text(marker.get("candidate_hash"))


def _marker_sources(marker: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        name
        for name, key in (
            ("book_spec", "book_artifact_id"),
            ("world_spec", "world_artifact_id"),
        )
        if _as_text(marker.get(key))
    )


def rearm_outline_recovery_metadata(
    metadata: Mapping[str, Any],
    *,
    revision_report_id: str,
    source_revision: str,
    changed_sources: tuple[str, ...],
    now: _dt.datetime | None = None,
    max_attempts: int = 5,
) -> tuple[dict[str, Any], OutlineRecoveryResult]:
    """Apply one idempotent, metadata-only rearm.

    This function is intentionally pure so the safety contract can be tested
    without a database.  It does not clear ``needs_replan``, pause reasons,
    human-review flags, or production-control state.
    """

    current = dict(metadata)
    report_id = _as_text(revision_report_id)
    revision = _as_text(source_revision)
    sources = tuple(sorted({_as_text(item) for item in changed_sources if _as_text(item)}))
    if not report_id or not revision or not sources:
        raise OutlineRecoveryError(
            "applied revision must identify report, source, and changed sources"
        )
    if not current.get("outline_replan_retry_exhausted"):
        prior = current.get("outline_replan_last_manual_repair_revision")
        if prior == report_id:
            return current, OutlineRecoveryResult(
                project_slug=_as_text(current.get("slug")),
                revision_report_id=report_id,
                source_revision=revision,
                changed_sources=sources,
                rearmed=False,
                reason="already_rearmed",
            )
        raise OutlineRecoveryError("outline replan retry is not exhausted")

    if current.get("outline_replan_last_manual_repair_revision") == report_id:
        return current, OutlineRecoveryResult(
            project_slug=_as_text(current.get("slug")),
            revision_report_id=report_id,
            source_revision=revision,
            changed_sources=sources,
            rearmed=False,
            reason="already_rearmed",
        )

    # Exactly one attempt remains.  Do not make the project look approved or
    # runnable; the dedicated planner owner still has to pass all gates.
    current["outline_replan_retry_attempts"] = max(0, int(max_attempts) - 1)
    current.pop("outline_replan_retry_exhausted", None)
    current.pop("outline_replan_next_retry_at", None)
    if current.get("outline_replan_auto_retry_exhausted"):
        try:
            auto_attempts = int(current.get("outline_replan_auto_retry_count", max_attempts))
        except (TypeError, ValueError):
            auto_attempts = max_attempts
        current["outline_replan_auto_retry_count"] = max(0, auto_attempts - 1)
        current.pop("outline_replan_auto_retry_exhausted", None)
        current.pop("outline_replan_auto_retry_last_reason", None)
        current.pop("outline_replan_auto_retry_last_error", None)
    current["outline_replan_last_manual_repair_revision"] = report_id
    current["outline_replan_rearm"] = {
        "revision_report_id": report_id,
        "source_revision": revision,
        "changed_sources": list(sources),
        "rearmed_at": (now or _dt.datetime.now(_dt.UTC)).isoformat(),
    }
    current["planning_status"] = "needs_replan"
    current["outline_semantic_gate_status"] = "needs_replan"
    return current, OutlineRecoveryResult(
        project_slug=_as_text(current.get("slug")),
        revision_report_id=report_id,
        source_revision=revision,
        changed_sources=sources,
        rearmed=True,
        reason="rearmed_one_bounded_attempt",
    )


async def rearm_outline_recovery_for_revision(
    session: AsyncSession,
    project_slug: str,
    revision_report_id: str | UUID,
) -> OutlineRecoveryResult:
    """Rearm one exhausted outline retry after a read-back applied revision.

    The transaction is committed only after all identity, source, pause, and
    active-workflow checks pass.  No queue/dispatch operation is performed.
    """

    slug = _as_text(project_slug)
    report_id = _as_text(revision_report_id)
    project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == slug))
    if project is None:
        raise OutlineRecoveryError(f"project not found: {slug}")
    if _as_text(project.status).lower() != ProjectStatus.PAUSED.value:
        raise OutlineRecoveryError("outline recovery requires a paused project")

    metadata = dict(project.metadata_json or {})
    marker = _revision_marker(metadata)
    if _as_text(marker.get("status")).lower() not in _APPLIED_MARKER_STATUSES:
        raise OutlineRecoveryError("derived revision is not marked applied")
    if _marker_report_id(marker) != report_id:
        raise OutlineRecoveryError("revision report does not match applied marker")
    source_revision = _marker_source_revision(marker)
    changed_sources = _marker_sources(marker)
    if not source_revision:
        raise OutlineRecoveryError("applied revision has no candidate hash")
    if not changed_sources:
        raise OutlineRecoveryError("applied revision has no changed sources")

    report = await session.scalar(
        select(ReviewReportModel).where(
            and_(
                ReviewReportModel.id == revision_report_id,
                ReviewReportModel.project_id == project.id,
            )
        )
    )
    if report is None:
        raise OutlineRecoveryError("revision report is missing or belongs to another project")
    if report.verdict != "pass" or report.reviewer_type != "derived_input_revision":
        raise OutlineRecoveryError("revision report is not an approved derived-input report")
    payload = report.structured_output
    candidate = payload.get("candidate") if isinstance(payload, Mapping) else None
    if (
        not isinstance(candidate, Mapping)
        or _as_text(candidate.get("candidate_hash")) != source_revision
    ):
        raise OutlineRecoveryError("applied revision candidate hash is stale")
    for key, artifact_type in (
        ("book_artifact_id", "book_spec"),
        ("world_artifact_id", "world_spec"),
    ):
        artifact_id = _as_text(marker.get(key))
        if not artifact_id:
            raise OutlineRecoveryError(f"applied revision missing {key}")
        artifact = await session.scalar(
            select(PlanningArtifactVersionModel).where(
                PlanningArtifactVersionModel.id == artifact_id,
                PlanningArtifactVersionModel.project_id == project.id,
                PlanningArtifactVersionModel.artifact_type == artifact_type,
            )
        )
        if artifact is None:
            raise OutlineRecoveryError(f"applied revision {key} is not current project data")

    control = await session.scalar(
        select(BookProductionControlModel)
        .where(BookProductionControlModel.project_id == project.id)
        .with_for_update()
    )
    if control is None or _as_text(control.desired_state).lower() != "pause":
        raise OutlineRecoveryError("authoritative production control is not paused")

    active = await session.scalar(
        select(WorkflowRunModel.id)
        .where(
            and_(
                WorkflowRunModel.project_id == project.id,
                WorkflowRunModel.status.in_(_ACTIVE_WORKFLOW_STATUSES),
            )
        )
        .limit(1)
    )
    if active is not None:
        raise OutlineRecoveryError("active workflow already owns this project")

    updated, result = rearm_outline_recovery_metadata(
        metadata,
        revision_report_id=report_id,
        source_revision=source_revision,
        changed_sources=changed_sources,
    )
    project.metadata_json = updated
    # Keep the authoritative pause and needs-replan state untouched.
    # The caller owns commit/rollback so this claim can be composed with the
    # subsequent bounded outline attempt atomically.
    await session.flush()
    return OutlineRecoveryResult(
        project_slug=slug,
        revision_report_id=result.revision_report_id,
        source_revision=result.source_revision,
        changed_sources=result.changed_sources,
        rearmed=result.rearmed,
        reason=result.reason,
    )
