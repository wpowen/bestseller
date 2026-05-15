"""Legacy project compatibility backfill for the entry system.

New planning runs create ``entry_system_kernel`` and ``entry_registry`` during
the planner phase.  This module gives already-running projects a deterministic
one-shot compatibility path before they enter chapter or scene production.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType, ProjectStatus
from bestseller.domain.planning import PlanningArtifactCreate
from bestseller.infra.db.models import ProjectModel
from bestseller.services.entry_registry import (
    build_entry_coverage_matrix,
    build_fallback_entry_registry,
    entry_registry_from_dict,
    entry_registry_to_dict,
)
from bestseller.services.entry_system_kernel import (
    build_fallback_entry_system_kernel,
    entry_system_kernel_from_dict,
    entry_system_kernel_to_dict,
)
from bestseller.services.projects import import_planning_artifact
from bestseller.services.workflows import get_latest_planning_artifact

ACTIVE_PROJECT_STATUSES: tuple[str, ...] = (
    ProjectStatus.PLANNING.value,
    ProjectStatus.WRITING.value,
    ProjectStatus.REVISING.value,
)


@dataclass(frozen=True)
class EntrySystemBackfillResult:
    status: str
    kernel: dict[str, object] | None
    registry: dict[str, object] | None
    source: str
    artifact_id: UUID | None = None
    version_no: int | None = None
    skipped_reason: str | None = None

    @property
    def changed(self) -> bool:
        return self.status not in {"existing", "skipped"}


@dataclass(frozen=True)
class EntrySystemActiveBackfillSummary:
    scanned: int
    changed: int
    skipped: int
    failed: int
    results: tuple[EntrySystemBackfillResult, ...]


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _existing_kernel(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return entry_system_kernel_to_dict(entry_system_kernel_from_dict(dict(value)))
    except Exception:
        return None


def _existing_registry(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return entry_registry_to_dict(entry_registry_from_dict(dict(value)))
    except Exception:
        return None


async def _latest_artifact_content(
    session: AsyncSession,
    *,
    project_id: UUID | None,
    artifact_type: ArtifactType,
) -> dict[str, object]:
    if project_id is None:
        return {}
    artifact = await get_latest_planning_artifact(
        session,
        project_id=project_id,
        artifact_type=artifact_type,
    )
    return _mapping(getattr(artifact, "content", None))


async def _artifact_kernel(
    session: AsyncSession,
    *,
    project_id: UUID | None,
) -> dict[str, object] | None:
    payload = await _latest_artifact_content(
        session,
        project_id=project_id,
        artifact_type=ArtifactType.ENTRY_SYSTEM_KERNEL,
    )
    return _existing_kernel(payload)


def _build_registry_payload(
    project: ProjectModel,
    kernel_payload: dict[str, object],
    metadata: Mapping[str, object],
) -> dict[str, object]:
    kernel = entry_system_kernel_from_dict(kernel_payload)
    coverage_matrix = build_entry_coverage_matrix(
        kernel,
        target_chapters=getattr(project, "target_chapters", None),
        genre=getattr(project, "genre", None),
    )
    registry = build_fallback_entry_registry(
        kernel,
        coverage_matrix=coverage_matrix,
        project_metadata=metadata,
    )
    return entry_registry_to_dict(registry)


def _result_status(
    *,
    force: bool,
    had_kernel: bool,
    had_registry: bool,
    valid_kernel: bool,
    valid_registry: bool,
    source: str,
) -> str:
    if force:
        return "forced"
    if source == "artifact" and not valid_kernel:
        return "restored_from_artifact"
    if (had_kernel and not valid_kernel) or (had_registry and not valid_registry):
        return "repaired_invalid"
    if valid_kernel and not valid_registry:
        return "registry_created"
    return "created"


async def ensure_project_entry_system_compat(
    session: AsyncSession,
    project: ProjectModel,
    *,
    requested_by: str = "system",
    persist_artifact: bool = False,
    force: bool = False,
) -> EntrySystemBackfillResult:
    """Ensure a project has a valid entry kernel and registry in metadata.

    Existing valid packages are preserved. Missing or invalid legacy metadata is
    repaired from the latest artifact when possible, otherwise from deterministic
    project/story-design fallbacks. Callers own transaction commit boundaries.
    """

    metadata = dict(getattr(project, "metadata_json", None) or {})
    existing_kernel = _existing_kernel(metadata.get("entry_system_kernel"))
    existing_registry = _existing_registry(metadata.get("entry_registry"))
    had_kernel = metadata.get("entry_system_kernel") is not None
    had_registry = metadata.get("entry_registry") is not None

    if existing_kernel is not None and existing_registry is not None and not force:
        normalized = {
            **metadata,
            "entry_system_kernel": existing_kernel,
            "entry_registry": existing_registry,
        }
        if normalized != metadata:
            project.metadata_json = normalized
        return EntrySystemBackfillResult(
            status="existing",
            kernel=existing_kernel,
            registry=existing_registry,
            source="metadata",
        )

    project_id = getattr(project, "id", None)
    source = "legacy_backfill"
    kernel_payload: dict[str, object] | None = existing_kernel if not force else None
    if kernel_payload is None:
        artifact_payload = await _artifact_kernel(session, project_id=project_id)
        if artifact_payload is not None:
            kernel_payload = artifact_payload
            source = "artifact"

    if kernel_payload is None:
        story_design_kernel = _mapping(metadata.get("story_design_kernel"))
        used_story_design_artifact = False
        if not story_design_kernel:
            story_design_kernel = await _latest_artifact_content(
                session,
                project_id=project_id,
                artifact_type=ArtifactType.STORY_DESIGN_KERNEL,
            )
            used_story_design_artifact = bool(story_design_kernel)
        kernel_payload = entry_system_kernel_to_dict(
            build_fallback_entry_system_kernel(
                project,
                story_design_kernel=story_design_kernel,
            )
        )
    else:
        used_story_design_artifact = False

    registry_payload = (
        existing_registry if existing_registry is not None and not force else None
    )
    if registry_payload is None:
        registry_payload = _build_registry_payload(project, kernel_payload, metadata)

    status = _result_status(
        force=force,
        had_kernel=had_kernel,
        had_registry=had_registry,
        valid_kernel=existing_kernel is not None,
        valid_registry=existing_registry is not None,
        source=source,
    )
    backfill_meta: dict[str, object] = {
        "status": status,
        "source": source,
        "mode": "deterministic_fallback",
        "requested_by": requested_by,
        "created_at": datetime.now(UTC).isoformat(),
        "registry_entry_count": len(registry_payload.get("entries") or []),
        "used_metadata": {
            "story_design_kernel": bool(_mapping(metadata.get("story_design_kernel"))),
            "world_spec": bool(_mapping(metadata.get("world_spec"))),
            "book_spec": bool(_mapping(metadata.get("book_spec"))),
            "power_system": bool(_mapping(metadata.get("power_system"))),
        },
        "used_artifact_fallback": {
            "entry_system_kernel": source == "artifact",
            "story_design_kernel": used_story_design_artifact,
        },
    }
    metadata["entry_system_kernel"] = kernel_payload
    metadata["entry_registry"] = registry_payload
    metadata["entry_system_backfill"] = backfill_meta
    project.metadata_json = metadata

    artifact_id: UUID | None = None
    version_no: int | None = None
    if persist_artifact:
        artifact = await import_planning_artifact(
            session,
            project.slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.ENTRY_SYSTEM_KERNEL,
                content=kernel_payload,
                notes="Legacy EntrySystemKernel compatibility backfill",
            ),
        )
        artifact_id = artifact.id
        version_no = artifact.version_no
        project.metadata_json = {
            **(project.metadata_json or {}),
            "entry_system_backfill": {
                **backfill_meta,
                "artifact_id": str(artifact.id),
                "version_no": artifact.version_no,
            },
        }

    return EntrySystemBackfillResult(
        status=status,
        kernel=kernel_payload,
        registry=registry_payload,
        source=source,
        artifact_id=artifact_id,
        version_no=version_no,
    )


def _is_active_project(project: ProjectModel, statuses: Sequence[str]) -> bool:
    return str(getattr(project, "status", "") or "") in set(statuses)


async def ensure_active_projects_entry_system_compat(
    session: AsyncSession,
    *,
    requested_by: str = "system",
    statuses: Iterable[str] = ACTIVE_PROJECT_STATUSES,
    persist_artifact: bool = False,
    force: bool = False,
) -> EntrySystemActiveBackfillSummary:
    """Run entry-system compatibility once for active projects.

    This is intentionally additive and commit-neutral: callers can schedule it
    as a one-off maintenance step or rely on pipeline entry points.
    """

    status_values = tuple(str(status) for status in statuses)
    result = await session.scalars(
        select(ProjectModel).where(ProjectModel.status.in_(status_values))
    )
    projects = [
        project
        for project in list(result)
        if _is_active_project(project, status_values)
    ]
    backfill_results: list[EntrySystemBackfillResult] = []
    failed = 0
    for project in projects:
        try:
            backfill_results.append(
                await ensure_project_entry_system_compat(
                    session,
                    project,
                    requested_by=requested_by,
                    persist_artifact=persist_artifact,
                    force=force,
                )
            )
        except Exception:
            failed += 1
    return EntrySystemActiveBackfillSummary(
        scanned=len(projects),
        changed=sum(1 for item in backfill_results if item.changed),
        skipped=sum(1 for item in backfill_results if item.status == "skipped"),
        failed=failed,
        results=tuple(backfill_results),
    )


__all__ = [
    "ACTIVE_PROJECT_STATUSES",
    "EntrySystemActiveBackfillSummary",
    "EntrySystemBackfillResult",
    "ensure_active_projects_entry_system_compat",
    "ensure_project_entry_system_compat",
]
