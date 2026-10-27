from uuid import uuid4

import pytest
from sqlalchemy.dialects import sqlite
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from sqlalchemy.schema import CreateTable

from bestseller.infra.db import session as db_session
from bestseller.infra.db.models import ProjectModel,WorkflowRunModel
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.execution_stage import attach_workflow_failure

pytestmark=pytest.mark.unit


@compiles(JSONB,"sqlite")
def _jsonb(_type,_compiler,**_kwargs):
    return "JSON"


@pytest.mark.asyncio
async def test_terminal_workflow_survives_outer_rollback_without_touching_another_run(tmp_path,monkeypatch):
    path=tmp_path/"pipeline_failure_test.sqlite3"
    engine=create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with engine.begin() as conn:
        for model in (ProjectModel,WorkflowRunModel):
            await conn.exec_driver_sql(str(CreateTable(model.__table__).compile(dialect=sqlite.dialect())).replace("::jsonb",""))
    project=ProjectModel(id=uuid4(),slug="durability-fixture",title="Original",genre="custom",target_word_count=7800,target_chapters=3,metadata_json={"author_fact":"preserve"})
    first=WorkflowRunModel(id=uuid4(),project_id=project.id,workflow_type="test_first",status="running",metadata_json={})
    other=WorkflowRunModel(id=uuid4(),project_id=project.id,workflow_type="test_other",status="running",metadata_json={})
    async with factory() as session:
        session.add_all([project,first,other]);await session.commit()
    monkeypatch.setattr(db_session,"_global_session_factory",factory)
    with pytest.raises(ExecutionBudgetError) as caught:
        async with db_session.get_server_session() as session:
            row=await session.get(WorkflowRunModel,first.id)
            book=await session.get(ProjectModel,project.id);book.title="Unrelated edit must rollback"
            error=ExecutionBudgetError("execution_budget_exhausted",f"project:{project.id}")
            attach_workflow_failure(error,row)
            row.status="failed"
            raise error
    await db_session._save_workflow_failures_after_rollback(factory,caught.value)
    async with factory() as session:
        row=await session.get(WorkflowRunModel,first.id)
        assert row.status=="failed" and row.error_message=="execution_budget_exhausted"
        assert len(row.metadata_json["execution_failure_receipts"])==1
        assert (await session.get(WorkflowRunModel,other.id)).status=="running"
        book=await session.get(ProjectModel,project.id)
        assert book.title=="Original" and book.metadata_json["author_fact"]=="preserve"
        assert book.metadata_json["execution_terminal"]["code"]=="execution_budget_exhausted"
    await engine.dispose()
