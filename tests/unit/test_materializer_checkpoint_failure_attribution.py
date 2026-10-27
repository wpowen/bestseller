"""Offline transaction proof: materializer failures retain checkpoint identity.

Only isolated SQLite tables and injected failures are used; no model is called.
"""

from uuid import uuid4

import pytest
from sqlalchemy import JSON, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from bestseller.infra.db import session as db_session
from bestseller.infra.db.models import ProjectModel, WorkflowRunModel, WorkflowStepRunModel
from bestseller.services import workflows
from bestseller.services.budget import ExecutionBudgetError

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("kind", ["graph", "tree", "bible", "outline"])
@pytest.mark.parametrize("code", ["execution_result_unknown", "execution_budget_exhausted"])
async def test_terminal_failure_survives_checkpoint_and_outer_rollback(monkeypatch, kind, code):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    def create_tables(connection):
        # Clone the DDL, never mutate the production model types or defaults.
        for model in (ProjectModel, WorkflowRunModel, WorkflowStepRunModel):
            table = model.__table__.to_metadata(MetaData())
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                if column.server_default and "::jsonb" in str(column.server_default.arg):
                    column.server_default = None
            connection.execute(CreateTable(table, include_foreign_key_constraints=[]))

    project_id, other_id = uuid4(), uuid4()
    error = ExecutionBudgetError(code, f"project:{project_id}")
    checkpoint_ids = []
    old_receipt = {"workflow_id": "historical", "code": "prior_failure"}
    metadata = {
        "author_fact": "retain",
        "retry_chain": {
            "generation_gate": {
                "auto_attempts_used": 3,
                "lifetime_attempts": 7,
                "chain_serial": 2,
                "last_trigger": "auto",
                "last_reason": "old_gate",
            }
        },
    }

    async def interrupted(session, *args, **kwargs):
        run = await session.scalar(
            select(WorkflowRunModel).where(
                WorkflowRunModel.project_id == project_id, WorkflowRunModel.id != other_id
            )
        )
        run.metadata_json = {**run.metadata_json, "execution_failure_receipts": [old_receipt]}
        checkpoint_ids.append(run.id)
        await session.commit()  # same transaction boundary as a pre-call checkpoint
        project = await session.get(ProjectModel, project_id)
        project.title = "unrelated post-checkpoint edit must roll back"
        raise error

    async def forbidden_model(*args, **kwargs):
        raise AssertionError("No model may run in this regression")

    monkeypatch.setattr("bestseller.services.llm.complete_text", forbidden_model)
    monkeypatch.setattr(db_session, "_global_session_factory", factory)
    if kind == "graph":
        monkeypatch.setattr(workflows, "rebuild_narrative_graph", interrupted)
        operation = workflows.materialize_narrative_graph
        arguments = {}
        expected_step = "rebuild_narrative_graph"
    elif kind == "tree":
        monkeypatch.setattr(workflows, "rebuild_narrative_tree", interrupted)
        operation = workflows.materialize_narrative_tree
        arguments = {}
        expected_step = "rebuild_narrative_tree"
    elif kind == "bible":
        monkeypatch.setattr(workflows, "_audit_bible_completeness", lambda **kw: None)
        monkeypatch.setattr(workflows, "apply_book_spec", interrupted)
        operation = workflows.materialize_story_bible
        arguments = {"book_spec_content": {"premise": "offline fixture"}}
        expected_step = "apply_book_spec"
    else:
        from bestseller.domain.workflow import ChapterOutlineBatchInput

        monkeypatch.setattr(
            workflows, "_load_story_engine_materialization_projections", interrupted
        )
        operation = workflows.materialize_chapter_outline_batch
        arguments = {"batch": ChapterOutlineBatchInput(batch_name="offline", chapters=[])}
        expected_step = "validate_outline_batch"

    try:
        async with engine.begin() as connection:
            await connection.run_sync(create_tables)
        async with factory() as session:
            session.add(
                ProjectModel(
                    id=project_id,
                    slug="checkpoint-attribution-test",
                    title="Original",
                    genre="general-fiction",
                    target_chapters=3,
                    target_word_count=7800,
                    metadata_json=metadata,
                )
            )
            session.add(
                WorkflowRunModel(
                    id=other_id,
                    project_id=project_id,
                    workflow_type="other_attempt",
                    status="running",
                    metadata_json={},
                )
            )
            await session.commit()
        with pytest.raises(ExecutionBudgetError) as caught:
            async with db_session.get_server_session() as session:
                await operation(session, "checkpoint-attribution-test", **arguments)
        assert caught.value is error
        # The shared transaction owner must persist only the exact checkpointed
        # workflow; no new workflow is manufactured as a replacement receipt.
        async with factory() as reader:
            runs = list(await reader.scalars(select(WorkflowRunModel)))
            assert len(runs) == 2 and len(checkpoint_ids) == 1
            failed = await reader.get(WorkflowRunModel, checkpoint_ids[0])
            assert failed.status == "failed"
            assert failed.error_message == code
            receipts = failed.metadata_json["execution_failure_receipts"]
            assert receipts[0] == old_receipt
            assert receipts[1] == {
                "workflow_id": str(failed.id),
                "project_id": str(project_id),
                "workflow_type": failed.workflow_type,
                "step": expected_step,
                "code": code,
            }
            assert (await reader.get(WorkflowRunModel, other_id)).status == "running"
            project = await reader.get(ProjectModel, project_id)
            assert project.title == "Original"
            assert project.metadata_json["author_fact"] == "retain"
            assert project.metadata_json["retry_chain"] == metadata["retry_chain"]
            assert project.metadata_json["execution_terminal"]["workflow_id"] == str(failed.id)
        # Replaying the same detached receipt must not duplicate history.
        await db_session._save_workflow_failures_after_rollback(factory, error)
        async with factory() as reader:
            failed = await reader.get(WorkflowRunModel, checkpoint_ids[0])
            assert len(failed.metadata_json["execution_failure_receipts"]) == 2
    finally:
        await engine.dispose()
