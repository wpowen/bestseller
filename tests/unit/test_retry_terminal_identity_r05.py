"""R05: worker terminal writes must retain the real, project-owned resume ID."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from bestseller.infra.db import session as db_session
from bestseller.infra.db.models import BookProductionControlModel, ProjectModel, WorkflowRunModel
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.execution_stage import attach_workflow_failure
from bestseller.worker.tasks import _persist_execution_terminal

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
CODE = "execution_result_unknown"


@pytest.fixture
async def database(tmp_path, monkeypatch):
    # Same isolated SQLite/table-copy convention as cancellation durability tests;
    # do not register a process-wide JSONB compiler or alter model metadata.
    path = tmp_path / "worker_terminal_identity_test.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    def ddl(connection):
        for model in (ProjectModel, WorkflowRunModel, BookProductionControlModel):
            table = model.__table__.to_metadata(MetaData())
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                if column.server_default and "::jsonb" in str(column.server_default.arg):
                    column.server_default = None
            connection.execute(CreateTable(table, include_foreign_key_constraints=[]))

    try:
        async with engine.begin() as connection:
            # Verify the actual SQL connection, in addition to conftest guards.
            databases = (await connection.exec_driver_sql("PRAGMA database_list")).all()
            assert databases[0][1] == "main" and databases[0][2] == str(path)
            assert "test" in path.stem
            await connection.run_sync(ddl)
        projects = [ProjectModel(
            id=uuid4(), slug=slug, title="Original", genre="custom",
            target_word_count=7800, target_chapters=3, status="drafting",
            metadata_json={"author_fact": "preserved", "retry_chain": {"used": 3}},
        ) for slug in ("terminal-owner-test", "terminal-foreign-test")]
        runs = [WorkflowRunModel(
            id=uuid4(), project_id=project.id, workflow_type="chapter_pipeline",
            status="running", current_step=step, metadata_json={"retained": step},
        ) for project, step in (
            (projects[0], "review_owner"),
            (projects[0], "unrelated_same_project"),
            (projects[1], "foreign_checkpoint"),
        )]
        async with factory() as session:
            session.add_all([*projects, *runs])
            session.add_all([BookProductionControlModel(project_id=p.id) for p in projects])
            await session.commit()
        # Only the DB factory is injected; rollback recovery and terminal helpers
        # run unchanged, through the real server transaction context.
        monkeypatch.setattr(db_session, "_global_session_factory", factory)
        yield SimpleNamespace(
            factory=factory, context=db_session.get_server_session,
            project=projects[0], foreign_project=projects[1],
            owner=runs[0], sibling=runs[1], foreign=runs[2],
        )
    finally:
        await engine.dispose()


async def rollback_failure(rig):
    error = ExecutionBudgetError(CODE, f"project:{rig.project.id}")
    with pytest.raises(ExecutionBudgetError) as caught:
        async with rig.context() as session:
            project = await session.get(ProjectModel, rig.project.id)
            run = await session.get(WorkflowRunModel, rig.owner.id)
            project.title = "must roll back"
            attach_workflow_failure(error, run)
            run.status = "failed"
            await session.flush()  # An actual SQL write, then an actual rollback.
            raise error
    assert caught.value is error
    async with rig.factory() as session:
        project = await session.get(ProjectModel, rig.project.id)
        run = await session.get(WorkflowRunModel, rig.owner.id)
        assert project.title == "Original"
        assert project.metadata_json["execution_terminal"] == {
            "terminal": True, "code": CODE, "workflow_id": str(rig.owner.id),
        }
        assert run.status == "failed"
        receipt = run.metadata_json["execution_failure_receipts"][-1]
        assert receipt["workflow_id"] == str(run.id)
        assert receipt["project_id"] == str(project.id)
        saved_run_metadata = deepcopy(run.metadata_json)
    return error, saved_run_metadata


@pytest.mark.parametrize("worker_id", ["real-workflow", "arq-heal", "absent"])
async def test_worker_terminal_retains_rollback_resume_identity(database, worker_id):
    rig = database
    error, saved_run_metadata = await rollback_failure(rig)
    argument = {
        "real-workflow": str(rig.owner.id),
        "arq-heal": f"heal:autowrite:{rig.project.slug}",
        "absent": None,
    }[worker_id]
    await _persist_execution_terminal(
        rig.project.slug, error, argument, session_context=rig.context,
    )
    async with rig.factory() as session:
        project = await session.get(ProjectModel, rig.project.id)
        terminal = project.metadata_json["execution_terminal"]
        assert terminal.get("workflow_id") == str(rig.owner.id), (
            "worker overwrote the rollback-persisted WorkflowRun identity; "
            f"web resume requires workflow_id, got {terminal!r}"
        )
        owner = await session.get(WorkflowRunModel, UUID(terminal["workflow_id"]))
        assert owner.project_id == project.id and owner.status == "failed"
        assert owner.metadata_json == saved_run_metadata
        assert project.status == "paused" and project.title == "Original"
        assert project.metadata_json["author_fact"] == "preserved"
        assert project.metadata_json["retry_chain"] == {"used": 3}
        assert project.metadata_json["production_paused"] is True
        assert project.metadata_json["production_pause_reason"] == CODE
        assert (await session.get(WorkflowRunModel, rig.sibling.id)).status == "running"
        assert (await session.get(WorkflowRunModel, rig.foreign.id)).status == "running"


async def test_worker_terminal_binds_valid_same_project_workflow_without_prior_latch(database):
    rig = database
    await _persist_execution_terminal(
        rig.project.slug, ExecutionBudgetError(CODE, f"project:{rig.project.id}"),
        str(rig.owner.id), session_context=rig.context,
    )
    async with rig.factory() as session:
        project = await session.get(ProjectModel, rig.project.id)
        assert project.metadata_json["execution_terminal"].get("workflow_id") == str(rig.owner.id)
        assert (await session.get(WorkflowRunModel, rig.owner.id)).status == "failed"


async def test_worker_terminal_never_closes_or_borrows_foreign_workflow(database):
    rig = database
    error, _ = await rollback_failure(rig)
    await _persist_execution_terminal(
        rig.project.slug, error, str(rig.foreign.id), session_context=rig.context,
    )
    async with rig.factory() as session:
        foreign = await session.get(WorkflowRunModel, rig.foreign.id)
        assert (foreign.status, foreign.current_step, foreign.error_message, foreign.metadata_json) == (
            "running", "foreign_checkpoint", None, {"retained": "foreign_checkpoint"},
        ), "terminal handling for one project must not mutate another project's workflow"
        foreign_project = await session.get(ProjectModel, rig.foreign_project.id)
        assert foreign_project.status == "drafting"
        assert foreign_project.metadata_json == rig.foreign_project.metadata_json
        project = await session.get(ProjectModel, rig.project.id)
        assert project.metadata_json["execution_terminal"].get("workflow_id") == str(rig.owner.id)


@pytest.mark.parametrize("worker_id", ["arq-heal", "missing-uuid"])
async def test_worker_terminal_does_not_invent_identity_when_no_workflow_proves_it(database, worker_id):
    rig = database
    argument = f"heal:autowrite:{rig.project.slug}" if worker_id == "arq-heal" else str(uuid4())
    await _persist_execution_terminal(
        rig.project.slug, ExecutionBudgetError(CODE, f"project:{rig.project.id}"),
        argument, session_context=rig.context,
    )
    async with rig.factory() as session:
        project = await session.get(ProjectModel, rig.project.id)
        terminal = project.metadata_json["execution_terminal"]
        assert terminal["terminal"] is True and terminal["code"] == CODE
        assert terminal.get("workflow_id") is None
        assert (await session.get(WorkflowRunModel, rig.owner.id)).status == "running"
        assert (await session.get(WorkflowRunModel, rig.foreign.id)).status == "running"


async def test_conflicting_valid_identity_durably_blocks_without_rebinding(database):
    rig=database
    error,_=await rollback_failure(rig)
    from bestseller.services.retry_ledger import ExecutionRecoveryBlockedError
    with pytest.raises(ExecutionRecoveryBlockedError,match="execution_terminal_identity_blocked"):
        await _persist_execution_terminal(rig.project.slug,error,str(rig.sibling.id),session_context=rig.context)
    async with rig.factory() as s:
        p=await s.get(ProjectModel,rig.project.id)
        assert p.metadata_json["execution_terminal"]["workflow_id"]==str(rig.owner.id)
        assert p.metadata_json["execution_terminal"]["identity_blocked"] is True
        assert p.metadata_json["execution_terminal"]["code"]==CODE
        assert p.metadata_json["execution_terminal_identity_issues"][-1]["reason"]=="conflicting_valid_workflows"
        assert (await s.get(WorkflowRunModel,rig.sibling.id)).status=="running"
        assert p.metadata_json["retry_chain"]=={"used":3}
        issues=deepcopy(p.metadata_json["execution_terminal_identity_issues"])
    # A later callback naming the old owner does not reconcile the conflict.
    with pytest.raises(ExecutionRecoveryBlockedError):
        await _persist_execution_terminal(rig.project.slug,error,str(rig.owner.id),session_context=rig.context)
    async with rig.factory() as s:
        p=await s.get(ProjectModel,rig.project.id)
        assert p.metadata_json["execution_terminal_identity_issues"]==issues
        assert p.metadata_json["execution_terminal"]["identity_blocked"] is True
        assert (await s.get(WorkflowRunModel,rig.sibling.id)).status=="running"


@pytest.mark.parametrize("fault",["missing_row","foreign","malformed","wrong_scope"])
async def test_stale_previous_binding_is_not_replaced_by_valid_explicit(database,fault):
    rig=database
    previous={"missing_row":str(uuid4()),"foreign":str(rig.foreign.id),"malformed":"heal:old",
              "wrong_scope":str(rig.sibling.id)}[fault]
    async with rig.factory() as s:
        p=await s.get(ProjectModel,rig.project.id)
        p.metadata_json={**p.metadata_json,"execution_terminal":{"terminal":True,"code":"execution_cancelled",
            "workflow_id":previous,"retained_receipt":{"paid":"unchanged"}}}
        if fault=="wrong_scope":
            r=await s.get(WorkflowRunModel,rig.sibling.id);r.metadata_json={"execution_scope_id":"conception:other"}
        await s.commit()
    from bestseller.services.retry_ledger import ExecutionRecoveryBlockedError
    with pytest.raises(ExecutionRecoveryBlockedError):
        await _persist_execution_terminal(rig.project.slug,ExecutionBudgetError(CODE,f"project:{rig.project.id}"),
                                          str(rig.owner.id),session_context=rig.context)
    async with rig.factory() as s:
        p=await s.get(ProjectModel,rig.project.id)
        t=p.metadata_json["execution_terminal"]
        assert t["code"]==CODE and "workflow_id" not in t
        assert t["retained_receipt"]=={"paid":"unchanged"}
        assert p.metadata_json["execution_terminal_history"][-1]["workflow_id"]==previous
        assert (await s.get(WorkflowRunModel,rig.owner.id)).status=="running"
        assert (await s.get(WorkflowRunModel,rig.foreign.id)).status=="running"


@pytest.mark.parametrize("fault",["error_scope","wf_scope","terminal_status"])
async def test_scope_or_finished_workflow_does_not_get_closed(database,fault):
    rig=database
    async with rig.factory() as s:
        r=await s.get(WorkflowRunModel,rig.owner.id)
        if fault=="wf_scope":r.metadata_json={"execution_scope_id":"conception:other"}
        if fault=="terminal_status":r.status="completed"
        await s.commit()
    scope="conception:wrong" if fault=="error_scope" else f"project:{rig.project.id}"
    from bestseller.services.retry_ledger import ExecutionRecoveryBlockedError
    if fault=="error_scope":
        with pytest.raises(ExecutionRecoveryBlockedError):
            await _persist_execution_terminal(rig.project.slug,ExecutionBudgetError(CODE,scope),str(rig.owner.id),session_context=rig.context)
    else:
        await _persist_execution_terminal(rig.project.slug,ExecutionBudgetError(CODE,scope),str(rig.owner.id),session_context=rig.context)
    async with rig.factory() as s:
        p=await s.get(ProjectModel,rig.project.id);r=await s.get(WorkflowRunModel,rig.owner.id)
        assert r.status==("completed" if fault=="terminal_status" else "running")
        assert p.metadata_json["execution_terminal"]["code"]==CODE
        if fault!="terminal_status":assert "workflow_id" not in p.metadata_json["execution_terminal"]


async def test_pure_helper_keeps_current_failure_and_history_but_does_not_authenticate():
    from bestseller.services.retry_ledger import execution_terminal_metadata
    old={"author_fact":"keep","retry_chain":{"used":19},"execution_terminal":{
        "terminal":True,"code":"execution_cancelled","workflow_id":"untrusted","paid_receipt":{"count":290}}}
    before=deepcopy(old)
    result=execution_terminal_metadata(old,ExecutionBudgetError(CODE))
    assert old==before
    assert result["execution_terminal"]=={"terminal":True,"code":CODE,"paid_receipt":{"count":290}}
    assert result["execution_terminal_history"]==[old["execution_terminal"]]
    assert result["retry_chain"]==old["retry_chain"]
    assert execution_terminal_metadata(old,ExecutionBudgetError(CODE),verified_workflow_id="sql-caller-bound")["execution_terminal"]["workflow_id"]=="sql-caller-bound"
