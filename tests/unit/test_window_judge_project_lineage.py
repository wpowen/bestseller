"""Window calls preserve exact workflow ownership; no live gateway or database."""
import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import JSON, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from bestseller.infra.db.models import LlmRunModel, WorkflowRunModel
from bestseller.services import chapter_window_quality_judge as judge
from bestseller.services.llm import LLMCompletionResult
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    def create_tables(connection):
        for model in (WorkflowRunModel, LlmRunModel):
            table = model.__table__.to_metadata(MetaData())
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
            connection.execute(CreateTable(table, include_foreign_key_constraints=[]))

    async with engine.begin() as connection:
        await connection.run_sync(create_tables)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def completion():
    return LLMCompletionResult(
        content=json.dumps({"pass": True, "overall_score": 0.95}),
        provider="fixture", model_name="fixture-critic",
    )


@pytest.mark.parametrize("project_index", [0, 1])
async def test_window_request_and_persisted_row_keep_exact_project(db, monkeypatch, project_index):
    projects = [uuid4(), uuid4()]
    workflow_id = uuid4()
    calls = []

    async def capture(session, settings, request):
        calls.append(request)
        session.add(LlmRunModel(
            project_id=request.project_id, workflow_run_id=request.workflow_run_id,
            logical_role=request.logical_role, provider="fixture", model_name="fixture",
            prompt_template=request.prompt_template, metadata_json={},
        ))
        return completion()

    monkeypatch.setattr(judge, "complete_text", capture)
    async with db() as session:
        session.add(WorkflowRunModel(
            id=workflow_id, project_id=projects[project_index],
            workflow_type="chapter_review", status="running", metadata_json={},
        ))
        await session.commit()
        result = await judge.judge_chapter_window_quality(
            session, load_settings(env={}), chapters=[],
            project_id=projects[project_index], workflow_run_id=workflow_id,
        )
        await session.commit()
    assert result.passed and len(calls) == 1
    async with db() as session:
        row = await session.scalar(select(LlmRunModel))
        assert row.project_id == projects[project_index]
        assert row.workflow_run_id == workflow_id
        assert row.prompt_template == "chapter_window_quality_judge"


@pytest.mark.parametrize("case", ["foreign", "unbound", "missing", "no_workflow"])
async def test_invalid_workflow_binding_stops_before_gateway(db, monkeypatch, case):
    project_id, workflow_id = uuid4(), uuid4()
    calls = []

    async def capture(*args):
        calls.append(args)
        return completion()

    monkeypatch.setattr(judge, "complete_text", capture)
    async with db() as session:
        if case not in {"missing", "no_workflow"}:
            session.add(WorkflowRunModel(
                id=workflow_id, project_id=None if case == "unbound" else uuid4(),
                workflow_type="chapter_review", status="running", metadata_json={},
            ))
            await session.commit()
        with pytest.raises(ValueError, match=r"window_judge_.*"):
            await judge.judge_chapter_window_quality(
                session, load_settings(env={}), chapters=[], project_id=project_id,
                workflow_run_id=None if case == "no_workflow" else workflow_id,
            )
    assert not calls


async def test_actual_review_window_block_passes_project_identity(monkeypatch):
    # Execute the owned production block, not a duplicate of its call site.
    from bestseller.services import reviews

    tree = ast.parse(Path(reviews.__file__).read_text())
    function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == "review_chapter_draft")
    block = next(n for n in function.body if isinstance(n, ast.If)
                 and "enable_chapter_window_llm_judge" in ast.unparse(n.test))
    harness = ast.parse("async def run(review_result):\n    pass\n")
    harness.body[0].body = [copy.deepcopy(block)]
    calls = []

    async def capture(*args, **kwargs):
        calls.append(kwargs)
        return judge.LLMQualityJudgeResult(passed=True, overall_score=0.95)

    async def payload(*args, **kwargs):
        return [{"chapter_number": 1}, {"chapter_number": 2}]

    async def wait(value, **kwargs):
        return await value

    monkeypatch.setattr(judge, "judge_chapter_window_quality", capture)
    project = SimpleNamespace(id=uuid4())
    namespace = dict(vars(reviews))
    namespace.update(
        session=object(), project=project, workflow_run_id=uuid4(), chapter_number=2,
        settings=SimpleNamespace(pipeline=SimpleNamespace(enable_chapter_window_llm_judge=True)),
        chapter=SimpleNamespace(chapter_number=2), draft=object(),
        review_result=SimpleNamespace(verdict="pass", scores={}, findings=[], severity_max="low",
                                      evidence_summary={}, rewrite_instructions=[]),
        ChapterReviewResult=lambda **kwargs: SimpleNamespace(**kwargs),
        _recent_chapter_window_payload=payload, _await_optional_chapter_review_llm=wait,
    )
    exec(compile(ast.fix_missing_locations(harness), "<owned-window-review>", "exec"), namespace)
    await namespace["run"](namespace["review_result"])
    assert len(calls) == 1
    assert calls[0]["project_id"] == project.id
    assert calls[0]["workflow_run_id"] == namespace["workflow_run_id"]
