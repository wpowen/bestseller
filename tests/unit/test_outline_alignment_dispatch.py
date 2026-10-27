"""Durable original-scope dispatch and new-body handoff, using real SQLite rows."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionBudgetModel,
    ProjectModel,
    RewriteTaskModel,
    WorkflowRunModel,
)
from bestseller.services import reviews
from bestseller.services import rewrite_outline_binding as binding
from bestseller.services import rewrite_outline_dispatch as dispatcher_service
from bestseller.services.budget import bind_execution_scope, current_execution_scope
from bestseller.services.rewrite_patch import text_hash
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def setup(factory):
    async with factory() as session:
        case = await seed(session)
        scope = f"project:{case.project.id}"
        case.project.metadata_json = {**case.project.metadata_json, "execution_scope_id": scope}
        admission = WorkflowRunModel(
            id=UUID(case.current["workflow_run_id"]),
            project_id=case.project.id,
            workflow_type="outline_hard_admission",
            status="completed",
            scope_type="project",
            scope_id=case.project.id,
            requested_by="fixture",
            metadata_json={"execution_scope_id": scope},
        )
        budget = ExecutionBudgetModel(
            scope_id=scope,
            max_calls=13,
            max_tokens=250000,
            calls_used=7,
            tokens_charged=12000,
            deadline_at=datetime.now(UTC) + timedelta(hours=1),
            status="active",
            metadata_json={"original": "preserved"},
        )
        session.add_all([admission, budget])
        await session.commit()
        staged = await binding.stage_outline_revision_rewrite_tasks(session, **migration_args(case))
        await session.commit()
        case.alignment_id = UUID(staged["replacement_task_id"])
        case.scope = scope
        return case


def args(case):
    return dict(
        project_id=case.project.id,
        chapter_id=case.chapter.id,
        expected_draft_id=case.draft.id,
        expected_body_sha256=text_hash(case.draft.content_md),
        expected_plan_binding=case.current,
        original_execution_scope_id=case.scope,
    )


async def prepare(dispatcher, factory, case):
    async with factory() as session:
        result = await dispatcher.prepare_outline_alignment_dispatch(session, **args(case))
        await session.commit()
        return result


def successful_rewrite(factory, calls):

    async def rewrite(session, slug, number, *, rewrite_task_id, workflow_run_id, **kwargs):
        async with factory() as observer:
            saved = await observer.get(WorkflowRunModel, workflow_run_id)
            assert saved.status == "running"
            assert saved.metadata_json["source_alignment_dispatch"]["claim_id"]
            assert saved.metadata_json["execution_scope_id"] == current_execution_scope()
            before = await observer.get(RewriteTaskModel, rewrite_task_id)
            assert before.status == "pending" and before.attempts == 0
        calls.append((workflow_run_id, current_execution_scope()))
        task = await session.get(RewriteTaskModel, rewrite_task_id)
        old = await session.get(
            ChapterDraftVersionModel, UUID(task.metadata_json["parent_draft_id"])
        )
        old.is_current = False
        await session.flush()
        new = ChapterDraftVersionModel(
            project_id=task.project_id,
            chapter_id=old.chapter_id,
            version_no=2,
            content_md="A complete changed map scene aligned with the admitted outline.\r\n",
            is_current=True,
            promotion_state="under_review",
        )
        session.add(new)
        await session.flush()
        task.status = "completed"
        task.attempts += 1
        task.metadata_json = {**task.metadata_json, "rewritten_chapter_draft_id": str(new.id)}
        return (new, task)

    return rewrite


async def test_stable_preparation_claim_before_rewrite_and_strict_new_body_handoff(dispatch_db):
    factory, dispatcher = (dispatch_db, dispatcher_service)
    case = await setup(factory)
    with bind_execution_scope(case.scope):
        async with factory() as session:
            prepared = await dispatcher.prepare_outline_alignment_dispatch(session, **args(case))
            repeated = await dispatcher.prepare_outline_alignment_dispatch(session, **args(case))
            assert prepared == repeated and prepared.state == "prepared"
            async with factory() as observer:
                assert await observer.get(WorkflowRunModel, prepared.workflow_run_id) is None
            await session.commit()
        again = await prepare(dispatcher, factory, case)
        assert again.workflow_run_id == prepared.workflow_run_id
        calls = []
        result = await dispatcher.execute_outline_alignment_dispatch(
            factory,
            load_settings(env={}),
            workflow_run_id=prepared.workflow_run_id,
            rewrite_executor=successful_rewrite(factory, calls),
        )
        assert result.state == "review_ready" and len(calls) == 1
        assert result.review_source_binding["draft_id"] != str(case.draft.id)
        replay = await dispatcher.execute_outline_alignment_dispatch(
            factory,
            load_settings(env={}),
            workflow_run_id=prepared.workflow_run_id,
            rewrite_executor=successful_rewrite(factory, calls),
        )
        assert replay == result and len(calls) == 1
        async with factory() as session:
            new = await session.get(
                ChapterDraftVersionModel, UUID(result.review_source_binding["draft_id"])
            )
            current_args = {
                **args(case),
                "expected_draft_id": new.id,
                "expected_body_sha256": text_hash(new.content_md),
            }
            resumed = await dispatcher.prepare_outline_alignment_dispatch(session, **current_args)
            assert resumed == result
            budget = await session.get(ExecutionBudgetModel, case.scope)
            assert budget.calls_used == 7 and budget.tokens_charged == 12000
            assert budget.max_calls == 13 and budget.metadata_json == {"original": "preserved"}
            assert await session.scalar(select(func.count()).select_from(WorkflowRunModel)) == 2
            assert (await session.get(RewriteTaskModel, case.alignment_id)).attempts == 1


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "superseded", "stale"])
async def test_terminal_same_body_task_cannot_dispatch_or_create_workflow(dispatch_db, status):
    factory, dispatcher = (dispatch_db, dispatcher_service)
    case = await setup(factory)
    async with factory() as session:
        task = await session.get(RewriteTaskModel, case.alignment_id)
        task.status = status
        task.attempts = 4
        await session.commit()
    with bind_execution_scope(case.scope):
        result = await prepare(dispatcher, factory, case)
    assert result.state == "blocked"
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowRunModel)) == 1
        task = await session.get(RewriteTaskModel, case.alignment_id)
        assert task.status == status and task.attempts == 4


@pytest.mark.parametrize("mode", ["failed", "pending", "same_bytes", "exception"])
async def test_failed_unchanged_or_unknown_rewrite_preserves_old_body_and_never_resends(
    dispatch_db, mode
):
    factory, dispatcher = (dispatch_db, dispatcher_service)
    case = await setup(factory)
    calls = []

    async def unsuccessful(session, *a, rewrite_task_id, **kw):
        calls.append(True)
        task = await session.get(RewriteTaskModel, rewrite_task_id)
        draft = await session.get(ChapterDraftVersionModel, case.draft.id)
        if mode == "exception":
            raise RuntimeError("offline unknown result")
        if mode == "failed":
            task.status = "failed"
            task.attempts = 1
        if mode == "same_bytes":
            task.status = "completed"
            task.attempts = 1
            task.metadata_json = {**task.metadata_json, "rewritten_chapter_draft_id": str(draft.id)}
        return (draft, task)

    with bind_execution_scope(case.scope):
        prepared = await prepare(dispatcher, factory, case)
        if mode == "exception":
            with pytest.raises(RuntimeError):
                await dispatcher.execute_outline_alignment_dispatch(
                    factory,
                    load_settings(env={}),
                    workflow_run_id=prepared.workflow_run_id,
                    rewrite_executor=unsuccessful,
                )
        else:
            result = await dispatcher.execute_outline_alignment_dispatch(
                factory,
                load_settings(env={}),
                workflow_run_id=prepared.workflow_run_id,
                rewrite_executor=unsuccessful,
            )
            assert result.state == "blocked"
        assert (await prepare(dispatcher, factory, case)).state == "blocked"
        second = await dispatcher.execute_outline_alignment_dispatch(
            factory,
            load_settings(env={}),
            workflow_run_id=prepared.workflow_run_id,
            rewrite_executor=unsuccessful,
        )
        assert second.state == "blocked" and len(calls) == 1
    async with factory() as session:
        current = await session.get(ChapterDraftVersionModel, case.draft.id)
        assert current.is_current and current.content_md == case.draft.content_md
        task = await session.get(RewriteTaskModel, case.alignment_id)
        expected_status = (
            mode
            if mode in {"pending", "failed"}
            else "pending"
            if mode == "exception"
            else "completed"
        )
        assert task.status == expected_status


async def test_running_claim_is_inflight_and_cannot_be_retried(dispatch_db):
    factory, dispatcher = (dispatch_db, dispatcher_service)
    case = await setup(factory)
    with bind_execution_scope(case.scope):
        prepared = await prepare(dispatcher, factory, case)
        async with factory() as session:
            workflow = await session.get(WorkflowRunModel, prepared.workflow_run_id)
            receipt = deepcopy(workflow.metadata_json["source_alignment_dispatch"])
            receipt["claim_id"] = str(uuid4())
            workflow.metadata_json = {
                **workflow.metadata_json,
                "source_alignment_dispatch": receipt,
            }
            workflow.status = "running"
            await session.commit()
        assert (await prepare(dispatcher, factory, case)).state == "inflight"
        result = await dispatcher.execute_outline_alignment_dispatch(
            factory, load_settings(env={}), workflow_run_id=prepared.workflow_run_id
        )
        assert result.state == "inflight"


@pytest.mark.parametrize("changed", ["marker", "body", "scope", "closed_budget", "admission_scope"])
async def test_selected_source_and_original_scope_are_rechecked(dispatch_db, changed):
    factory, dispatcher = (dispatch_db, dispatcher_service)
    case = await setup(factory)
    with bind_execution_scope(case.scope):
        prepared = await prepare(dispatcher, factory, case)
        async with factory() as session:
            if changed == "marker":
                chapter = await session.get(ChapterModel, case.chapter.id)
                chapter.metadata_json = {
                    **chapter.metadata_json,
                    "outline_source_admission": {**case.current, "artifact_id": str(uuid4())},
                }
            elif changed == "body":
                await session.execute(
                    update(ChapterDraftVersionModel)
                    .where(ChapterDraftVersionModel.id == case.draft.id)
                    .values(content_md="Different body with retained ID")
                )
            elif changed == "closed_budget":
                budget = await session.get(ExecutionBudgetModel, case.scope)
                budget.status = "cancelled"
            elif changed == "admission_scope":
                admission = await session.get(
                    WorkflowRunModel, UUID(case.current["workflow_run_id"])
                )
                admission.metadata_json = {"execution_scope_id": "unrelated"}
            await session.commit()
        with bind_execution_scope("unrelated" if changed == "scope" else case.scope):
            if changed == "body":
                result = await dispatcher.execute_outline_alignment_dispatch(
                    factory, load_settings(env={}), workflow_run_id=prepared.workflow_run_id
                )
                assert result.state == "blocked"
            else:
                with pytest.raises(ValueError):
                    await dispatcher.execute_outline_alignment_dispatch(
                        factory, load_settings(env={}), workflow_run_id=prepared.workflow_run_id
                    )


async def test_normal_entry_without_alignment_does_not_require_retained_body_or_scope(dispatch_db):
    async with dispatch_db() as session:
        result = await dispatcher_service.prepare_outline_alignment_dispatch(
            session,
            project_id=uuid4(),
            chapter_id=uuid4(),
            expected_draft_id=None,
            expected_body_sha256=None,
            expected_plan_binding=None,
            original_execution_scope_id=None,
        )
        assert result.state == "not_applicable"


async def test_pg_compiled_long_transaction_has_no_pre_provider_row_locks(dispatch_db):
    from collections import defaultdict

    from sqlalchemy import event
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.orm import Session

    factory, dispatcher = (dispatch_db, dispatcher_service)
    case = await setup(factory)
    statements = defaultdict(list)
    long_sessions = []

    def observe(state):
        if state.is_select:
            statements[state.session].append(
                str(state.statement.compile(dialect=postgresql.dialect()))
            )

    async def gateway(session, *a, **kw):
        prior = list(statements[session.sync_session])
        long_sessions.append((session.sync_session, len(prior)))
        assert prior
        assert all("FOR UPDATE" not in sql for sql in prior), prior
        return await successful_rewrite(factory, [])(session, *a, **kw)

    event.listen(Session, "do_orm_execute", observe)
    try:
        with bind_execution_scope(case.scope):
            prepared = await prepare(dispatcher, factory, case)
            result = await dispatcher.execute_outline_alignment_dispatch(
                factory,
                load_settings(env={}),
                workflow_run_id=prepared.workflow_run_id,
                rewrite_executor=gateway,
            )
        assert result.state == "review_ready"
        session, boundary = long_sessions[0]
        post = statements[session][boundary:]
        assert any("workflow_runs" in sql and "FOR UPDATE" in sql for sql in post)
        assert any("chapters" in sql and "FOR UPDATE" in sql for sql in post)
    finally:
        event.remove(Session, "do_orm_execute", observe)


async def test_fresh_actual_proof_api_is_rechecked_in_isolated_rewrite_session(
    dispatch_db, monkeypatch
):
    from bestseller.services import outline_source_entry

    factory, dispatcher = (dispatch_db, dispatcher_service)
    case = await setup(factory)
    verifier = outline_source_entry.require_current_outline_source_admission
    phases = []

    async def changed(session, settings, **kwargs):
        workflow = await session.get(WorkflowRunModel, dispatcher._workflow_id(case.alignment_id))
        phases.append(workflow.status if workflow else "prepare")
        if workflow is not None and workflow.status == "running":
            raise ValueError("canonical_or_foundation_changed_after_claim")
        return await verifier(session, settings, **kwargs)

    monkeypatch.setattr(outline_source_entry, "require_current_outline_source_admission", changed)
    with bind_execution_scope(case.scope):
        prepared = await prepare(dispatcher, factory, case)
        with pytest.raises(ValueError, match="changed_after_claim"):
            await dispatcher.execute_outline_alignment_dispatch(
                factory, load_settings(env={}), workflow_run_id=prepared.workflow_run_id
            )
    assert phases == ["prepare", "pending", "running"]
    async with factory() as session:
        workflow = await session.get(WorkflowRunModel, prepared.workflow_run_id)
        task = await session.get(RewriteTaskModel, case.alignment_id)
        assert workflow.status == "machine_blocked"
        assert task.status == "pending" and task.attempts == 0


async def test_deadline_crossing_does_not_erase_returned_failed_evidence(dispatch_db):
    factory, dispatcher = (dispatch_db, dispatcher_service)
    case = await setup(factory)

    async def failed(session, *a, rewrite_task_id, **kw):
        task = await session.get(RewriteTaskModel, rewrite_task_id)
        task.status = "failed"
        task.attempts = 1
        task.metadata_json = {**task.metadata_json, "candidate_evidence": {"body": "retained"}}
        await session.execute(
            update(ExecutionBudgetModel)
            .where(ExecutionBudgetModel.scope_id == case.scope)
            .values(deadline_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        return (await session.get(ChapterDraftVersionModel, case.draft.id), task)

    with bind_execution_scope(case.scope):
        prepared = await prepare(dispatcher, factory, case)
        result = await dispatcher.execute_outline_alignment_dispatch(
            factory,
            load_settings(env={}),
            workflow_run_id=prepared.workflow_run_id,
            rewrite_executor=failed,
        )
        assert result.state == "blocked"
    async with factory() as session:
        task = await session.get(RewriteTaskModel, case.alignment_id)
        assert task.status == "failed" and task.attempts == 1
        assert task.metadata_json["candidate_evidence"] == {"body": "retained"}


async def test_historical_output_cannot_intercept_new_selected_marker_pending_task(dispatch_db):
    factory, dispatcher = (dispatch_db, dispatcher_service)
    case = await setup(factory)
    with bind_execution_scope(case.scope):
        prepared = await prepare(dispatcher, factory, case)
        done = await dispatcher.execute_outline_alignment_dispatch(
            factory,
            load_settings(env={}),
            workflow_run_id=prepared.workflow_run_id,
            rewrite_executor=successful_rewrite(factory, []),
        )
        async with factory() as session:
            case.draft = await session.get(
                ChapterDraftVersionModel, UUID(done.review_source_binding["draft_id"])
            )
            case.chapter = await session.get(ChapterModel, case.chapter.id)
            case.current = {
                **case.current,
                "artifact_id": str(uuid4()),
                "workflow_run_id": str(uuid4()),
                "projection_hash": text_hash("new outline"),
            }
            case.chapter.metadata_json = {
                **case.chapter.metadata_json,
                "outline_source_admission": case.current,
            }
            session.add(
                WorkflowRunModel(
                    id=UUID(case.current["workflow_run_id"]),
                    project_id=case.project.id,
                    workflow_type="outline_hard_admission",
                    status="completed",
                    metadata_json={"execution_scope_id": case.scope},
                )
            )
            await session.commit()
            staged = await binding.stage_outline_revision_rewrite_tasks(
                session, **migration_args(case)
            )
            await session.commit()
            new_task_id = UUID(staged["replacement_task_id"])
        result = await prepare(dispatcher, factory, case)
        assert result.state == "prepared" and result.task_id == new_task_id


@pytest.mark.parametrize(
    "kind", ["available_review", "waiting_infrastructure", "consume_retained_unknown"]
)
async def test_completed_output_with_valid_retained_preflight_returns_to_normal_routing(
    dispatch_db, monkeypatch, kind
):
    from bestseller.services import chapter_review_preflight

    factory, dispatcher = (dispatch_db, dispatcher_service)
    case = await setup(factory)
    with bind_execution_scope(case.scope):
        prepared = await prepare(dispatcher, factory, case)
        done = await dispatcher.execute_outline_alignment_dispatch(
            factory,
            load_settings(env={}),
            workflow_run_id=prepared.workflow_run_id,
            rewrite_executor=successful_rewrite(factory, []),
        )

        async def valid(*a, **kw):
            return {
                "reuse_kind": kind,
                "project_id": str(case.project.id),
                "chapter_id": str(case.chapter.id),
                "draft_id": done.review_source_binding["draft_id"],
                "body_sha256": done.review_source_binding["body_hash"],
            }

        monkeypatch.setattr(
            chapter_review_preflight, "validate_retained_review_before_processing", valid
        )
        async with factory() as session:
            case.draft = await session.get(
                ChapterDraftVersionModel, UUID(done.review_source_binding["draft_id"])
            )
        result = await prepare(dispatcher, factory, case)
        assert result.state == "not_applicable" and result.review_source_binding is None


async def test_normal_rewrite_descendant_of_completed_alignment_is_not_applicable(dispatch_db):
    factory, dispatcher = (dispatch_db, dispatcher_service)
    case = await setup(factory)
    with bind_execution_scope(case.scope):
        prepared = await prepare(dispatcher, factory, case)
        done = await dispatcher.execute_outline_alignment_dispatch(
            factory,
            load_settings(env={}),
            workflow_run_id=prepared.workflow_run_id,
            rewrite_executor=successful_rewrite(factory, []),
        )
        async with factory() as session:
            old = await session.get(
                ChapterDraftVersionModel, UUID(done.review_source_binding["draft_id"])
            )
            old.is_current = False
            await session.flush()
            case.draft = ChapterDraftVersionModel(
                project_id=case.project.id,
                chapter_id=case.chapter.id,
                version_no=3,
                is_current=True,
                content_md="A later normal targeted rewrite with its own review pending.",
            )
            session.add(case.draft)
            await session.commit()
        assert (await prepare(dispatcher, factory, case)).state == "not_applicable"


def marker():
    return {
        "artifact_id": str(uuid4()),
        "workflow_run_id": str(uuid4()),
        "projection_hash": text_hash("current approved outline projection"),
        "source_content_sha256": text_hash("complete approved source content"),
    }


async def seed(session, *, marked=True, bound=False):
    project = ProjectModel(
        id=uuid4(),
        slug=f"outline-test-{uuid4().hex}",
        title="Independent transport diary",
        genre="travel",
        language="en",
        target_word_count=8000,
        target_chapters=4,
        metadata_json={"global_ledger": {"attempts_used": 17}},
    )
    current = marker()
    chapter = ChapterModel(
        id=uuid4(),
        project_id=project.id,
        chapter_number=1,
        chapter_goal="Return the map",
        production_state="blocked",
        status="revision",
        revision_count=6,
        metadata_json={
            **({"outline_source_admission": current} if marked else {}),
            "outline_prefix_readiness": {"artifact_id": "legacy prefix is not authority"},
            "auto_repair_total_attempts": 8,
            "rewrite_history": [{"version": 1, "block_codes": ["ORIGINAL"]}],
            "rewrite_escalation_failures": 2,
        },
    )
    draft = ChapterDraftVersionModel(
        id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="A cartographer compared the map with the road before returning it.",
        word_count=13,
        is_current=True,
        promotion_state="under_review",
    )
    task = RewriteTaskModel(
        id=uuid4(),
        project_id=project.id,
        trigger_type="chapter_review",
        trigger_source_id=chapter.id,
        rewrite_strategy="chapter_coherence_bridge_rewrite",
        status="pending",
        priority=3,
        instructions="FULL ORIGINAL OBSOLETE INSTRUCTIONS",
        attempts=4,
        error_log="FULL ORIGINAL ERROR",
        context_required=["original_context"],
        metadata_json={
            **reviews.rewrite_source_binding(draft),
            **({"source_plan_binding": deepcopy(current)} if bound else {}),
            "raw_candidate_receipts": [{"body": "retained rejected candidate", "attempt": 4}],
            "attempt_budget": 5,
            "supersession_receipts": [{"prior": "history"}],
        },
    )
    session.add_all([project, chapter, draft, task])
    await session.commit()
    return SimpleNamespace(
        project=project, chapter=chapter, draft=draft, task=task, current=current, session=session
    )


def migration_args(case):
    return dict(
        project_id=case.project.id,
        chapter_id=case.chapter.id,
        draft_id=case.draft.id,
        expected_body_sha256=text_hash(case.draft.content_md),
        expected_plan_binding=deepcopy(case.current),
    )


@pytest.fixture(autouse=True)
def guard_dispatch_boundaries(monkeypatch):
    import socket

    from bestseller.services import llm, reviews

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Dispatcher tests forbid PG/provider/network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(reviews, "complete_text", forbidden)
    from bestseller.services import outline_source_entry

    async def proof(_session, _settings, *, project, chapter, **kwargs):
        # This suite exercises dispatch sequencing, not the parent's full grade
        # verifier. The production helper always calls that real query-only API.
        marker = chapter.metadata_json["outline_source_admission"]
        return {
            "workflow_run_id": marker["workflow_run_id"],
            "source_content_sha256": marker["source_content_sha256"],
            "source_plan_projection": marker["projection_hash"],
        }

    monkeypatch.setattr(outline_source_entry, "require_current_outline_source_admission", proof)


@pytest.fixture
async def dispatch_db(tmp_path):
    from sqlalchemy import JSON, MetaData
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.schema import CreateTable

    from bestseller.infra.db.models import (
        BookProductionControlModel,
        ChapterDraftVersionModel,
        ChapterModel,
        ExecutionBudgetModel,
        ProjectModel,
        ReviewReportModel,
        RewriteTaskModel,
        WorkflowRunModel,
    )

    path = tmp_path / "outline_alignment_dispatch_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    def ddl(connection):
        for model in (
            BookProductionControlModel,
            ExecutionBudgetModel,
            WorkflowRunModel,
            ProjectModel,
            ChapterModel,
            ChapterDraftVersionModel,
            ReviewReportModel,
            RewriteTaskModel,
        ):
            table = model.__table__.to_metadata(MetaData())
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                if column.server_default and "::jsonb" in str(column.server_default.arg):
                    column.server_default = None
            connection.execute(CreateTable(table, include_foreign_key_constraints=[]))
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX dispatch_test_current "
            "ON chapter_draft_versions(chapter_id) WHERE is_current = 1"
        )

    async with engine.begin() as connection:
        await connection.run_sync(ddl)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
