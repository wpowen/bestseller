"""Real SQLite workflow shells bind actual task scope without rewriting history."""

# ruff: noqa: F811 - imported pytest fixture is injected by parameter name

from copy import deepcopy

import pytest
from sqlalchemy import event, func, select
from test_workflow_flush_failure_identity import database  # noqa: F401

from bestseller.domain.enums import WorkflowStatus
from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services.budget import bind_execution_scope, current_execution_scope
from bestseller.services.workflows import create_workflow_run

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def normal_create(session, database, *, metadata=None, key=None, other_project=False):
    return await create_workflow_run(
        session,
        project_id=database.other_project_id if other_project else database.project_id,
        workflow_type="chapter_pipeline",
        status=WorkflowStatus.RUNNING,
        scope_type="chapter",
        scope_id=database.chapter_id,
        requested_by="normal-pipeline-fixture",
        current_step="load_chapter_context",
        metadata=metadata,
        idempotency_key=key,
    )


@pytest.mark.parametrize("other_project", [False, True])
async def test_normal_caller_persists_actual_scope_and_all_metadata(database, other_project):
    metadata = {"scene_count": 4, "chapter_first": True, "author_contract": {"keep": [1, 2]}}
    before = deepcopy(metadata)
    async with database.factory() as session:
        with bind_execution_scope("test:original-task"):
            row = await normal_create(
                session, database, metadata=metadata, other_project=other_project
            )
        await session.commit()
        identity = row.id
    async with database.factory() as session:
        stored = await session.get(WorkflowRunModel, identity)
        assert stored.metadata_json == {**before, "execution_scope_id": "test:original-task"}
        assert stored.project_id == (
            database.other_project_id if other_project else database.project_id
        )
    assert metadata == before


@pytest.mark.parametrize("metadata", [None, {"author_note": "keep"}, {"execution_scope_id": None}])
async def test_no_ambient_scope_preserves_original_unknown_metadata(database, metadata):
    assert current_execution_scope() is None
    before = deepcopy(metadata)
    async with database.factory() as session:
        row = await normal_create(session, database, metadata=metadata)
        await session.commit()
        await session.refresh(row)
        assert row.metadata_json == (before or {})
    assert metadata == before


async def test_idempotent_old_unknown_is_never_backfilled_from_new_ambient_scope(database):
    async with database.factory() as session:
        original = await normal_create(
            session, database, metadata={"old_error": "preserved"}, key="old"
        )
        original.status, original.error_message = "failed", "old failure"
        await session.commit()
        identity = original.id
    async with database.factory() as session:
        with bind_execution_scope("test:original-task"):
            repeated = await normal_create(
                session, database, metadata={"new": "not merged"}, key="old"
            )
        assert repeated.id == identity
        assert repeated.metadata_json == {"old_error": "preserved"}
        assert repeated.status == "failed" and repeated.error_message == "old failure"
        assert not session.dirty and not session.new
        await session.commit()
    async with database.factory() as session:
        stored = await session.get(WorkflowRunModel, identity)
        assert stored.metadata_json == {"old_error": "preserved"}


@pytest.mark.parametrize("existing", [False, True])
async def test_conflicting_caller_scope_fails_before_query_or_mutation(database, existing):
    async with database.factory() as session:
        if existing:
            await normal_create(session, database, key="same-key")
            await session.commit()
        statements = []

        def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)

        event.listen(database.engine.sync_engine, "before_cursor_execute", capture)
        try:
            with (
                bind_execution_scope("test:actual-task"),
                pytest.raises(ValueError, match="workflow_execution_scope_conflict"),
            ):
                await normal_create(
                    session,
                    database,
                    metadata={"execution_scope_id": "test:other-task"},
                    key="same-key",
                )
        finally:
            event.remove(database.engine.sync_engine, "before_cursor_execute", capture)
        assert statements == []
        assert not session.dirty and not session.new


async def test_known_old_scope_conflict_rejects_idempotent_return_without_autoflush(database):
    async with database.factory() as session:
        with bind_execution_scope("test:old-task"):
            original = await normal_create(session, database, metadata={"keep": True}, key="known")
        await session.commit()
        before = deepcopy(original.metadata_json)
        count = await session.scalar(select(func.count()).select_from(WorkflowRunModel))
        # A read for scope validation must not flush pending unrelated work.
        pending = WorkflowRunModel(workflow_type="unrelated", status="queued", metadata_json={})
        session.add(pending)
        with (
            bind_execution_scope("test:other-task"),
            pytest.raises(ValueError, match="workflow_execution_scope_conflict"),
        ):
            await normal_create(session, database, key="known")
        assert pending in session.new and pending.id is None
        assert original.metadata_json == before and not session.dirty
        with session.no_autoflush:
            assert await session.scalar(select(func.count()).select_from(WorkflowRunModel)) == count


async def test_matching_known_scope_reuses_row_without_merging_new_metadata(database):
    async with database.factory() as session:
        with bind_execution_scope("test:same-task"):
            original = await normal_create(session, database, metadata={"keep": True}, key="known")
            await session.commit()
            repeated = await normal_create(
                session,
                database,
                metadata={"execution_scope_id": "test:same-task", "new": "ignored"},
                key="known",
            )
        assert repeated.id == original.id
        assert repeated.metadata_json == {"keep": True, "execution_scope_id": "test:same-task"}
        assert not session.dirty and not session.new
