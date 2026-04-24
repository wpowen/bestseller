from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType
from bestseller.infra.db.models import ProjectModel, WorkflowRunModel

WORKFLOW_TYPE_MATERIALIZE_CHAPTER_OUTLINE = "materialize_chapter_outline_batch"
WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE = "materialize_story_bible"
WORKFLOW_TYPE_MATERIALIZE_NARRATIVE_GRAPH = "materialize_narrative_graph"

CORE_TRUTH_ARTIFACT_TYPES: frozenset[str] = frozenset(
    {
        ArtifactType.PREMISE.value,
        ArtifactType.BOOK_SPEC.value,
        ArtifactType.WORLD_SPEC.value,
        ArtifactType.CAST_SPEC.value,
        ArtifactType.VOLUME_PLAN.value,
        ArtifactType.ACT_PLAN.value,
    }
)


@dataclass(frozen=True)
class TruthVersionState:
    version: int
    updated_at: str | None
    last_changed_artifact_type: str | None
    fingerprints: dict[str, str]


@dataclass(frozen=True)
class TruthMaterializationStatus:
    component: str
    workflow_type: str
    status: str
    required_truth_version: int
    materialized_truth_version: int | None = None
    materialized_at: str | None = None
    workflow_run_id: UUID | None = None
    detail: str | None = None


class TruthVersionStaleError(ValueError):
    def __init__(
        self,
        *,
        project_slug: str,
        truth_version: int,
        stale_components: tuple[TruthMaterializationStatus, ...],
    ) -> None:
        labels = ", ".join(status.component for status in stale_components)
        super().__init__(
            f"Project '{project_slug}' has stale planning materializations for "
            f"truth_version={truth_version}: {labels}. Re-materialize story bible, "
            "narrative graph, and chapter outline before drafting new content."
        )
        self.project_slug = project_slug
        self.truth_version = truth_version
        self.stale_components = stale_components


def initialize_truth_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(metadata or {})
    payload.setdefault("truth_version", 1)
    payload.setdefault("truth_updated_at", None)
    payload.setdefault("truth_last_changed_artifact_type", None)
    fingerprints = payload.get("_truth_artifact_fingerprints")
    payload["_truth_artifact_fingerprints"] = (
        dict(fingerprints) if isinstance(fingerprints, dict) else {}
    )
    change_log = payload.get("_truth_change_log")
    payload["_truth_change_log"] = list(change_log) if isinstance(change_log, list) else []
    return payload


def truth_state_from_project(project: ProjectModel) -> TruthVersionState:
    metadata = initialize_truth_metadata(project.metadata_json)
    return TruthVersionState(
        version=max(int(metadata.get("truth_version") or 1), 1),
        updated_at=(
            str(metadata.get("truth_updated_at"))
            if metadata.get("truth_updated_at")
            else None
        ),
        last_changed_artifact_type=(
            str(metadata.get("truth_last_changed_artifact_type"))
            if metadata.get("truth_last_changed_artifact_type")
            else None
        ),
        fingerprints=dict(metadata.get("_truth_artifact_fingerprints") or {}),
    )


def truth_metadata_for_workflow(project: ProjectModel) -> dict[str, Any]:
    state = truth_state_from_project(project)
    return {
        "truth_version": state.version,
        "truth_updated_at": state.updated_at,
        "truth_last_changed_artifact_type": state.last_changed_artifact_type,
    }


def _artifact_fingerprint(content: Any) -> str:
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _artifact_key(artifact_type: ArtifactType, scope_ref_id: UUID | None) -> str:
    if scope_ref_id is None:
        return artifact_type.value
    return f"{artifact_type.value}:{scope_ref_id}"


