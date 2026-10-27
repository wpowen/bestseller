"""Actual selected-cohort proofs for the pre-outline planning resume stage."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import select
from test_fanqie_materialized_plan_resume import (
    evidence,
    integrated_modules,  # noqa: F401
    materialized_case,  # noqa: F401
    plan_case,  # noqa: F401
    store,  # noqa: F401
)

from bestseller.domain.project import ProjectCreate
from bestseller.infra.db import models as db
from bestseller.services.budget import bind_execution_scope
from bestseller.services.fanqie_foundation_stage_resume import SCHEMA
from bestseller.services.foundation_selection import POINTER

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def selected_case(plan_case):  # noqa: F811
    c = plan_case
    async with c.factory() as session:
        # Remove only isolated fixture materialization to model the actual
        # failed pre-outline stage. All selected foundation and paid rows stay.
        for model in (db.SceneCardModel, db.ChapterDraftVersionModel, db.ChapterModel):
            for row in await session.scalars(select(model).where(model.project_id == c.pid)):
                await session.delete(row)
        for row in await session.scalars(select(db.WorkflowRunModel).where(
            db.WorkflowRunModel.project_id == c.pid,
            db.WorkflowRunModel.workflow_type.in_(("materialize_chapter_outline_batch",
                "materialized_outline_source_admission")),
        )):
            await session.delete(row)
        for row in await session.scalars(select(db.PlanningArtifactVersionModel).where(
            db.PlanningArtifactVersionModel.project_id == c.pid,
            db.PlanningArtifactVersionModel.artifact_type == "chapter_outline_batch",
        )):
            await session.delete(row)
        project = await session.get(db.ProjectModel, c.pid)
        project.status = "planning"
        await session.commit()
        pin = deepcopy(project.metadata_json[POINTER])
    return SimpleNamespace(base=c, pin=pin)


async def read(c, session):
    project = await session.get(db.ProjectModel, c.base.pid)
    return await c.base.helper.require_or_read_existing_complete_fanqie_plan(
        session, c.base.settings, project=project,
    )


async def test_selected_foundation_is_distinct_from_fresh_or_materialized_plan(selected_case):
    c = selected_case
    before = await evidence(c.base)
    async with c.base.factory() as session:
        result = await read(c, session)
        assert not session.new and not session.dirty and not session.deleted
    assert result["schema"] == SCHEMA and result["planning_reused"] is True
    assert result["materialization_reused"] is False
    assert result["planning_workflow_run_id"] == c.pin["workflow_id"]
    assert result["foundation_artifact_ids"] == c.pin["artifact_ids"]
    assert all(result[k] is False for k in (
        "dispatch_authorized", "adoption_authorized", "grade_granted", "credit_released"))
    assert result["chapters_created"] == result["scenes_created"] == result["provider_calls"] == 0
    assert await evidence(c.base) == before
    assert len(c.base.sent) == 1  # Original offline paid admission fixture only.


async def test_public_route_keeps_selected_cohort_and_commits_before_projection_failure(
    selected_case, monkeypatch,
):
    c = selected_case
    pipeline = c.base.pipeline
    marker_id = None
    captured = []

    async def invariant_boundary(*args, **kwargs):
        # This test isolates caller staging; invariant admission has its own
        # real tests. No source or approval is changed by this inert boundary.
        return None

    async def no_kernel(*args, **kwargs):
        return None

    async def beat_boundary(session, settings, slug, premise, **kwargs):
        nonlocal marker_id
        assert slug and premise
        assert kwargs["book_spec"] == c.base.original.assets["book_spec"].content
        assert kwargs["cast_spec"] == c.base.original.assets["cast_spec"].content
        marker = db.PlanningArtifactVersionModel(project_id=c.base.pid,
            artifact_type="fanqie_beat_sheet", version_no=1, schema_version="1.0",
            status="draft", content={"transaction_marker_only": True, "grade_granted": False})
        session.add(marker)
        await session.flush()
        marker_id = marker.id
        captured.append("beat-returned")
        return object()

    class ProjectionFailure(Exception):
        pass

    def fail_projection(*args, **kwargs):
        captured.append("projection-failed")
        raise ProjectionFailure("controlled downstream failure after Beat return")

    async def latest_forbidden(*args, **kwargs):
        raise AssertionError("selected foundation must not be replaced by latest assets")

    monkeypatch.setattr(pipeline, "_ensure_project_invariants", invariant_boundary)
    monkeypatch.setattr(pipeline, "derive_ideology_kernel", no_kernel)
    monkeypatch.setattr(pipeline, "generate_fanqie_beat_sheet", beat_boundary)
    monkeypatch.setattr(pipeline, "get_latest_planning_artifact", latest_forbidden)
    monkeypatch.setattr(pipeline, "build_fanqie_segment_outline_batch", fail_projection)
    before = await evidence(c.base)
    async with c.base.factory() as session:
        project = await session.get(db.ProjectModel, c.base.pid)
        payload = ProjectCreate(slug=project.slug, title=project.title, genre=project.genre,
            language=project.language, project_type="fanqie_short", target_chapters=4,
            target_word_count=8000, metadata=deepcopy(project.metadata_json))
        with bind_execution_scope(c.base.original.scope):
            with pytest.raises(ProjectionFailure):
                await pipeline.run_fanqie_short_pipeline(session, c.base.settings,
                    project_payload=payload, premise=project.metadata_json["premise"])
        await session.rollback()
    async with c.base.factory() as reader:
        assert await reader.get(db.PlanningArtifactVersionModel, marker_id) is not None
        project = await reader.get(db.ProjectModel, c.base.pid)
        assert project.metadata_json[POINTER] == c.pin
    after = await evidence(c.base)
    assert captured == ["beat-returned", "projection-failed"]
    for table in before[0]:
        if table != "planning_artifact_versions":
            assert before[0][table] == after[0][table]
    assert before[1] == after[1] and len(c.base.sent) == 1


@pytest.mark.parametrize("fault", ["pin", "canonical", "foundation_owner", "outline"])
async def test_selected_stage_requires_actual_current_cohort_and_no_outline(selected_case, fault):
    c = selected_case
    async with c.base.factory() as session:
        project = await session.get(db.ProjectModel, c.base.pid)
        if fault == "pin":
            project.metadata_json = {**project.metadata_json, POINTER: {"status": "completed"}}
        elif fault == "canonical":
            row = await session.get(db.PlanningArtifactVersionModel, c.base.original.canonical.id)
            row.content = {**row.content, "changed_original_source": True}
        elif fault == "foundation_owner":
            row = await session.get(db.WorkflowRunModel, UUID(c.pin["workflow_id"]))
            row.status = "running"
        else:
            session.add(db.PlanningArtifactVersionModel(project_id=c.base.pid,
                artifact_type="chapter_outline_batch", version_no=2, schema_version="1.0",
                status="draft", content={"retained": True}))
        await session.commit()
    before = await evidence(c.base)
    async with c.base.factory() as session:
        with pytest.raises(c.base.helper.MaterializedFanqiePlanPendingError):
            await read(c, session)
    assert await evidence(c.base) == before
