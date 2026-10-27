"""Read an already selected foundation cohort for unfinished outline planning."""

from copy import deepcopy

from sqlalchemy import select

from bestseller.infra.db.models import PlanningArtifactVersionModel
from bestseller.services.foundation_selection import (
    KINDS,
    POINTER,
    load_selected_foundation_artifacts,
)
from bestseller.services.prompt_source_policy import content_hash

SCHEMA = "fanqie-selected-foundation-stage.v1"


async def read_selected_foundation_stage(session, settings, *, project):
    """Caller first excludes any chapter/outline/materialization and dirty source.

    This is a planning-stage choice, not materialization, adoption or new credit.
    The normal Beat producer rechecks its complete source under publication locks.
    """
    with session.no_autoflush:
        selected = await load_selected_foundation_artifacts(
            session, project=project, settings=settings,
        )
        if selected is None or set(selected) != set(KINDS):
            raise ValueError("fanqie_selected_foundation_stage_requires_exact_cohort")
        outlines = list(await session.scalars(select(PlanningArtifactVersionModel).where(
            PlanningArtifactVersionModel.project_id == project.id,
            PlanningArtifactVersionModel.artifact_type == "chapter_outline_batch",
        ).execution_options(populate_existing=True, autoflush=False)))
        if outlines:
            from bestseller.services.fanqie_outline_contract_completion import (
                _check_outline_versions,
                _sources,
            )

            # Legacy imports dropped projection metadata. Reconstruct the exact
            # public body from its actual paid Beat rather than trust a marker.
            current, source = await _sources(session, settings, project.id)
            await _check_outline_versions(session, settings, current, source)
        pin = deepcopy(project.metadata_json[POINTER])
        return {
            "schema": SCHEMA,
            "planning_workflow_run_id": pin["workflow_id"],
            "foundation_selection_sha256": content_hash(pin),
            "foundation_artifact_ids": {key: str(row.id) for key, row in selected.items()},
            "foundation_contents": {key: deepcopy(row.content) for key, row in selected.items()},
            "unmaterialized_outline_sources": [
                {"id": str(row.id), "content_sha256": content_hash(row.content)} for row in outlines
            ],
            "planning_reused": True,
            "materialization_reused": False,
            "chapters_created": 0,
            "scenes_created": 0,
            "provider_calls": 0,
            "dispatch_authorized": False,
            "adoption_authorized": False,
            "grade_granted": False,
            "credit_released": False,
        }