def maybe_bump_project_truth_version(
    project: ProjectModel,
    *,
    artifact_type: ArtifactType,
    content: Any,
    scope_ref_id: UUID | None = None,
) -> bool:
    metadata = initialize_truth_metadata(project.metadata_json)
    if artifact_type.value not in CORE_TRUTH_ARTIFACT_TYPES:
        project.metadata_json = metadata
        return False

    fingerprints = dict(metadata.get("_truth_artifact_fingerprints") or {})
    key = _artifact_key(artifact_type, scope_ref_id)
    new_fingerprint = _artifact_fingerprint(content)
    previous = fingerprints.get(key)
    fingerprints[key] = new_fingerprint
    metadata["_truth_artifact_fingerprints"] = fingerprints

    if previous is None:
        # First-seed path: record the fingerprint so future changes can
        # detect drift, but do NOT touch ``truth_updated_at``. Writing a
        # timestamp here would trip ``assert_truth_materializations_fresh``
        # on the very first draft — historical bible/graph/outline runs
        # created before the bootstrap would all look stale even though the
        # canon has not actually diverged from them.
        metadata.setdefault("truth_updated_at", None)
        metadata.setdefault("truth_last_changed_artifact_type", None)
        project.metadata_json = metadata
        return False
    if previous == new_fingerprint:
        project.metadata_json = metadata
        return False

    new_version = max(int(metadata.get("truth_version") or 1), 1) + 1
    changed_at = datetime.now(timezone.utc).isoformat()
    metadata["truth_version"] = new_version
    metadata["truth_updated_at"] = changed_at
    metadata["truth_last_changed_artifact_type"] = artifact_type.value
    change_log = list(metadata.get("_truth_change_log") or [])
    change_log.append(
        {
            "truth_version": new_version,
            "artifact_type": artifact_type.value,
            "scope_ref_id": str(scope_ref_id) if scope_ref_id else None,
            "changed_at": changed_at,
        }
    )
    metadata["_truth_change_log"] = change_log[-20:]
    project.metadata_json = metadata
    return True


def _parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_run_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _latest_completed_workflow_run(
    session: AsyncSession,
    *,
    project_id: UUID,
    workflow_type: str,
) -> WorkflowRunModel | None:
    return await session.scalar(
        select(WorkflowRunModel)
        .where(
            WorkflowRunModel.project_id == project_id,
            WorkflowRunModel.workflow_type == workflow_type,
            WorkflowRunModel.status == "completed",
        )
        .order_by(WorkflowRunModel.created_at.desc())
        .limit(1)
    )


async def get_truth_materialization_statuses(
    session: AsyncSession,
    project: ProjectModel,
) -> tuple[TruthMaterializationStatus, ...]:
    state = truth_state_from_project(project)
    required_version = state.version
    changed_at = _parse_iso8601(state.updated_at)
    statuses: list[TruthMaterializationStatus] = []

    for component, workflow_type in (
        ("story_bible", WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE),
        ("narrative_graph", WORKFLOW_TYPE_MATERIALIZE_NARRATIVE_GRAPH),
        ("chapter_outline", WORKFLOW_TYPE_MATERIALIZE_CHAPTER_OUTLINE),
    ):
        run = await _latest_completed_workflow_run(
            session,
            project_id=project.id,
            workflow_type=workflow_type,
        )
        if run is None:
            statuses.append(
                TruthMaterializationStatus(
                    component=component,
                    workflow_type=workflow_type,
                    status="missing",
                    required_truth_version=required_version,
                    detail="No completed materialization workflow found.",
                )
            )
            continue

        metadata = dict(run.metadata_json or {})
        materialized_version = metadata.get("truth_version")
        materialized_version_int = (
            int(materialized_version)
            if isinstance(materialized_version, int)
            else None
        )
        materialized_at = _normalize_run_timestamp(run.created_at)

        is_stale = False
        detail = None
        if materialized_version_int is not None:
            is_stale = materialized_version_int < required_version
            if is_stale:
                detail = (
                    f"Materialized at truth_version={materialized_version_int}, "
                    f"but current truth_version is {required_version}."
                )
        elif changed_at is not None and materialized_at is not None:
            is_stale = materialized_at < changed_at
            if is_stale:
                detail = (
                    "Materialization predates the latest truth change and has no "
                    "recorded truth_version metadata."
                )

        statuses.append(
            TruthMaterializationStatus(
                component=component,
                workflow_type=workflow_type,
                status="stale" if is_stale else "fresh",
                required_truth_version=required_version,
                materialized_truth_version=materialized_version_int,
                materialized_at=materialized_at.isoformat() if materialized_at else None,
                workflow_run_id=run.id,
                detail=detail,
            )
        )

    return tuple(statuses)


async def assert_truth_materializations_fresh(
    session: AsyncSession,
    project: ProjectModel,
) -> None:
    state = truth_state_from_project(project)
    if state.updated_at is None:
        return
    statuses = await get_truth_materialization_statuses(session, project)
    stale_components = tuple(status for status in statuses if status.status != "fresh")
    if not stale_components:
        return
    raise TruthVersionStaleError(
        project_slug=project.slug,
        truth_version=state.version,
        stale_components=stale_components,
    )


__all__ = [
    "CORE_TRUTH_ARTIFACT_TYPES",
    "TruthMaterializationStatus",
    "TruthVersionStaleError",
    "TruthVersionState",
    "assert_truth_materializations_fresh",
    "get_truth_materialization_statuses",
    "initialize_truth_metadata",
    "maybe_bump_project_truth_version",
    "truth_metadata_for_workflow",
    "truth_state_from_project",
]
