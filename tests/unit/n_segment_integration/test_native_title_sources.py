"""RED: actual native title-source acquisition, no approved positive snapshot.

Proposed API: await read_current_title_sources(session, settings,
    project_id=..., owner_workflow=<persistent normal project-pipeline row>).

These negative tests use actual AsyncSession/SQLite/native readers and guards.
An absent complete-plan/adopted-knowledge fixture never becomes a fabricated
successful source receipt. Component positives elsewhere are not integration
acceptance. Main executes RED/GREEN; this worker does not run the tests.
"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import importlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.domain.book_design_snapshot import build_book_design_snapshot
from bestseller.infra.db import models as db
from bestseller.services import llm, planner
from bestseller.services.budget import ExecutionBudgetError, bind_execution_scope
from bestseller.services.native_title_operator import title_operator as op
from bestseller.services.production_control import ProductionControlHaltedError
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
MODULE = "bestseller.services.full_flow_title_sources"


def title_reader():
    try:
        module = importlib.import_module(MODULE)
    except ModuleNotFoundError as exc:
        if exc.name != MODULE:
            raise
        pytest.fail("missing actual native read_current_title_sources acquisition API")
    reader = getattr(module, "read_current_title_sources", None)
    assert callable(reader), "missing actual native read_current_title_sources acquisition API"
    return reader


def locked_design(count):
    return build_book_design_snapshot(
        creation_intent={"genre_intent": {"channel": "male", "genre": "xianxia"},
                         "chapter_count": count},
        protagonist="林澈", tone="克制", target_words=count * 2000,
        chapter_count=count, approved_story_design={"source_contract_hash": "native-source-negative-case"},
    ).model_dump(mode="json")


@pytest.fixture
async def native_source_store(tmp_path, monkeypatch):
    # Local native rows are test inputs, never source/adoption approval proofs.
    path = tmp_path / "native_title_sources_test.sqlite3"
    url = f"sqlite+aiosqlite:///{path}"
    engine = create_async_engine(url)
    models = (
        db.ProjectModel, db.WorkflowRunModel, db.PlanningArtifactVersionModel,
        db.ChapterModel, db.SceneCardModel, db.ChapterDraftVersionModel,
        db.ExecutionBudgetModel, db.BookProductionControlModel, db.RewriteTaskModel,
    )
    for model in models:
        table = model.__table__.to_metadata(MetaData())
        for column in table.c:
            if isinstance(column.type, JSONB):
                column.type = JSON()
            if column.server_default is not None and "::" in str(column.server_default.arg):
                column.server_default = None
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
        async with engine.begin() as connection:
            await connection.run_sync(table.create)

    async def forbidden(*args, **kwargs):
        raise AssertionError("source acquisition cannot dispatch a model or title request")

    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(planner, "complete_text", forbidden)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    pid = uuid4()
    scope = f"project:{pid}"
    design = locked_design(8)
    project = db.ProjectModel(
        id=pid, slug="native-title-source-" + pid.hex[:12], title="雾站最后一班记录",
        genre="科幻", language="zh-CN", project_type="fanqie_short",
        status="writing", target_chapters=8, target_word_count=16000,
        metadata_json={
            "execution_scope_id": scope, "book_design_snapshot": design,
            "book_design_snapshot_id": design["snapshot_id"],
            "book_design_snapshot_hash": design["source_hash"],
            "book_design_snapshot_status": "locked",
        },
    )
    owner = db.WorkflowRunModel(
        id=uuid4(), project_id=pid, workflow_type="project_pipeline", status="running",
        scope_type="project", scope_id=pid, current_step="title",
        metadata_json={"execution_scope_id": scope},
    )
    budget = db.ExecutionBudgetModel(
        scope_id=scope, status="active", max_calls=20, calls_used=2,
        max_tokens=100000, tokens_charged=1000,
        deadline_at=datetime.now(UTC) + timedelta(hours=1), metadata_json={},
    )
    control = db.BookProductionControlModel(
        project_id=pid, desired_state="run", command_serial=7,
        resume_pending=False, detail_json={},
    )
    async with factory() as session:
        session.add_all([project, owner, budget, control])
        await session.commit()
    settings = load_settings(env={"BESTSELLER__DATABASE__URL": url})
    try:
        yield SimpleNamespace(
            factory=factory, engine=engine, project_id=pid, owner_id=owner.id,
            scope=scope, settings=settings,
        )
    finally:
        await engine.dispose()


@contextmanager
def read_only_call(case, session, monkeypatch):
    def no_write(connection, cursor, statement, parameters, context, executemany):
        command = statement.lstrip().split(None, 1)[0].upper()
        if command in {"INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "REPLACE"}:
            raise AssertionError("native title-source acquisition emitted a write")

    async def no_transaction_mutation(*args, **kwargs):
        raise AssertionError("native title-source acquisition must not flush/commit/rollback")

    event.listen(case.engine.sync_engine, "before_cursor_execute", no_write)
    try:
        with monkeypatch.context() as guard:
            for name in ("flush", "commit", "rollback"):
                guard.setattr(session, name, no_transaction_mutation)
            with bind_execution_scope(case.scope):
                yield
    finally:
        event.remove(case.engine.sync_engine, "before_cursor_execute", no_write)


@pytest.mark.parametrize("state,code", [
    ("unknown", "execution_result_unknown"),
    ("awaiting_submission", "execution_submission_not_authorized"),
    ("cancelled", "execution_cancelled"),
    ("exhausted", "execution_budget_exhausted"),
    ("expired", "execution_deadline_exhausted"),
])
async def test_actual_budget_stop_survives_before_title_source_acquisition(native_source_store, monkeypatch, state, code):
    read = title_reader()
    case = native_source_store
    async with case.factory() as session:
        budget = await session.get(db.ExecutionBudgetModel, case.scope)
        if state == "expired":
            budget.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
        else:
            budget.status = state
        await session.commit()
        owner = await session.get(db.WorkflowRunModel, case.owner_id)
        def state_tuple():
            deadline = budget.deadline_at
            deadline = deadline.replace(tzinfo=UTC) if deadline.tzinfo is None else deadline.astimezone(UTC)
            return budget.status, budget.calls_used, budget.tokens_charged, deadline
        before = state_tuple()
        with read_only_call(case, session, monkeypatch):
            with pytest.raises(ExecutionBudgetError) as error:
                await read(session, case.settings, project_id=case.project_id, owner_workflow=owner)
        assert error.value.code == code
        assert state_tuple() == before
        assert not session.new and not session.dirty and not session.deleted


async def test_missing_budget_cannot_become_dispatch_admissible(native_source_store, monkeypatch):
    read = title_reader()
    case = native_source_store
    async with case.factory() as session:
        budget = await session.get(db.ExecutionBudgetModel, case.scope)
        await session.delete(budget)
        await session.commit()
        owner = await session.get(db.WorkflowRunModel, case.owner_id)
        with read_only_call(case, session, monkeypatch):
            with pytest.raises(ExecutionBudgetError) as error:
                await read(session, case.settings, project_id=case.project_id, owner_workflow=owner)
        assert error.value.code == "execution_scope_missing"


@pytest.mark.parametrize("intent", ["pause", "stop", "unrecognized"])
async def test_actual_operator_control_blocks_before_reading_title_sources(native_source_store, monkeypatch, intent):
    read = title_reader()
    case = native_source_store
    async with case.factory() as session:
        control = await session.get(db.BookProductionControlModel, case.project_id)
        control.desired_state = intent
        await session.commit()
        owner = await session.get(db.WorkflowRunModel, case.owner_id)
        with read_only_call(case, session, monkeypatch):
            with pytest.raises((ProductionControlHaltedError, ValueError), match="halted|production_(pause|stop)"):
                await read(session, case.settings, project_id=case.project_id, owner_workflow=owner)
        assert control.desired_state == intent and control.command_serial == 7


@pytest.mark.parametrize("fault", ["uuid_only", "transient_clone", "foreign_project", "chapter_owner", "completed_owner", "wrong_scope"])
async def test_owner_exemption_requires_the_actual_persistent_normal_owner(native_source_store, monkeypatch, fault):
    read = title_reader()
    case = native_source_store
    async with case.factory() as session:
        owner = await session.get(db.WorkflowRunModel, case.owner_id)
        supplied = owner
        if fault == "uuid_only":
            supplied = owner.id
        elif fault == "transient_clone":
            supplied = db.WorkflowRunModel(
                id=owner.id, project_id=owner.project_id, workflow_type=owner.workflow_type,
                status=owner.status, scope_type=owner.scope_type, scope_id=owner.scope_id,
                metadata_json=deepcopy(owner.metadata_json),
            )
        else:
            if fault == "foreign_project":
                owner.project_id = uuid4()
            elif fault == "chapter_owner":
                owner.workflow_type, owner.scope_type, owner.scope_id = "chapter_pipeline", "chapter", uuid4()
            elif fault == "completed_owner":
                owner.status = "completed"
            else:
                owner.metadata_json = {"execution_scope_id": "project:" + str(uuid4())}
            await session.commit()
        with read_only_call(case, session, monkeypatch):
            with pytest.raises(ValueError, match="owner|workflow|scope"):
                await read(session, case.settings, project_id=case.project_id, owner_workflow=supplied)


@pytest.mark.parametrize("status", ["running", "pending", "queued", "stopping"])
async def test_current_project_owner_does_not_hide_an_open_chapter_workflow(native_source_store, monkeypatch, status):
    read = title_reader()
    case = native_source_store
    async with case.factory() as session:
        chapter_wf = db.WorkflowRunModel(
            id=uuid4(), project_id=case.project_id, workflow_type="chapter_pipeline",
            status=status, scope_type="chapter", scope_id=uuid4(),
            metadata_json={"execution_scope_id": case.scope},
        )
        session.add(chapter_wf)
        await session.commit()
        owner = await session.get(db.WorkflowRunModel, case.owner_id)
        with read_only_call(case, session, monkeypatch):
            with pytest.raises(ValueError, match="open.*workflow|workflow.*open|unclosed.*workflow"):
                await read(session, case.settings, project_id=case.project_id, owner_workflow=owner)
        assert chapter_wf.status == status


async def test_owner_exemption_does_not_hide_a_second_project_owner(native_source_store, monkeypatch):
    read = title_reader()
    case = native_source_store
    async with case.factory() as session:
        session.add(db.WorkflowRunModel(
            id=uuid4(), project_id=case.project_id, workflow_type="project_pipeline",
            status="running", scope_type="project", scope_id=case.project_id,
            metadata_json={"execution_scope_id": case.scope},
        ))
        await session.commit()
        owner = await session.get(db.WorkflowRunModel, case.owner_id)
        with read_only_call(case, session, monkeypatch):
            with pytest.raises(ValueError, match="open.*workflow|workflow.*open|unclosed.*workflow"):
                await read(session, case.settings, project_id=case.project_id, owner_workflow=owner)


@pytest.mark.parametrize("status", ["pending", "queued", "processing", "running", "stopping", "in_progress"])
async def test_actual_unresolved_task_census_blocks_new_title_request(native_source_store, monkeypatch, status):
    read = title_reader()
    case = native_source_store
    async with case.factory() as session:
        task = db.RewriteTaskModel(
            id=uuid4(), project_id=case.project_id, trigger_type="chapter", trigger_source_id=uuid4(),
            rewrite_strategy="local", status=status, instructions="Resolve the retained chapter issue.",
            metadata_json={},
        )
        session.add(task)
        await session.commit()
        owner = await session.get(db.WorkflowRunModel, case.owner_id)
        with read_only_call(case, session, monkeypatch):
            with pytest.raises(ValueError, match="open.*(rewrite|task)|unclosed.*(rewrite|task)"):
                await read(session, case.settings, project_id=case.project_id, owner_workflow=owner)
        assert task.status == status


@pytest.mark.parametrize("fault", ["content_hash", "snapshot_id", "unlocked", "chapter_count"])
async def test_real_book_design_validation_cannot_be_replaced_by_a_true_flag(native_source_store, monkeypatch, fault):
    read = title_reader()
    case = native_source_store
    async with case.factory() as session:
        project = await session.get(db.ProjectModel, case.project_id)
        metadata = deepcopy(project.metadata_json)
        if fault == "content_hash":
            metadata["book_design_snapshot"]["source_hash"] = "0" * 64
        elif fault == "snapshot_id":
            metadata["book_design_snapshot_id"] = "f" * 16
        elif fault == "unlocked":
            metadata["book_design_snapshot_status"] = "draft"
        else:
            design = locked_design(4)
            metadata.update(book_design_snapshot=design, book_design_snapshot_id=design["snapshot_id"],
                            book_design_snapshot_hash=design["source_hash"])
        project.metadata_json = metadata
        await session.commit()
        owner = await session.get(db.WorkflowRunModel, case.owner_id)
        with read_only_call(case, session, monkeypatch):
            with pytest.raises(ValueError, match="design|snapshot|chapter|target|segment"):
                await read(session, case.settings, project_id=case.project_id, owner_workflow=owner)


@pytest.mark.parametrize("count", [4, 5, 6, 7, 8])
async def test_absent_complete_native_plan_never_reaches_snapshot_acceptance(native_source_store, monkeypatch, count):
    read = title_reader()
    case = native_source_store
    async with case.factory() as session:
        project = await session.get(db.ProjectModel, case.project_id)
        project.target_chapters, project.target_word_count = count, count * 2000
        design = locked_design(count)
        project.metadata_json = {**project.metadata_json, "book_design_snapshot": design,
            "book_design_snapshot_id": design["snapshot_id"], "book_design_snapshot_hash": design["source_hash"]}
        await session.commit()
        owner = await session.get(db.WorkflowRunModel, case.owner_id)
        def no_fabricated_snapshot(*args, **kwargs):
            raise AssertionError("missing native source proof cannot become approved snapshot booleans")
        monkeypatch.setattr(op, "check_current_snapshot", no_fabricated_snapshot)
        with read_only_call(case, session, monkeypatch):
            with pytest.raises(ValueError, match="complete.*plan|plan.*required|source_repair|foundation|outline"):
                await read(session, case.settings, project_id=case.project_id, owner_workflow=owner)
        assert not session.new and not session.dirty and not session.deleted
        assert (await session.scalars(select(db.WorkflowRunModel))).all() == [owner]


async def test_pending_owner_state_is_not_flushed_or_discarded(native_source_store, monkeypatch):
    read = title_reader()
    case = native_source_store
    async with case.factory() as session:
        owner = await session.get(db.WorkflowRunModel, case.owner_id)
        owner.metadata_json = {**owner.metadata_json, "caller_pending_note": "preserve me"}
        pending = deepcopy(owner.metadata_json)
        with read_only_call(case, session, monkeypatch):
            with pytest.raises(ValueError, match="pending|dirty|unflushed"):
                await read(session, case.settings, project_id=case.project_id, owner_workflow=owner)
        assert owner.metadata_json == pending and owner in session.dirty


async def test_none_owner_with_no_active_workflow_reaches_real_missing_plan_boundary(native_source_store, monkeypatch):
    read = title_reader()
    case = native_source_store
    async with case.factory() as session:
        owner = await session.get(db.WorkflowRunModel, case.owner_id)
        owner.status = "completed"
        await session.commit()
        with read_only_call(case, session, monkeypatch):
            with pytest.raises(ValueError, match="complete.*plan|plan.*required|source_repair|foundation|outline"):
                await read(session, case.settings, project_id=case.project_id)
        assert owner.status == "completed"
        assert not session.new and not session.dirty and not session.deleted


@pytest.mark.parametrize("status", ["running", "pending", "queued", "stopping"])
async def test_none_owner_does_not_hide_any_actual_active_project_workflow(native_source_store, monkeypatch, status):
    read = title_reader()
    case = native_source_store
    async with case.factory() as session:
        owner = await session.get(db.WorkflowRunModel, case.owner_id)
        owner.status = status
        await session.commit()
        with read_only_call(case, session, monkeypatch):
            with pytest.raises(ValueError, match="open.*workflow|workflow.*open|unclosed.*workflow"):
                await read(session, case.settings, project_id=case.project_id, owner_workflow=None)
        assert owner.status == status


async def test_none_owner_still_blocks_an_open_chapter_workflow(native_source_store, monkeypatch):
    read = title_reader()
    case = native_source_store
    async with case.factory() as session:
        owner = await session.get(db.WorkflowRunModel, case.owner_id)
        owner.status = "completed"
        session.add(db.WorkflowRunModel(
            id=uuid4(), project_id=case.project_id, workflow_type="chapter_pipeline", status="running",
            scope_type="chapter", scope_id=uuid4(), metadata_json={"execution_scope_id": case.scope},
        ))
        await session.commit()
        with read_only_call(case, session, monkeypatch):
            with pytest.raises(ValueError, match="open.*workflow|workflow.*open|unclosed.*workflow"):
                await read(session, case.settings, project_id=case.project_id, owner_workflow=None)


async def test_native_completion_authorization_preserves_old_exhausted_raw_limits(native_source_store, monkeypatch):
    from bestseller.services.budget import completion_authorization, execution_scope_block_code

    read = title_reader()
    case = native_source_store
    async with case.factory() as session:
        budget = await session.get(db.ExecutionBudgetModel, case.scope)
        budget.status = "exhausted"
        budget.max_calls, budget.calls_used = 1, 17
        budget.max_tokens, budget.tokens_charged = 1, 1000
        budget.deadline_at = datetime.now(UTC) - timedelta(days=1)
        budget.metadata_json = {"provider_authorization": {
            "mode": "completion_bound", "status": "active", "authorization_ref": "native-unit-authorization",
            "models": ["native-unit-model"], "endpoint_hosts": ["provider.example.invalid"],
            "objective": "Complete the separately authorized native title objective.",
        }}
        await session.commit()
        assert completion_authorization(budget) is not None
        assert execution_scope_block_code(budget) is None
        owner = await session.get(db.WorkflowRunModel, case.owner_id)
        before = (budget.status, budget.max_calls, budget.calls_used, budget.max_tokens, budget.tokens_charged,
                  deepcopy(budget.metadata_json))
        with read_only_call(case, session, monkeypatch):
            with pytest.raises(ValueError, match="complete.*plan|plan.*required|source_repair|foundation|outline"):
                await read(session, case.settings, project_id=case.project_id, owner_workflow=owner)
        assert (budget.status, budget.max_calls, budget.calls_used, budget.max_tokens, budget.tokens_charged,
                budget.metadata_json) == before
