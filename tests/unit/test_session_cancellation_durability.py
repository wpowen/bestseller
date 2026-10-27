"""Cancelled task transactions roll back while exact detached receipts survive."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from bestseller.infra.db import session as db_session
from bestseller.infra.db.models import (
    BookProductionControlModel, ExecutionBudgetModel, ProjectModel,
    WorkflowRunModel, WorkflowStepRunModel,
)
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.execution_stage import attach_workflow_failure
from bestseller.services.progress_context import TaskCancelledError

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def database(tmp_path):
    path = tmp_path / "session_cancellation_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    def ddl(connection):
        for model in (ProjectModel, WorkflowRunModel, WorkflowStepRunModel,
                      BookProductionControlModel, ExecutionBudgetModel):
            table = model.__table__.to_metadata(MetaData())
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                if column.server_default and "::jsonb" in str(column.server_default.arg):
                    column.server_default = None
            connection.execute(CreateTable(table, include_foreign_key_constraints=[]))

    async with engine.begin() as connection:
        await connection.run_sync(ddl)
    project = ProjectModel(
        id=uuid4(),
        slug="cancel-test",
        title="Original",
        genre="test",
        target_word_count=7800,
        target_chapters=3,
        status="drafting",
        metadata_json={"author_fact": "preserve", "calls_used": 245, "retry_chain": {"used": 3}},
    )
    run = WorkflowRunModel(
        id=uuid4(),
        project_id=project.id,
        workflow_type="chapter_pipeline",
        status="running",
        current_step="review_exact_checkpoint",
        metadata_json={"old": [1]},
    )
    other = WorkflowRunModel(
        id=uuid4(),
        project_id=project.id,
        workflow_type="other",
        status="running",
        current_step="unrelated",
        metadata_json={},
    )
    step = WorkflowStepRunModel(
        id=uuid4(),
        workflow_run_id=run.id,
        step_name="review_exact_checkpoint",
        step_order=1,
        status="running",
    )
    async with factory() as session:
        session.add_all([project, run, other, step])
        await session.commit()
    yield engine, factory, project, run, other, step
    await engine.dispose()


def owner(monkeypatch, engine, factory, kind):
    if kind == "server":
        monkeypatch.setattr(db_session, "_global_session_factory", factory)
        return db_session.get_server_session
    monkeypatch.setattr(db_session, "create_engine", lambda _settings: engine)
    monkeypatch.setattr(db_session, "create_session_factory", lambda **_kw: factory)
    return db_session.session_scope


@pytest.mark.parametrize("kind", ["server", "one_shot"])
@pytest.mark.parametrize(
    "error_type,code",
    [
        (TaskCancelledError, "operator_halted"),
        (asyncio.CancelledError, "workflow_interrupted"),
    ],
)
async def test_cancel_after_checkpoint_rolls_back_and_preserves_recoverable_receipt(
    database,
    monkeypatch,
    kind,
    error_type,
    code,
):
    engine, factory, project, run, other, step = database
    context = owner(monkeypatch, engine, factory, kind)
    error = error_type("control flow, not a permanent budget cancellation")
    with pytest.raises(error_type) as caught:
        async with context() as session:
            current = await session.get(WorkflowRunModel, run.id)
            await session.commit()  # Same boundary as an already-saved paid checkpoint.
            changed = await session.get(ProjectModel, project.id)
            changed.title = "uncommitted edit"
            changed_step = await session.get(WorkflowStepRunModel, step.id)
            changed_step.status = "completed"
            attach_workflow_failure(error, current)
            raise error
    assert caught.value is error
    async with factory() as reader:
        saved = await reader.get(WorkflowRunModel, run.id)
        assert saved.status == "cancelled"
        assert saved.current_step == "review_exact_checkpoint"
        assert saved.error_message == code
        assert saved.metadata_json["old"] == [1]
        receipt = saved.metadata_json["execution_failure_receipts"][-1]
        assert receipt == {
            "workflow_id": str(run.id),
            "project_id": str(project.id),
            "workflow_type": "chapter_pipeline",
            "step": "review_exact_checkpoint",
            "code": code,
            "recoverable": True,
        }
        book = await reader.get(ProjectModel, project.id)
        assert book.title == "Original" and book.status == "drafting"
        assert book.metadata_json == project.metadata_json
        assert (await reader.get(WorkflowRunModel, other.id)).status == "running"
        assert (await reader.get(WorkflowStepRunModel, step.id)).status == "running"
    await db_session._save_workflow_failures_after_rollback(factory, error)
    async with factory() as reader:
        assert (
            len(
                (await reader.get(WorkflowRunModel, run.id)).metadata_json[
                    "execution_failure_receipts"
                ]
            )
            == 1
        )


@pytest.mark.parametrize("kind", ["server", "one_shot"])
async def test_real_task_cancel_without_detached_identity_only_rolls_back(
    database, monkeypatch, kind
):
    engine, factory, project, run, _, _ = database
    context = owner(monkeypatch, engine, factory, kind)
    entered = asyncio.Event()

    async def work():
        async with context() as session:
            book = await session.get(ProjectModel, project.id)
            book.title = "discard me"
            await session.flush()
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(work())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with factory() as reader:
        assert (await reader.get(ProjectModel, project.id)).title == "Original"
        saved = await reader.get(WorkflowRunModel, run.id)
        assert saved.status == "running" and saved.metadata_json == {"old": [1]}


@pytest.mark.parametrize("kind", ["server", "one_shot"])
async def test_budget_terminal_receipts_keep_existing_terminal_behavior(
    database, monkeypatch, kind
):
    engine, factory, project, run, _, _ = database
    context = owner(monkeypatch, engine, factory, kind)
    error = ExecutionBudgetError("execution_result_unknown")
    with pytest.raises(ExecutionBudgetError) as caught:
        async with context() as session:
            current = await session.get(WorkflowRunModel, run.id)
            attach_workflow_failure(error, current)
            raise error
    assert caught.value is error
    async with factory() as reader:
        saved = await reader.get(WorkflowRunModel, run.id)
        assert saved.status == "failed" and saved.current_step == "execution_result_unknown"
        assert "recoverable" not in saved.metadata_json["execution_failure_receipts"][-1]
        book = await reader.get(ProjectModel, project.id)
        assert book.status == "paused"
        assert book.metadata_json["execution_terminal"]["code"] == "execution_result_unknown"


@pytest.mark.parametrize("kind", ["server", "one_shot"])
@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
async def test_ordinary_attempt_failure_does_not_leave_running_or_poison_project(
    database, monkeypatch, kind, error_type,
):
    engine, factory, project, run, other, _ = database
    context = owner(monkeypatch, engine, factory, kind)
    error = error_type("failed local step")
    with pytest.raises(error_type) as caught:
        async with context() as session:
            current = await session.get(WorkflowRunModel, run.id)
            await session.commit()  # Earlier paid checkpoint survives.
            book = await session.get(ProjectModel, project.id)
            book.title = "uncommitted change"
            attach_workflow_failure(error, current)
            current.status = "failed"  # This write will itself roll back.
            raise error
    assert caught.value is error
    async with factory() as reader:
        saved = await reader.get(WorkflowRunModel, run.id)
        assert saved.status == "failed"
        assert saved.current_step == "review_exact_checkpoint"
        receipt = saved.metadata_json["execution_failure_receipts"][-1]
        assert receipt["code"] == "workflow_failed" and receipt["recoverable"]
        assert receipt["error_type"] == error_type.__name__
        book = await reader.get(ProjectModel, project.id)
        assert book.title == "Original" and book.metadata_json == project.metadata_json
        assert (await reader.get(WorkflowRunModel, other.id)).status == "running"


async def test_failed_workflow_history_migration_is_bound_idempotent_and_preserves_data(database):
    from bestseller.services.workflow_failure_reconciliation import reconcile_observed_failed_workflow
    _, factory, project, run, other, step = database
    proof = {"schema": "observed-failed-worker.v1", "job_success": False,
        "job_id": "isolated-failed-job", "owner_absent": True, "pending_provider_operations": 0,
        "workflow_id": str(run.id), "project_id": str(project.id),
        "failure_receipt_sha256": "a" * 64, "progress_sha256": "b" * 64}
    async with factory() as s:
        first = await reconcile_observed_failed_workflow(s, project_id=project.id,
            workflow_id=run.id, evidence=proof, actor="test-operator")
        assert first["status"] == "reconciled"
        await s.commit()
    async with factory() as s:
        again = await reconcile_observed_failed_workflow(s, project_id=project.id,
            workflow_id=run.id, evidence=proof, actor="test-operator")
        assert again["status"] == "already_reconciled"
        assert (await s.get(WorkflowRunModel, run.id)).current_step == "review_exact_checkpoint"
        assert (await s.get(WorkflowRunModel, other.id)).status == "running"
        assert (await s.get(WorkflowStepRunModel, step.id)).status == "running"
        assert (await s.get(ProjectModel, project.id)).metadata_json == project.metadata_json


@pytest.mark.parametrize("bad", ["owner", "pending", "foreign", "success", "completed"])
async def test_failed_workflow_migration_refuses_ambiguous_or_active_owner(database, bad):
    from bestseller.services.workflow_failure_reconciliation import reconcile_observed_failed_workflow
    _, factory, project, run, _, _ = database
    proof = {"schema": "observed-failed-worker.v1", "job_success": False,
        "job_id": "isolated-failed-job", "owner_absent": True, "pending_provider_operations": 0,
        "workflow_id": str(run.id), "project_id": str(project.id),
        "failure_receipt_sha256": "a" * 64, "progress_sha256": "b" * 64}
    if bad == "owner": proof["owner_absent"] = False
    elif bad == "pending": proof["pending_provider_operations"] = 1
    elif bad == "foreign": proof["workflow_id"] = str(uuid4())
    elif bad == "success": proof["job_success"] = True
    async with factory() as s:
        if bad == "completed":
            (await s.get(WorkflowRunModel, run.id)).status = "completed"
            await s.flush()
        with pytest.raises(ValueError):
            await reconcile_observed_failed_workflow(s, project_id=project.id,
                workflow_id=run.id, evidence=proof, actor="test-operator")


@pytest.mark.parametrize("blocked", [False, True])
async def test_explicit_run_retires_only_reconciled_pause_with_current_open_ledger(database, blocked):
    from datetime import UTC, datetime, timedelta
    from bestseller.services.production_control import request_pause, request_run, load_control_state
    _, factory, project, *_ = database
    scope = f"project:{project.id}"
    async with factory() as s:
        book = await s.get(ProjectModel, project.id)
        book.metadata_json = {**book.metadata_json, "production_paused": True,
            "production_pause_reason": "execution_reconciled_manual_resume_required",
            "independent_structural_block": True,
            "execution_reconciliation_history": [{"receipt": {
                "scope_id": scope, "remaining_unknown": 0, "status": "active",
                "reconciliation_id": "operator-proof",
            }}]}
        s.add(ExecutionBudgetModel(scope_id=scope, status="unknown" if blocked else "active",
            max_calls=10, max_tokens=10000, calls_used=4, tokens_charged=5000,
            deadline_at=datetime.now(UTC) + timedelta(hours=1), metadata_json={}))
        await request_pause(s, project.id, actor="test-operator")
        await s.commit()
    async with factory() as s:
        if blocked:
            with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
                await request_run(s, project.id, actor="test-operator")
            await s.rollback()
        else:
            await request_run(s, project.id, actor="test-operator")
            await s.commit()
    async with factory() as s:
        book = await s.get(ProjectModel, project.id)
        assert book.metadata_json["independent_structural_block"] is True
        row = await s.get(ExecutionBudgetModel, scope)
        assert row.calls_used == 4 and row.tokens_charged == 5000
        if blocked:
            assert book.metadata_json["production_paused"] is True
            assert (await load_control_state(s, project.id)).halted
        else:
            assert "production_paused" not in book.metadata_json
            assert len(book.metadata_json["reconciled_pause_resume_receipts"]) == 1
            assert not (await load_control_state(s, project.id)).halted


@pytest.mark.parametrize("kind", ["server", "one_shot"])
async def test_second_task_cancel_cannot_interrupt_rollback_or_detached_save(
    database, monkeypatch, kind
):
    from sqlalchemy.ext.asyncio import AsyncSession

    engine, factory, project, run, _, _ = database
    context = owner(monkeypatch, engine, factory, kind)
    working = asyncio.Event()
    rolling_back = asyncio.Event()
    release = asyncio.Event()
    actual = AsyncSession.rollback
    controlled_session = None
    caught_errors = []

    async def slow_rollback(session):
        if session is controlled_session:
            rolling_back.set()
            await release.wait()
        await actual(session)

    monkeypatch.setattr(AsyncSession, "rollback", slow_rollback)

    async def work():
        nonlocal controlled_session
        async with context() as session:
            controlled_session = session
            current = await session.get(WorkflowRunModel, run.id)
            await session.commit()
            book = await session.get(ProjectModel, project.id)
            book.title = "uncommitted after checkpoint"
            await session.flush()
            working.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as error:
                caught_errors.append(error)
                attach_workflow_failure(error, current)
                raise

    task = asyncio.create_task(work())
    await asyncio.wait_for(working.wait(), 3)
    task.cancel("first cancellation")
    await asyncio.wait_for(rolling_back.wait(), 3)
    task.cancel("second cancellation during rollback")
    release.set()
    with pytest.raises(asyncio.CancelledError) as caught:
        await asyncio.wait_for(task, 3)
    assert caught.value is caught_errors[0]
    async with factory() as reader:
        assert (await reader.get(ProjectModel, project.id)).title == "Original"
        saved = await reader.get(WorkflowRunModel, run.id)
        assert saved.status == "cancelled"
        assert saved.current_step == "review_exact_checkpoint"
        assert (
            saved.metadata_json["execution_failure_receipts"][0]["code"] == "workflow_interrupted"
        )


@pytest.mark.parametrize("kind", ["server", "one_shot"])
async def test_recovery_error_cannot_replace_original_cancellation(
    database, monkeypatch, kind, caplog
):
    engine, factory, project, _, _, _ = database
    context = owner(monkeypatch, engine, factory, kind)
    original = TaskCancelledError("operator pause")

    async def broken_recovery(*_args):
        raise RuntimeError("recovery failed")

    monkeypatch.setattr(db_session, "_save_workflow_failures_after_rollback", broken_recovery)
    with pytest.raises(TaskCancelledError) as caught:
        async with context() as session:
            book = await session.get(ProjectModel, project.id)
            book.title = "uncommitted"
            raise original
    assert caught.value is original
    assert "Session rollback/recovery failed (RuntimeError)" in caplog.text
    async with factory() as reader:
        assert (await reader.get(ProjectModel, project.id)).title == "Original"


@pytest.mark.parametrize(
    "error_type,code",
    [
        (TaskCancelledError, "operator_halted"),
        (asyncio.CancelledError, "workflow_interrupted"),
    ],
)
async def test_legacy_cancel_receipt_is_recoverable_without_clearing_real_terminal(
    database, error_type, code
):
    _, factory, project, run, _, _ = database
    previous = {"terminal": True, "code": "execution_result_unknown", "workflow_id": "old-attempt"}
    async with factory() as session:
        book = await session.get(ProjectModel, project.id)
        book.metadata_json = {**book.metadata_json, "execution_terminal": previous}
        book.status = "paused"
        await session.commit()
    error = error_type()
    error.workflow_failures = (
        {
            "workflow_id": str(run.id),
            "project_id": str(project.id),
            "workflow_type": "chapter_pipeline",
            "step": "review_exact_checkpoint",
            "code": "execution_cancelled",
        },
    )
    await db_session._save_workflow_failures_after_rollback(factory, error)
    await db_session._save_workflow_failures_after_rollback(factory, error)
    async with factory() as reader:
        saved = await reader.get(WorkflowRunModel, run.id)
        assert saved.status == "cancelled" and saved.current_step == "review_exact_checkpoint"
        receipts = saved.metadata_json["execution_failure_receipts"]
        assert len(receipts) == 1 and receipts[0]["code"] == code
        assert receipts[0]["original_code"] == "execution_cancelled" and receipts[0]["recoverable"]
        book = await reader.get(ProjectModel, project.id)
        assert book.metadata_json["execution_terminal"] == previous
        assert book.status == "paused"


async def test_detached_receipts_cannot_touch_wrong_owner_or_reopen_finished_run(database):
    _, factory, project, run, other, _ = database
    async with factory() as session:
        completed = await session.get(WorkflowRunModel, run.id)
        completed.status = "completed"
        completed.current_step = "done"
        await session.commit()
    error = TaskCancelledError()
    error.workflow_failures = (
        {
            "workflow_id": str(run.id),
            "project_id": str(project.id),
            "workflow_type": "chapter_pipeline",
            "step": "review_exact_checkpoint",
            "code": "operator_halted",
            "recoverable": True,
        },
        {
            "workflow_id": str(other.id),
            "project_id": str(uuid4()),
            "workflow_type": "other",
            "step": "wrong",
            "code": "operator_halted",
            "recoverable": True,
        },
    )
    await db_session._save_workflow_failures_after_rollback(factory, error)
    async with factory() as reader:
        completed = await reader.get(WorkflowRunModel, run.id)
        assert completed.status == "completed" and completed.current_step == "done"
        assert (await reader.get(WorkflowRunModel, other.id)).metadata_json == {}
        assert (await reader.get(ProjectModel, project.id)).metadata_json == project.metadata_json
