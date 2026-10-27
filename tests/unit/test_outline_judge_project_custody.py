"""Actual outline callers retain project/workflow custody before any paid call.

The planner checkpoint uses isolated SQLite. The materializer uses the existing
approved-source entry fixture. Both execute the real commercial judge renderer;
only its final complete_text boundary is intercepted, with no provider access.
"""

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import JSON, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable
from test_workflow_approved_outline_source_guard import _batch, _run, rig  # noqa: F401

from bestseller.infra.db.models import ProjectModel, WorkflowRunModel
from bestseller.services import outline_llm_judge, planner, workflows
from bestseller.services.prompt_source_policy import content_hash
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class RequestCaptured(BaseException):
    """Stop before dispatch without triggering unrelated workflow recovery."""


def _capture_gateway(monkeypatch, *, project_id, workflow_id=None):
    requests = []

    async def capture(session, settings, request):
        # This is deliberately checked at the actual judge's gateway boundary,
        # not in a mocked judge accepting arbitrary missing kwargs.
        assert request.project_id == project_id
        assert request.workflow_run_id is not None
        if workflow_id is not None:
            assert request.workflow_run_id == workflow_id
        assert request.prompt_template == "outline_commercial_judge"
        assert request.logical_role == "critic"
        requests.append(request)
        raise RequestCaptured

    monkeypatch.setattr(outline_llm_judge, "complete_text", capture)
    return requests


@pytest.mark.parametrize("language,title", [("en", "The Tide Register"), ("zh-CN", "山城旧信")])
async def test_actual_planner_judge_binds_project_and_persisted_pending_checkpoint(
    monkeypatch, language, title
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    def create_tables(connection):
        for model in (ProjectModel, WorkflowRunModel):
            table = model.__table__.to_metadata(MetaData())
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                if column.server_default and "::jsonb" in str(column.server_default.arg):
                    column.server_default = None
            connection.execute(CreateTable(table, include_foreign_key_constraints=[]))

    async with engine.begin() as connection:
        await connection.run_sync(create_tables)
    project_id, workflow_id, foreign_project_id = uuid4(), uuid4(), uuid4()
    project = ProjectModel(
        id=project_id, slug=f"custody-{uuid4().hex}", title=title, genre="fantasy",
        language=language, target_chapters=12, target_word_count=30000,
        metadata_json={"creation_protagonist_name": "Mara"},
    )
    candidate = _batch().model_dump(mode="json", by_alias=True)
    before = deepcopy(candidate)
    requests = _capture_gateway(monkeypatch, project_id=project_id, workflow_id=workflow_id)
    monkeypatch.setattr(planner, "is_strict_prewrite_project", lambda project: True)
    try:
        async with factory() as session:
            session.add_all([
                project,
                ProjectModel(id=foreign_project_id, slug=f"foreign-{uuid4().hex}",
                             title="Other book", genre="mystery", target_chapters=20,
                             target_word_count=50000, metadata_json={"keep": True}),
                WorkflowRunModel(id=workflow_id, project_id=project_id,
                                 workflow_type="generate_volume_plan", status="running",
                                 metadata_json={"prior_paid_attempts": 4}),
            ])
            await session.commit()
            with pytest.raises(RequestCaptured):
                await planner._run_planner_outline_commercial_judge(
                    session, load_settings(), project=project, workflow_run_id=workflow_id,
                    step_name="volume_1_outline_commercial_judge", step_order=1,
                    outline_payload=candidate,
                )
        async with factory() as reader:
            saved = await reader.get(ProjectModel, project_id)
            checkpoints = saved.metadata_json["outline_commercial_judge_checkpoints"]
            record = next(iter(checkpoints.values()))["latest_candidate"]
            assert record["workflow_run_id"] == str(workflow_id)
            assert record["candidate"] == before
            assert record["candidate_hash"] == content_hash(before)
            assert record["report"] is None and record["judgement_status"] == "pending"
            workflow = await reader.get(WorkflowRunModel, workflow_id)
            assert workflow.project_id == requests[0].project_id
            assert workflow.status == "running"
            assert workflow.metadata_json["prior_paid_attempts"] == 4
            foreign = await reader.get(ProjectModel, foreign_project_id)
            assert foreign.metadata_json == {"keep": True}
            assert len(list(await reader.scalars(select(WorkflowRunModel)))) == 1
        assert len(requests) == 1 and candidate == before
    finally:
        await engine.dispose()


@pytest.mark.parametrize("language,title", [("en", "The Tide Register"), ("zh-CN", "山城旧信")])
async def test_actual_materializer_passes_its_own_project_and_workflow_to_real_judge(
    request, monkeypatch, language, title
):
    h = request.getfixturevalue("rig")
    h.project.language, h.project.title = language, title
    settings = workflows.load_settings()
    settings.pipeline.enable_outline_llm_commercial_judge = True
    settings.pipeline.enable_outline_reader_experience_judge = False
    before = deepcopy(h.source.content)
    requests = _capture_gateway(monkeypatch, project_id=h.project.id)

    with pytest.raises(RequestCaptured):
        await _run(h)

    runs = [row for row in h.store.added if isinstance(row, WorkflowRunModel)]
    assert len(runs) == len(requests) == 1
    assert runs[0].project_id == h.project.id
    assert requests[0].workflow_run_id == runs[0].id
    assert runs[0].metadata_json["source_artifact_id"] == str(h.source.id)
    assert h.source.content == before and h.source.status == "approved"
    assert h.chapters == [] and h.scenes == []
