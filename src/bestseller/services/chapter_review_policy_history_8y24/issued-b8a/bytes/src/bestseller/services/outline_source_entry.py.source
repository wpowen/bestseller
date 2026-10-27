"""Common source-admission entry for current chapter prose and review context."""
from collections.abc import Mapping

from sqlalchemy import select

from bestseller.domain.enums import ArtifactType
from bestseller.infra.db.models import SceneCardModel

ORDINARY_SELECTION_KEY = "fanqie_outline_source_selection"


async def current_outline_artifact(session, *, project, chapter):
    """Select the admitted source, rather than a later unrelated approved row."""
    from uuid import UUID
    from bestseller.infra.db.models import ChapterModel, PlanningArtifactVersionModel
    from bestseller.services.retained_planning_generation import _Reader
    from bestseller.services.rewrite_outline_binding import validate_source_plan_binding
    from bestseller.services.workflows import get_latest_approved_planning_artifact

    with session.no_autoflush:
        fresh = await _Reader(session).get(ChapterModel, chapter.id)
        if fresh is None or fresh.project_id != project.id or chapter.project_id != project.id:
            raise ValueError("outline_source_admission_required:current_chapter_owner")
        metadata, cached = fresh.metadata_json or {}, chapter.metadata_json or {}
        for key in ("outline_source_admission", "outline_prefix_readiness", ORDINARY_SELECTION_KEY):
            if ((key in metadata) != (key in cached)
                    or metadata.get(key) != cached.get(key)):
                raise ValueError("outline_source_admission_required:current_marker_changed")
        markers = [validate_source_plan_binding(metadata[key])
            for key in ("outline_source_admission", ORDINARY_SELECTION_KEY) if key in metadata]
        if not markers:
            return await get_latest_approved_planning_artifact(
                session, project_id=project.id, artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH)
        marker = markers[0]
        if any(other != marker for other in markers[1:]):
            raise ValueError("outline_source_admission_required:conflicting_selection_markers")
        if "outline_prefix_readiness" in metadata:
            prefix = metadata["outline_prefix_readiness"]
            if (not isinstance(prefix, Mapping)
                    or any(prefix.get(key) != marker[key]
                        for key in ("artifact_id", "workflow_run_id", "projection_hash"))
                    or ("source_content_sha256" in prefix
                        and prefix["source_content_sha256"] != marker["source_content_sha256"])):
                raise ValueError("outline_source_admission_required:conflicting_selection_markers")
        artifact = await _Reader(session).get(PlanningArtifactVersionModel, UUID(marker["artifact_id"]))
        if (artifact is None or artifact.project_id != project.id or artifact.status != "approved"
                or artifact.artifact_type != ArtifactType.CHAPTER_OUTLINE_BATCH.value):
            raise ValueError("outline_source_admission_required:selected_artifact_missing")
        if ORDINARY_SELECTION_KEY in metadata:
            from bestseller.services.prompt_source_policy import content_hash

            if (artifact.scope_ref_id is not None
                    or content_hash(artifact.content) != marker["source_content_sha256"]):
                raise ValueError("outline_source_admission_required:selected_artifact_changed")
        return artifact


async def require_current_outline_source_admission(session, settings, *, project, chapter, scenes=None):
    from bestseller.services.materialized_outline_admission_read import (
        require_materialized_outline_source_admission,
    )
    with session.no_autoflush:
        artifact = await current_outline_artifact(session, project=project, chapter=chapter)
        if artifact is None:
            return None
        if scenes is None:
            scenes = list(await session.scalars(select(SceneCardModel).where(
                SceneCardModel.project_id == project.id, SceneCardModel.chapter_id == chapter.id,
            ).order_by(SceneCardModel.scene_number)))
        receipt = await require_materialized_outline_source_admission(
            session, settings, project=project, artifact=artifact, chapter=chapter, scenes=scenes,
        )
        metadata = chapter.metadata_json or {}
        if ORDINARY_SELECTION_KEY in metadata:
            from bestseller.services.materialized_outline_admission_read import SCHEMA
            from bestseller.services.rewrite_outline_binding import validate_source_plan_binding

            marker = validate_source_plan_binding(metadata[ORDINARY_SELECTION_KEY])
            if (not isinstance(receipt, Mapping)
                    or any(receipt.get(key) != expected for key, expected in {
                        "schema": SCHEMA, "source_artifact_id": marker["artifact_id"],
                        "workflow_run_id": marker["workflow_run_id"],
                        "source_content_sha256": marker["source_content_sha256"],
                        "source_plan_projection": marker["projection_hash"],
                    }.items())
                    or not isinstance(receipt.get("source_admission"), Mapping)
                    or receipt["source_admission"].get("draft_eligible") is not True):
                raise ValueError("outline_source_admission_required:ordinary_hard_admission_required")
        return receipt
