"""Trusted web resume with a real SQLite store and unchanged downstream guards."""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.schema import CreateTable
from story_bible_read_views_test_support import create_story_bible_read_view_tables
from test_adopted_knowledge_resume_control_integrity import configured as adopted_db  # noqa: F401
from test_fanqie_segment_resume import forbid, resume, seed

from bestseller.infra.db.models import (
    BookProductionControlModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services import continuity, feedback, llm
from bestseller.services import fanqie_short_pipeline as fanqie
from bestseller.services import web_project_resume as web_resume
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.production_control import (
    auto_recovery_is_permitted,
    load_control_state,
    request_pause,
    request_run,
    request_stop,
)
from bestseller.web import server
from support.adopted_bible_fixture import seed_empty_bible

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def store(adopted_db, monkeypatch):  # noqa: F811
    session, settings, project, chapter, draft = adopted_db
    async with session.bind.begin() as connection:
        for model in (ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel):
            await connection.exec_driver_sql(str(
                CreateTable(model.__table__).compile(dialect=connection.dialect),
            ))
    factory = async_sessionmaker(session.bind, expire_on_commit=False)

    @asynccontextmanager
    async def scope(_settings):
        async with factory() as current:
            async with current.begin():
                yield current

    monkeypatch.setattr(server, "session_scope", scope)
    return SimpleNamespace(
        session=session, settings=settings, project=project, chapter=chapter,
        draft=draft, factory=factory, configured=adopted_db,
    )


async def arrange(store, *, intent="run", workflow=None):
    store.project.status = "paused"
    control = BookProductionControlModel(
        project_id=store.project.id, desired_state=intent, command_serial=7,
        state_reason="old reason", requested_by="old operator",
        resume_pending=True,
        detail_json={"preserved": {"unknown": None}, "web_resume_status_history": [{"old": True}]},
    )
    store.session.add(control)
    if workflow:
        store.session.add(WorkflowRunModel(
            project_id=store.project.id, workflow_type=workflow, status="failed",
            error_message="retained failure", metadata_json={"raw": "preserved"},
        ))
    await store.session.commit()


async def explicit(store):
    return await server._clear_production_halt_async(store.settings, store.project.slug)


async def read(store):
    async with store.factory() as session:
        project = await session.get(ProjectModel, store.project.id)
        control = await session.get(BookProductionControlModel, store.project.id)
        return project, control


async def protected(store):
    async with store.factory() as session:
        return [list((await session.execute(select(*model.__table__.columns))).mappings())
                for model in (WorkflowRunModel, ChapterModel, ChapterDraftVersionModel,
                              ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel)]


@pytest.mark.parametrize("workflow", [
    "generate_volume_plan", "generate_foundation_plan", "generate_novel_plan",
])
async def test_failed_planning_run_with_existing_chapter_recovers_planning(store, workflow):
    await arrange(store, workflow=workflow)
    metadata = deepcopy(store.project.metadata_json)
    before = await protected(store)
    assert await explicit(store)
    project, control = await read(store)
    assert project.status == "planning" and project.metadata_json == metadata
    assert control.desired_state == "run" and control.command_serial == 8
    history = control.detail_json["web_resume_status_history"]
    assert history[0] == {"old": True}
    assert history[1]["from_status"] == "paused" and history[1]["to_status"] == "planning"
    assert history[1]["previous_control"]["command_serial"] == 7
    assert history[1]["phase_evidence"]["latest_workflow"]["type"] == workflow
    assert await protected(store) == before


async def test_no_materialized_chapters_recovers_planning(store):
    await store.session.execute(delete(ChapterDraftVersionModel))
    await store.session.execute(delete(ChapterModel))
    await arrange(store)
    assert await explicit(store)
    assert (await read(store))[0].status == "planning"


@pytest.mark.parametrize("intent", ["run", "pause", "stop"])
async def test_explicit_resume_restores_writing_and_retains_old_control(store, intent):
    await arrange(store, intent=intent)
    before = await protected(store)
    metadata = deepcopy(store.project.metadata_json)
    assert await explicit(store)
    project, control = await read(store)
    assert project.status == "writing" and project.metadata_json == metadata
    assert control.desired_state == "run" and not control.resume_pending
    record = control.detail_json["web_resume_status_history"][-1]
    assert record["previous_control"]["desired_state"] == intent
    assert record["previous_control"]["reason"] == "old reason"
    assert record["previous_control"]["requested_by"] == "old operator"
    assert record["previous_control"]["resume_pending"] is True
    assert record["previous_control"]["detail"] == {"preserved": {"unknown": None}}
    assert control.detail_json["preserved"] == {"unknown": None}
    assert await protected(store) == before
    assert not await explicit(store)
    assert (await read(store))[1].detail_json == control.detail_json


async def test_explicit_resume_reaches_actual_cached_segment_without_new_work(store, monkeypatch):
    await seed(store.configured, monkeypatch)
    await create_story_bible_read_view_tables(store.session)
    await seed_empty_bible(store.configured, monkeypatch)
    await arrange(store)
    before = await protected(store)
    monkeypatch.setattr(feedback, "complete_text", forbid)
    monkeypatch.setattr(continuity, "complete_text", forbid)
    monkeypatch.setattr(llm, "complete_text", forbid)
    monkeypatch.setattr(fanqie, "run_chapter_pipeline", forbid)
    assert await explicit(store)
    await fanqie._run_fanqie_segments(
        store.session, store.settings, store.project, requested_by="test",
    )
    assert store.project.status == "writing"
    assert await protected(store) == before


@pytest.mark.parametrize("status", ["archived", "cancelled", "completed", "needs_replan"])
async def test_terminal_and_structural_statuses_do_not_release_control(store, status):
    await arrange(store, intent="pause")
    store.project.status = status
    await store.session.commit()
    before = await protected(store)
    with pytest.raises(web_resume.WebProjectResumeBlockedError):
        await explicit(store)
    project, control = await read(store)
    assert project.status == status and control.desired_state == "pause"
    assert control.command_serial == 7 and await protected(store) == before


@pytest.mark.parametrize("metadata", [
    {"structural_repair_required": True},
    {"planning_status": "needs_replan"},
    {"production_paused": True, "production_pause_reason": "unknown_safety_stop"},
    {"generation_resume_blocked_until_repair_audit": True},
])
async def test_structural_metadata_cannot_be_washed_by_explicit_run(store, metadata):
    await arrange(store)
    store.project.metadata_json = {**store.project.metadata_json, **metadata}
    await store.session.commit()
    with pytest.raises(web_resume.WebProjectResumeBlockedError):
        await explicit(store)
    project, control = await read(store)
    assert project.status == "paused" and control.command_serial == 7
    assert project.metadata_json == store.project.metadata_json


@pytest.mark.parametrize("state,code", [
    ("unknown", "execution_result_unknown"),
    ("cancelled", "execution_cancelled"),
    ("exhausted", "execution_budget_exhausted"),
    ("awaiting_submission", "execution_submission_not_authorized"),
    ("expired", "execution_deadline_exhausted"),
    ("authorization_paused", "execution_authorization_paused"),
])
@pytest.mark.parametrize("first_run", [False, True])
async def test_financial_stops_are_read_only_and_propagate_without_strict_flag(
    store, state, code, first_run,
):
    if first_run:
        store.project.status = "writing"
        await store.session.commit()
        assert (await read(store))[1] is None
    else:
        await arrange(store, intent="pause")
    ledger = ExecutionBudgetModel(
        scope_id=store.project.metadata_json["execution_scope_id"],
        status=state if state not in {"expired", "authorization_paused"} else "active",
        calls_used=9, tokens_charged=1234, max_calls=10, max_tokens=9999,
        deadline_at=datetime.now(UTC) + timedelta(days=-1 if state == "expired" else 1),
        metadata_json={"retained": None},
    )
    if state == "authorization_paused":
        ledger.metadata_json = {"provider_authorization": {
            "mode": "completion_bound", "authorization_ref": "old-auth", "models": ["offline"],
            "endpoint_hosts": ["offline.invalid"], "objective": "preserved", "status": "paused",
        }}
    store.session.add(ledger)
    await store.session.commit()
    before = await protected(store)
    with pytest.raises(ExecutionBudgetError, match=code):
        await explicit(store)
    project, control = await read(store)
    if first_run:
        assert project.status == "writing" and control is None
    else:
        assert project.status == "paused" and control.desired_state == "pause"
        assert control.command_serial == 7
    assert await protected(store) == before


async def test_terminal_metadata_and_unknown_control_are_not_reinterpreted(store):
    await arrange(store, intent="future_unknown")
    with pytest.raises(web_resume.WebProjectResumeBlockedError, match="unknown_control"):
        await explicit(store)
    control = await store.session.get(BookProductionControlModel, store.project.id)
    control.desired_state = "run"
    store.project.metadata_json = {
        **store.project.metadata_json,
        "execution_terminal": {"terminal": True, "code": "execution_result_unknown"},
    }
    await store.session.commit()
    with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
        await explicit(store)
    project, control = await read(store)
    assert project.status == "paused" and control.command_serial == 7
    assert project.metadata_json == store.project.metadata_json


async def test_existing_exact_reconciliation_remains_explicit_and_retains_evidence(store):
    await arrange(store)
    history = [{"receipt": {
        "scope_id": store.project.metadata_json["execution_scope_id"],
        "status": "active", "remaining_unknown": 0, "reconciliation_id": "original-proof",
    }}]
    store.project.metadata_json = {
        **store.project.metadata_json, "production_paused": True,
        "production_pause_reason": "execution_reconciled_manual_resume_required",
        "execution_reconciliation_history": history,
    }
    await store.session.commit()
    assert await explicit(store)
    project, _ = await read(store)
    assert project.status == "writing"
    assert "production_paused" not in project.metadata_json
    assert project.metadata_json["execution_reconciliation_history"] == history
    assert project.metadata_json["reconciled_pause_resume_receipts"] == [{
        "command_serial": 8, "actor": "web_dashboard", "reconciliation_id": "original-proof",
    }]


@pytest.mark.parametrize("project_pause", [False, True])
async def test_external_pause_after_resume_still_blocks_actual_knowledge_resume(
    store, project_pause,
):
    await arrange(store)
    assert await explicit(store)
    await store.session.refresh(store.project)
    assert store.project.status == "writing"
    async with store.factory() as operator:
        await request_pause(operator, store.project.id, actor="external", reason="new pause")
        if project_pause:
            project = await operator.get(ProjectModel, store.project.id)
            project.status = "paused"
        await operator.commit()
    assert store.project.status == "writing", "the caller still holds a stale ORM row"
    with pytest.raises(ValueError, match="adopted_knowledge_halted"):
        await resume(store.configured)
    project, control = await read(store)
    assert control.desired_state == "pause" and control.command_serial == 9
    assert project.status == ("paused" if project_pause else "writing")
    assert not list(await store.session.scalars(select(WorkflowRunModel)))


async def test_pause_racing_observation_and_lock_is_not_overwritten(store, monkeypatch):
    await arrange(store)
    original = web_resume._control_row
    injected = False

    async def raced(session, project_id, *, lock):
        nonlocal injected
        if lock and not injected:
            injected = True
            # Release SQLite's read transaction, as real provider boundaries do.
            # SQLite ignores FOR UPDATE: this tests generation freshness, not PG locking.
            await session.commit()
            async with store.factory() as external:
                await request_pause(external, project_id, actor="external", reason="racing pause")
                await external.commit()
        return await original(session, project_id, lock=lock)

    monkeypatch.setattr(web_resume, "_control_row", raced)
    # The race needs a transaction managed like the production session_scope,
    # which permits a commit at a boundary (rather than an outer begin block).
    @asynccontextmanager
    async def scope(_settings):
        async with store.factory() as session:
            yield session
            await session.commit()

    monkeypatch.setattr(server, "session_scope", scope)
    with pytest.raises(web_resume.WebProjectResumeBlockedError, match="command_changed"):
        await explicit(store)
    project, control = await read(store)
    assert project.status == "paused" and control.desired_state == "pause"
    assert control.command_serial == 8 and control.requested_by == "external"
    assert control.detail_json["web_resume_status_history"] == [{"old": True}]


async def test_generic_request_run_does_not_repair_status(store):
    await arrange(store, intent="pause")
    await request_run(store.session, store.project.id, actor="other caller")
    await store.session.commit()
    assert (await read(store))[0].status == "paused"


async def test_new_pause_row_after_empty_control_lock_is_not_overwritten(store, monkeypatch):
    store.project.status = "paused"
    await store.session.commit()
    original = web_resume._control_row
    injected = False

    async def raced(session, project_id, *, lock):
        nonlocal injected
        row = await original(session, project_id, lock=lock)
        if lock and not injected:
            injected = True
            assert row is None
            await session.commit()
            async with store.factory() as external:
                await request_pause(external, project_id, actor="external", reason="new control")
                await external.commit()
        return row

    monkeypatch.setattr(web_resume, "_control_row", raced)
    async with store.factory() as session:
        with pytest.raises(web_resume.WebProjectResumeBlockedError, match="command_changed"):
            await web_resume.request_explicit_web_resume(
                session, slug=store.project.slug, actor="web_dashboard", reason="explicit retry",
            )
    project, control = await read(store)
    assert project.status == "paused" and control.desired_state == "pause"
    assert control.command_serial == 1 and not control.detail_json


async def test_project_pause_racing_lock_is_not_reinterpreted_as_old_residue(store, monkeypatch):
    await arrange(store)
    store.project.status = "writing"
    await store.session.commit()
    original = web_resume._control_row
    injected = False

    async def raced(session, project_id, *, lock):
        nonlocal injected
        if lock and not injected:
            injected = True
            await session.commit()
            async with store.factory() as external:
                project = await external.get(ProjectModel, project_id)
                project.status = "paused"
                await external.commit()
        return await original(session, project_id, lock=lock)

    monkeypatch.setattr(web_resume, "_control_row", raced)
    async with store.factory() as session:
        with pytest.raises(web_resume.WebProjectResumeBlockedError, match="project_changed"):
            await web_resume.request_explicit_web_resume(
                session, slug=store.project.slug, actor="web_dashboard", reason="explicit retry",
            )
    project, control = await read(store)
    assert project.status == "paused" and control.command_serial == 7


@pytest.mark.parametrize("status", ["planning", "writing"])
async def test_first_explicit_run_persists_native_run1_and_second_request_is_noop(store, status):
    store.project.status = status
    store.session.add(ExecutionBudgetModel(
        scope_id=store.project.metadata_json["execution_scope_id"], status="active",
        calls_used=9, tokens_charged=1234, max_calls=10, max_tokens=9999,
        deadline_at=datetime.now(UTC) + timedelta(days=1), metadata_json={"retained": None},
    ))
    await store.session.commit()
    assert (await read(store))[1] is None
    metadata = deepcopy(store.project.metadata_json)
    before = await protected(store)
    assert await explicit(store) is True  # Actual dashboard service, normal native request_run.
    project, control = await read(store)
    assert project.status == status and project.metadata_json == metadata
    assert control.desired_state == "run" and control.command_serial == 1
    assert control.requested_by == "web_dashboard" and control.requested_at is not None
    assert control.state_reason == "operator started writing"
    assert control.resume_pending is False
    history = control.detail_json["web_resume_status_history"]
    assert len(history) == 1 and history[0]["previous_control"] is None
    assert history[0]["from_status"] == history[0]["to_status"] == status
    assert history[0]["phase_evidence"] == {}
    assert await protected(store) == before
    async with store.factory() as session:
        control_before = dict((await session.execute(
            select(*BookProductionControlModel.__table__.columns),
        )).mappings().one())
    assert await explicit(store) is False
    async with store.factory() as session:
        assert dict((await session.execute(
            select(*BookProductionControlModel.__table__.columns),
        )).mappings().one()) == control_before
    assert await protected(store) == before


async def test_first_explicit_run_remains_in_callers_rollback_boundary(store):
    assert (await read(store))[1] is None
    before = await protected(store)
    async with store.factory() as session:
        assert await web_resume.request_explicit_web_resume(
            session, slug=store.project.slug, actor="web_dashboard", reason="explicit first RUN",
        )
        control = await session.get(BookProductionControlModel, store.project.id)
        assert control.desired_state == "run" and control.command_serial == 1
        await session.rollback()
    project, control = await read(store)
    assert control is None and project.status == "writing"
    assert await protected(store) == before


@pytest.mark.parametrize("boundary", ["before_empty_lock", "after_empty_lock"])
async def test_first_run_does_not_overwrite_stop_created_during_empty_control_lock(
    store, monkeypatch, boundary,
):
    assert (await read(store))[1] is None
    before = await protected(store)
    original = web_resume._control_row
    injected = False

    async def stop_in_other_session(session, project_id):
        await session.commit()  # SQLite ignores FOR UPDATE; test actual command freshness.
        async with store.factory() as external:
            await request_stop(external, project_id, actor="external", reason="concurrent STOP")
            await external.commit()

    async def raced(session, project_id, *, lock):
        nonlocal injected
        if lock and not injected:
            injected = True
            if boundary == "before_empty_lock":
                await stop_in_other_session(session, project_id)
                return await original(session, project_id, lock=lock)
            row = await original(session, project_id, lock=lock)
            assert row is None
            await stop_in_other_session(session, project_id)
            return row
        return await original(session, project_id, lock=lock)

    # Inject only timing; every read and STOP write still uses the real SQL service.
    monkeypatch.setattr(web_resume, "_control_row", raced)
    async with store.factory() as session:
        with pytest.raises(web_resume.WebProjectResumeBlockedError, match="command_changed"):
            await web_resume.request_explicit_web_resume(
                session, slug=store.project.slug, actor="web_dashboard", reason="first RUN",
            )
    assert injected
    project, control = await read(store)
    assert project.status == "writing" and control.desired_state == "stop"
    assert control.command_serial == 1 and control.requested_by == "external"
    assert control.state_reason == "concurrent STOP" and not control.detail_json
    assert await protected(store) == before


async def test_stop_after_first_run_invalidates_run1_and_blocks_native_knowledge_resume(store):
    assert (await read(store))[1] is None
    assert await explicit(store)
    assert (await read(store))[1].command_serial == 1
    before = await protected(store)
    async with store.factory() as external:
        await request_stop(external, store.project.id, actor="external", reason="later STOP")
        await external.commit()
    async with store.factory() as observer:
        state = await load_control_state(observer, store.project.id)
        assert state.command_serial == 2 and not auto_recovery_is_permitted(state)
    with pytest.raises(ValueError, match="adopted_knowledge_halted"):
        await resume(store.configured)
    project, control = await read(store)
    assert project.status == "writing" and control.desired_state == "stop"
    assert control.command_serial == 2 and control.requested_by == "external"
    assert len(control.detail_json["web_resume_status_history"]) == 1
    assert await protected(store) == before
