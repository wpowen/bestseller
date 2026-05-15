"""Legacy project backfill for EmotionDrivenKernel.

The normal planner now creates ``emotion_driven_kernel`` for new projects.
This module gives older projects an LLM-free, conservative backfill path so
future chapter writing can consume the same writer/quality-gate contracts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType
from bestseller.domain.planning import PlanningArtifactCreate
from bestseller.infra.db.models import ProjectModel
from bestseller.services.emotion_driven_kernel import (
    emotion_driven_kernel_from_dict,
    emotion_driven_kernel_to_dict,
)
from bestseller.services.planner import build_emotion_driven_kernel_backfill_payload
from bestseller.services.projects import import_planning_artifact
from bestseller.services.workflows import get_latest_planning_artifact


@dataclass(frozen=True)
class EmotionKernelBackfillResult:
    status: str
    kernel: dict[str, Any] | None
    source: str
    artifact_id: UUID | None = None
    version_no: int | None = None
    skipped_reason: str | None = None

    @property
    def changed(self) -> bool:
        return self.status in {"created", "repaired_invalid", "forced"}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _existing_kernel(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return emotion_driven_kernel_to_dict(emotion_driven_kernel_from_dict(dict(value)))
    except Exception:
        return None


def _metadata_premise(metadata: Mapping[str, Any]) -> str | None:
    premise = metadata.get("premise")
    if isinstance(premise, Mapping):
        value = premise.get("premise")
        return str(value).strip() if value else None
    if isinstance(premise, str) and premise.strip():
        return premise.strip()
    for key in ("logline", "unique_hook", "dramatic_question", "theme_statement"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def _latest_artifact_content(
    session: AsyncSession,
    *,
    project_id: UUID,
    artifact_type: ArtifactType,
) -> dict[str, Any]:
    artifact = await get_latest_planning_artifact(
        session,
        project_id=project_id,
        artifact_type=artifact_type,
    )
    return _mapping(getattr(artifact, "content", None))


async def ensure_project_emotion_driven_kernel(
    session: AsyncSession,
    project: ProjectModel,
    *,
    requested_by: str = "system",
    persist_artifact: bool = False,
    force: bool = False,
) -> EmotionKernelBackfillResult:
    """Ensure a project has a valid ``emotion_driven_kernel`` in metadata.

    Existing valid kernels are preserved. Missing or invalid legacy metadata is
    repaired from available planning artifacts and local planner fallbacks.
    ``persist_artifact`` is opt-in because automatic writing entry points only
    need metadata injection and should avoid creating extra artifact versions.
    """

    metadata = dict(project.metadata_json or {})
    existing = _existing_kernel(metadata.get("emotion_driven_kernel"))
    if existing is not None and not force:
        if metadata.get("emotion_driven_kernel") != existing:
            metadata["emotion_driven_kernel"] = existing
            project.metadata_json = metadata
        return EmotionKernelBackfillResult(
            status="existing",
            kernel=existing,
            source="metadata",
        )

    project_id = getattr(project, "id", None)
    if project_id is None:
        return EmotionKernelBackfillResult(
            status="skipped",
            kernel=None,
            source="none",
            skipped_reason="project_id_missing",
        )

    book_spec = _mapping(metadata.get("book_spec")) or await _latest_artifact_content(
        session,
        project_id=project_id,
        artifact_type=ArtifactType.BOOK_SPEC,
    )
    world_spec = _mapping(metadata.get("world_spec")) or await _latest_artifact_content(
        session,
        project_id=project_id,
        artifact_type=ArtifactType.WORLD_SPEC,
    )
    cast_spec = _mapping(metadata.get("cast_spec")) or await _latest_artifact_content(
        session,
        project_id=project_id,
        artifact_type=ArtifactType.CAST_SPEC,
    )
    story_design_kernel = _mapping(
        metadata.get("story_design_kernel")
    ) or await _latest_artifact_content(
        session,
        project_id=project_id,
        artifact_type=ArtifactType.STORY_DESIGN_KERNEL,
    )
    premise_artifact = await _latest_artifact_content(
        session,
        project_id=project_id,
        artifact_type=ArtifactType.PREMISE,
    )
    premise = _metadata_premise(metadata) or str(premise_artifact.get("premise") or "")

    kernel = build_emotion_driven_kernel_backfill_payload(
        project,
        premise=premise or None,
        book_spec=book_spec,
        world_spec=world_spec,
        cast_spec=cast_spec,
        story_design_kernel=story_design_kernel,
        category_key=str(metadata.get("category_key") or "") or None,
    )
    status = "forced" if force else "created"
    if metadata.get("emotion_driven_kernel") is not None and existing is None:
        status = "repaired_invalid"

    backfill_meta: dict[str, Any] = {
        "status": status,
        "source": "legacy_backfill",
        "mode": "deterministic_fallback",
        "requested_by": requested_by,
        "created_at": datetime.now(UTC).isoformat(),
        "used_metadata": {
            "book_spec": bool(_mapping(metadata.get("book_spec"))),
            "world_spec": bool(_mapping(metadata.get("world_spec"))),
            "cast_spec": bool(_mapping(metadata.get("cast_spec"))),
            "story_design_kernel": bool(_mapping(metadata.get("story_design_kernel"))),
        },
        "used_artifact_fallback": {
            "book_spec": bool(book_spec) and not _mapping(metadata.get("book_spec")),
            "world_spec": bool(world_spec) and not _mapping(metadata.get("world_spec")),
            "cast_spec": bool(cast_spec) and not _mapping(metadata.get("cast_spec")),
            "story_design_kernel": bool(story_design_kernel)
            and not _mapping(metadata.get("story_design_kernel")),
        },
    }
    metadata["emotion_driven_kernel"] = kernel
    metadata["emotion_driven_kernel_backfill"] = backfill_meta
    project.metadata_json = metadata

    artifact_id: UUID | None = None
    version_no: int | None = None
    if persist_artifact:
        artifact = await import_planning_artifact(
            session,
            project.slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.EMOTION_DRIVEN_KERNEL,
                content=kernel,
                notes="Legacy EmotionDrivenKernel backfill",
            ),
        )
        artifact_id = artifact.id
        version_no = artifact.version_no
        project.metadata_json = {
            **(project.metadata_json or {}),
            "emotion_driven_kernel_backfill": {
                **backfill_meta,
                "artifact_id": str(artifact.id),
                "version_no": artifact.version_no,
            },
        }

    return EmotionKernelBackfillResult(
        status=status,
        kernel=kernel,
        source="legacy_backfill",
        artifact_id=artifact_id,
        version_no=version_no,
    )


__all__ = [
    "EmotionKernelBackfillResult",
    "ensure_project_emotion_driven_kernel",
]
