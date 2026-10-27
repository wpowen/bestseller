"""Resource projections retain state/history without fetching audit payloads."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from bestseller.infra.db.models import ProjectModel, WorkflowRunModel, WorkflowStepRunModel
from bestseller.services.inspection import build_project_workflow_overview
from bestseller.services.web_progress_queries import load_execution_workflow_summary
from bestseller.web import server

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def progress_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'web_resource_test.sqlite3'}")
    metadata = MetaData()
    for model in (ProjectModel, WorkflowRunModel, WorkflowStepRunModel):
        table = model.__table__.to_metadata(metadata)
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type, column.server_default = JSON(), None
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        projects = [
            ProjectModel(
                slug=slug,
                title=slug,
                genre="custom",
                target_word_count=4000,
                target_chapters=2,
                status="paused",
                metadata_json={},
                hype_scheme_json={},
            )
            for slug in ("harbor-resource", "unrelated-resource")
        ]
        session.add_all(projects)
        await session.commit()
        yield session, projects
    await engine.dispose()


@pytest.mark.parametrize("future_clock_skew", [False, True])
async def test_progress_bounds_events_preserves_liveness_and_never_loads_payloads(
    progress_db,
    future_clock_skew,
):
    session, (project, other) = progress_db
    now = datetime.now(UTC)
    runs = [
        WorkflowRunModel(
            project_id=project.id,
            workflow_type="chapter_pipeline",
            status=status,
            created_at=now - timedelta(days=3 + i),
            updated_at=now - timedelta(days=3),
            metadata_json={
                "planner_last_heartbeat_at": now.isoformat() if i == 1 else None,
                "large_private_payload": "x" * 120000,
            },
        )
        for i, status in enumerate(("running", "queued", "failed", "cancelled"))
    ]
    unrelated = WorkflowRunModel(
        project_id=other.id, workflow_type="other", status="running", metadata_json={}
    )
    session.add_all([*runs, unrelated])
    await session.flush()
    for i in range(64):
        session.add(
            WorkflowStepRunModel(
                workflow_run_id=runs[i % 4].id,
                step_name=f"step-{i}",
                step_order=i,
                status="failed" if i == 4 else "completed",
                created_at=now - timedelta(seconds=64 - i),
                input_ref={"body": "i" * 12000},
                output_ref={"evidence": i, "body": "o" * 12000},
            )
        )
    session.add(
        WorkflowStepRunModel(
            workflow_run_id=unrelated.id,
            step_name="must-be-isolated",
            step_order=0,
            status="failed",
            input_ref={},
            output_ref={},
            created_at=now + timedelta(seconds=1),
        )
    )
    await session.commit()
    if future_clock_skew:
        session.add(
            WorkflowStepRunModel(
                workflow_run_id=runs[0].id,
                step_name="future-clock",
                step_order=1000,
                status="completed",
                input_ref={},
                output_ref={},
                created_at=now + timedelta(days=1),
            )
        )
        await session.commit()
    run_ids = [r.id for r in runs]
    queries = []

    def capture(_conn, _cursor, statement, *_args):
        queries.append(statement)

    event.listen(session.bind.sync_engine, "before_cursor_execute", capture)
    try:
        summary = await load_execution_workflow_summary(session, project.id)
    finally:
        event.remove(session.bind.sync_engine, "before_cursor_execute", capture)
    assert len(queries) == 2
    assert all(q.lstrip().upper().startswith("SELECT") for q in queries)
    assert all("input_ref" not in q and "output_ref" not in q for q in queries)
    assert summary["run_count"] == 4
    assert len(summary["recent_steps"]) == 18
    assert summary["recent_steps"][0]["name"] == (
        "future-clock" if future_clock_skew else "step-63"
    )
    assert "must-be-isolated" not in {s["name"] for s in summary["recent_steps"]}
    assert sum(r["failed_step_count"] for r in summary["runs"]) == 1
    assert {r["workflow_run_id"] for r in summary["runs"]} == set(run_ids)
    assert "large_private_payload" not in str(summary)
    full = await build_project_workflow_overview(session, project.slug)
    for original, projected in zip(full.runs, summary["runs"], strict=True):
        assert server._workflow_activity_evidence(original.model_dump(mode="json")) == (
            server._workflow_activity_evidence(
                projected, latest_step_at=projected["latest_step_at"]
            )
        )
    assert full.runs[0].metadata["large_private_payload"] == "x" * 120000
    assert full.runs[0].steps[0].input_ref["body"] == "i" * 12000
    reduced = await build_project_workflow_overview(session, project.slug, include_details=False)
    assert reduced.run_count == full.run_count
    for original, projected in zip(full.runs, reduced.runs, strict=True):
        assert projected.metadata == {}
        assert projected.failed_step_count == original.failed_step_count
        assert [s.output_ref for s in projected.steps] == [s.output_ref for s in original.steps]
        assert all(s.input_ref == {} for s in projected.steps)
    assert (
        await session.scalar(
            select(WorkflowRunModel.metadata_json).where(WorkflowRunModel.id == run_ids[0])
        )
        == runs[0].metadata_json
    )


@pytest.mark.parametrize("archived", [False, True])
async def test_scoped_light_project_read_keeps_archival_and_repair_fields_without_files(
    progress_db,
    monkeypatch,
    archived,
):
    session, (project, _) = progress_db
    project.status = "planning"
    project.metadata_json = {"library_archived": archived, "llm_model_id": "configured-model"}
    await session.commit()

    @asynccontextmanager
    async def scope(_settings):
        yield session

    async def stats(_session, rows, **kwargs):
        assert kwargs["include_filesystem"] is False
        assert [r.id for r in rows] == [project.id]
        return {
            project.id: {
                "adopted_chapters": 0,
                "completed_chapters": 0,
                "word_count_total": 0,
                "candidate_chapters": 1,
            }
        }

    async def empty(*_args, **_kwargs):
        return {}

    def forbidden(*_args, **_kwargs):
        pytest.fail("Light listing must not traverse or read export files")

    monkeypatch.setattr(server, "session_scope", scope)
    monkeypatch.setattr(server, "_load_project_content_stats", stats)
    monkeypatch.setattr(server, "_load_workflow_activity_by_project", empty)
    monkeypatch.setattr(server, "_load_project_chapter_state_counts", empty)
    monkeypatch.setattr(server, "_load_project_autonomous_repair_task_counts", empty)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "glob", forbidden)
    (row,) = await server._load_projects_payload(object(), light=True, project_slug=project.slug)
    assert row["slug"] == project.slug
    assert row["library_archived"] is archived
    assert row["book_state"] == ("archived" if archived else "in_progress")
    assert row["candidate_chapters"] == 1 and row["adopted_chapters"] == 0
    assert row["llm_model_id"] == "configured-model"
    assert "repair_status" in row
    assert row["execution_active"] is False
