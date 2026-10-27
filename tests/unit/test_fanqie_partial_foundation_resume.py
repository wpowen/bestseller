"""Real SQLite planning classification; no model/source-proof mocks."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from test_fanqie_materialized_plan_resume import (
    evidence,
    integrated_modules,  # noqa: F401
    materialized_case,  # noqa: F401
    plan_case,  # noqa: F401
    store,  # noqa: F401
)

from bestseller.infra.db import models as db
from bestseller.services.foundation_selection import HISTORY, POINTER

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def partial_case(plan_case):  # noqa: F811
    c = plan_case
    async with c.factory() as session:
        project = db.ProjectModel(
            slug="partial-foundation-" + uuid4().hex,
            title="Before the wedding",
            genre="realistic-life",
            language="zh-CN",
            project_type="fanqie_short",
            target_chapters=4,
            target_word_count=8000,
            status="planning",
            metadata_json={"premise": "A young cook takes over the wedding banquet."},
        )
        session.add(project)
        await session.flush()
        owner = db.WorkflowRunModel(
            project_id=project.id, workflow_type="generate_foundation_plan",
            status="failed", current_step="generate_cast_spec", scope_type="project",
            scope_id=project.id, metadata_json={"retained_test_marker": "immutable"},
            error_message="foundation_identity_contract failed",
        )
        session.add(owner)
        await session.flush()
        for kind in ("book_spec", "world_spec"):
            session.add(db.PlanningArtifactVersionModel(
                project_id=project.id, artifact_type=kind, version_no=1,
                schema_version="1.0", status="approved", source_run_id=owner.id,
                content={"retained": kind, "not_a_grade": True},
            ))
        await session.commit()
        return SimpleNamespace(base=c, pid=project.id, owner=owner.id)


async def read(c, session):
    project = await session.get(db.ProjectModel, c.pid)
    return await c.base.helper.require_or_read_existing_complete_fanqie_plan(
        session, c.base.settings, project=project,
    )


async def test_partial_foundation_is_normal_planning_not_materialized_reuse(partial_case):
    c = partial_case
    before = await evidence(c.base)
    statements = []
    engine = c.base.factory.kw["bind"].sync_engine

    def record(_connection, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        for _ in range(2):
            async with c.base.factory() as session:
                def reject_flush(*_args):
                    raise AssertionError("read-only classification must never flush")

                event.listen(session.sync_session, "before_flush", reject_flush)
                assert await read(c, session) is None
                assert not session.new and not session.dirty and not session.deleted
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert statements and all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert await evidence(c.base) == before
    assert len(c.base.sent) == 1  # Only the original offline paid fixture setup.


@pytest.mark.parametrize("fault", [
    "chapter", "scene", "draft", "beat", "outline", "pointer", "history",
    "materializer", "foundation_running", "foundation_completed", "foundation_unknown",
    "completed_project", "invalid_target", "volume_outline", "bible_owner", "graph_owner",
    "tree_owner", "admission_owner",
])
async def test_any_materialization_or_selection_or_owner_remains_fail_closed(partial_case, fault):
    c = partial_case
    async with c.base.factory() as session:
        project = await session.get(db.ProjectModel, c.pid)
        if fault == "chapter":
            session.add(db.ChapterModel(project_id=c.pid, chapter_number=1, title="Retained",
                                       chapter_goal="Preserve the existing chapter plan."))
        elif fault == "scene":
            session.add(db.SceneCardModel(project_id=c.pid, chapter_id=uuid4(), scene_number=1,
                                         scene_type="investigation", purpose={}, metadata_json={}))
        elif fault == "draft":
            session.add(db.ChapterDraftVersionModel(project_id=c.pid, chapter_id=uuid4(),
                version_no=1, content_md="Retained draft; never regenerate the plan."))
        elif fault in {"beat", "outline", "volume_outline"}:
            session.add(db.PlanningArtifactVersionModel(project_id=c.pid,
                artifact_type={"beat": "fanqie_beat_sheet", "outline": "chapter_outline_batch",
                               "volume_outline": "volume_chapter_outline"}[fault],
                version_no=1, schema_version="1.0", status="draft", content={"retained": True}))
        elif fault in {"pointer", "history"}:
            project.metadata_json = {**project.metadata_json,
                (POINTER if fault == "pointer" else HISTORY): None}
        elif fault == "materializer":
            session.add(db.WorkflowRunModel(project_id=c.pid,
                workflow_type="materialize_chapter_outline_batch", status="failed",
                scope_type="project", scope_id=c.pid, metadata_json={}))
        elif fault in {"bible_owner", "graph_owner", "tree_owner", "admission_owner"}:
            session.add(db.WorkflowRunModel(project_id=c.pid,
                workflow_type={"bible_owner": "materialize_story_bible",
                    "graph_owner": "materialize_narrative_graph",
                    "tree_owner": "materialize_narrative_tree",
                    "admission_owner": "materialized_outline_source_admission"}[fault],
                status="failed", scope_type="project", scope_id=c.pid, metadata_json={}))
        elif fault.startswith("foundation_"):
            owner = await session.get(db.WorkflowRunModel, c.owner)
            owner.status = {"foundation_running": "running", "foundation_completed": "completed",
                            "foundation_unknown": "unknown"}[fault]
        elif fault == "completed_project":
            project.status = "completed"
        else:
            project.target_chapters = 0
        await session.commit()
    before = await evidence(c.base)
    async with c.base.factory() as session:
        with pytest.raises(c.base.helper.MaterializedFanqiePlanPendingError):
            await read(c, session)
    assert await evidence(c.base) == before
    assert len(c.base.sent) == 1


async def test_partial_source_stale_and_pending_state_still_refused(partial_case):
    c = partial_case
    async with c.base.factory() as first:
        project = await first.get(db.ProjectModel, c.pid)
        async with c.base.factory() as other:
            changed = await other.get(db.ProjectModel, c.pid)
            changed.metadata_json = {**changed.metadata_json, "external_change": True}
            await other.commit()
        original = deepcopy(project.metadata_json)
        with pytest.raises(
            c.base.helper.MaterializedFanqiePlanPendingError, match="caller_project_stale"
        ):
            await c.base.helper.require_or_read_existing_complete_fanqie_plan(
                first, c.base.settings, project=project,
            )
        assert project.metadata_json == original
        first.add(db.ChapterDraftVersionModel(project_id=c.pid, chapter_id=uuid4(),
                                              version_no=1, content_md="Pending caller draft"))
        with pytest.raises(
            c.base.helper.MaterializedFanqiePlanPendingError, match="pending_source_state"
        ):
            await c.base.helper.require_or_read_existing_complete_fanqie_plan(
                first, c.base.settings, project=project,
            )
        assert len(first.new) == 1
    async with c.base.factory() as session:
        assert not list(await session.scalars(select(db.ChapterDraftVersionModel).where(
            db.ChapterDraftVersionModel.project_id == c.pid)))


async def test_partial_source_pending_deletion_is_preserved_and_refused(partial_case):
    c = partial_case
    before = await evidence(c.base)
    async with c.base.factory() as session:
        project = await session.get(db.ProjectModel, c.pid)
        asset = await session.scalar(select(db.PlanningArtifactVersionModel).where(
            db.PlanningArtifactVersionModel.project_id == c.pid,
            db.PlanningArtifactVersionModel.artifact_type == "book_spec"))
        await session.delete(asset)
        with pytest.raises(
            c.base.helper.MaterializedFanqiePlanPendingError, match="pending_source_state"
        ):
            await c.base.helper.require_or_read_existing_complete_fanqie_plan(
                session, c.base.settings, project=project,
            )
        assert asset in session.deleted
    assert await evidence(c.base) == before
