"""Unsent durable claims can recover; paid/unknown/history never gain credit."""

from copy import deepcopy
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import JSON, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from bestseller.infra.db import models as db
from bestseller.services import rewrite_outline_dispatch as dispatch
from bestseller.services import rewrite_outline_dispatch_recovery as recovery
from bestseller.services.budget import bind_execution_scope
from bestseller.services.prompt_source_policy import content_hash
from bestseller.settings import load_settings
from test_outline_alignment_dispatch import (
    guard_dispatch_boundaries,  # noqa: F401 -- Imported autouse offline boundary fixture.
    setup, prepare, successful_rewrite,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def recovery_db(tmp_path):
    path = tmp_path / "alignment_unsent_recovery_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    def ddl(connection):
        for model in (db.BookProductionControlModel, db.ExecutionBudgetModel,
            db.ExecutionOperationModel, db.ExecutionAttemptModel, db.WorkflowRunModel,
            db.ProjectModel, db.ChapterModel, db.ChapterDraftVersionModel,
            db.ReviewReportModel, db.RewriteTaskModel, db.SceneCardModel, db.LlmRunModel):
            table = model.__table__.to_metadata(MetaData())
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                if column.server_default and "::jsonb" in str(column.server_default.arg):
                    column.server_default = None
            connection.execute(CreateTable(table, include_foreign_key_constraints=[]))

    async with engine.begin() as connection:
        await connection.run_sync(ddl)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def failed_case(factory):
    case = await setup(factory)
    old = datetime.now(UTC) - timedelta(hours=1)
    async with factory() as session:
        for i in range(7):
            key = f"old-root-{i}"
            session.add(db.ExecutionOperationModel(key=key, scope_id=case.scope,
                input_hash="a" * 64, status="completed", owner="old-dispatcher",
                lease_until=old, result_json={"historical": i}))
            session.add(db.ExecutionAttemptModel(id=f"old-attempt-{i}", operation_key=key,
                scope_id=case.scope, model_name="offline-model", input_hash="a" * 64,
                reserved_tokens=6000, actual_tokens=1000 if i < 6 else 6000,
                status="completed", created_at=old))
        session.add(db.SceneCardModel(project_id=case.project.id, chapter_id=case.chapter.id,
            scene_number=1, scene_type="", status="planned", purpose={"story": "Inspect a map"},
            target_word_count=600))
        await session.commit()

    async def pre_provider_error(*args, **kwargs):
        raise ValueError("offline deterministic source validation failed")

    with bind_execution_scope(case.scope):
        prepared = await prepare(dispatch, factory, case)
        with pytest.raises(ValueError, match="deterministic source"):
            await dispatch.execute_outline_alignment_dispatch(factory, load_settings(env={}),
                workflow_run_id=prepared.workflow_run_id, rewrite_executor=pre_provider_error)
    case.workflow_id = prepared.workflow_run_id
    async with factory() as session:
        w = await session.get(db.WorkflowRunModel, case.workflow_id)
        case.claim_id = w.metadata_json[dispatch._KEY]["claim_id"]
        case.prior_workflow = recovery._row(w)
    return case


def kwargs(case):
    return dict(workflow_run_id=case.workflow_id, expected_claim_id=case.claim_id,
        actor="offline-operator", authorization_ref="explicit continuation",
        repair_ref="verified generic schema repair", explicit_user_approval=True)


async def financial_rows(session, case):
    result = {}
    for model, key in ((db.ExecutionBudgetModel, "scope_id"),
        (db.ExecutionOperationModel, "scope_id"), (db.ExecutionAttemptModel, "scope_id")):
        rows = list(await session.scalars(select(model).where(getattr(model, key) == case.scope)))
        result[model.__tablename__] = sorted(content_hash(recovery._row(row)) for row in rows)
    return result


async def test_plan_is_read_only_and_exact_retirement_preserves_rows_and_original_task(recovery_db):
    factory = recovery_db
    case = await failed_case(factory)
    with bind_execution_scope(case.scope):
        async with factory() as session:
            before = await financial_rows(session, case)
            task_before = recovery._row(await session.get(db.RewriteTaskModel, case.alignment_id))
            p = await recovery.reconcile_unstarted_outline_alignment(session,
                load_settings(env={}), **kwargs(case))
            assert p["status"] == "ready" and not p["database_writes"]
            assert (await session.get(db.WorkflowRunModel, case.workflow_id)).status == "machine_blocked"
            await session.rollback()
        async with factory() as session:
            result = await recovery.reconcile_unstarted_outline_alignment(session,
                load_settings(env={}), **kwargs(case), execute=True,
                expected_witness_sha256=p["witness_sha256"])
            await session.commit()
            assert result["status"] == "reconciled" and result["provider_dispatches"] == 0
            w = await session.get(db.WorkflowRunModel, case.workflow_id)
            assert w.status == "pending" and w.metadata_json[dispatch._KEY]["claim_id"] is None
            retired = w.metadata_json[recovery.HISTORY][0]
            assert retired["prior_workflow"] == case.prior_workflow
            assert retired["retired_claim_id"] == case.claim_id
            assert not retired["new_grade_or_adoption"] and not retired["new_spend_allowance"]
            assert before == await financial_rows(session, case)
            assert task_before == recovery._row(await session.get(db.RewriteTaskModel, case.alignment_id))
        async with factory() as session:
            repeated = await recovery.reconcile_unstarted_outline_alignment(session,
                load_settings(env={}), **kwargs(case), execute=True,
                expected_witness_sha256=p["witness_sha256"])
            assert repeated["status"] == "already_reconciled" and repeated["database_writes"] == 0
        calls = []
        ready = await prepare(dispatch, factory, case)
        assert ready.workflow_run_id == case.workflow_id and ready.task_id == case.alignment_id
        result = await dispatch.execute_outline_alignment_dispatch(factory, load_settings(env={}),
            workflow_run_id=case.workflow_id, rewrite_executor=successful_rewrite(factory, calls))
        assert result.state == "review_ready" and len(calls) == 1
        again = await dispatch.execute_outline_alignment_dispatch(factory, load_settings(env={}),
            workflow_run_id=case.workflow_id, rewrite_executor=successful_rewrite(factory, calls))
        assert again == result and len(calls) == 1
        async with factory() as session:
            old = await session.get(db.ChapterDraftVersionModel, case.draft.id)
            assert old.content_md == case.draft.content_md
            assert (await session.get(db.RewriteTaskModel, case.alignment_id)).attempts == 1
            assert before == await financial_rows(session, case)


@pytest.mark.parametrize(("mode", "guard"), [
    ("attempt", "attempt_after_preparation"), ("not_sent", "attempt_after_preparation"),
    ("cross_scope_attempt", "attempt_after_preparation"), ("unknown", "scope_not_settled"),
    ("unsupported_attempt", "attempt_status_unsupported"),
    ("count_gap", "financial_inventory_incomplete"), ("token_gap", "financial_inventory_incomplete"),
    ("missing_root", "attempt_root_missing"), ("live_root", "scope_operation_inflight"),
    ("live_workflow", "project_dispatcher_inflight"), ("result", "workflow_has_provider_result"),
    ("unattributed_result", "workflow_has_provider_result"),
    ("operation_evidence", "workflow_has_operation_evidence"),
    ("cross_scope_evidence", "workflow_has_operation_evidence"),
    ("cross_scope_orphan", "global_orphan_chargeable_root"),
    ("orphan_completed", "orphan_chargeable_root"), ("orphan_stopped", "orphan_chargeable_root"),
    ("forged_technical", "technical_receipt_invalid"),
    ("unsupported_operation", "operation_status_unsupported"),
    ("attempt_receipt_parent", "attempt_parent_not_chargeable"),
    ("task_attempt", "task_or_parent_changed"), ("task_terminal", "task_or_parent_changed"),
    ("trigger_changed", "task_or_parent_changed"), ("body_changed", "task_or_parent_changed"),
    ("identity_changed", "task_or_parent_changed"), ("output", "claim_or_output_changed"),
    ("claim_changed", "claim_or_output_changed"),
])
async def test_paid_unknown_unattributed_or_changed_boundaries_never_rearm(recovery_db, mode, guard):
    factory = recovery_db
    case = await failed_case(factory)
    with bind_execution_scope(case.scope):
        async with factory() as session:
            valid = await recovery.reconcile_unstarted_outline_alignment(session,
                load_settings(env={}), **kwargs(case))
    async with factory() as session:
        w = await session.get(db.WorkflowRunModel, case.workflow_id)
        task = await session.get(db.RewriteTaskModel, case.alignment_id)
        budget = await session.get(db.ExecutionBudgetModel, case.scope)
        if mode in {"attempt", "not_sent", "cross_scope_attempt"}:
            session.add(db.ExecutionAttemptModel(id="later",
                scope_id="other-scope" if mode == "cross_scope_attempt" else case.scope,
                operation_key="old-root-0", input_hash="a" * 64, model_name="offline",
                reserved_tokens=10, actual_tokens=0 if mode == "not_sent" else 10,
                status="not_sent" if mode == "not_sent" else "completed", created_at=datetime.now(UTC)))
        elif mode == "unknown":
            (await session.get(db.ExecutionAttemptModel, "old-attempt-0")).status = "unknown"
        elif mode == "unsupported_attempt":
            (await session.get(db.ExecutionAttemptModel, "old-attempt-0")).status = "unrecognized_return"
        elif mode == "count_gap":
            budget.calls_used += 1
        elif mode == "token_gap":
            budget.tokens_charged += 1
        elif mode == "missing_root":
            await session.delete(await session.get(db.ExecutionOperationModel, "old-root-0"))
        elif mode == "live_root":
            (await session.get(db.ExecutionOperationModel, "old-root-0")).status = "pending"
        elif mode == "unsupported_operation":
            (await session.get(db.ExecutionOperationModel, "old-root-0")).status = "unrecognized_settlement"
        elif mode == "attempt_receipt_parent":
            (await session.get(db.ExecutionOperationModel, "old-root-0")).status = "transport_result"
        elif mode == "live_workflow":
            session.add(db.WorkflowRunModel(project_id=case.project.id, workflow_type="other",
                status="running", requested_by="fixture", scope_type="project", scope_id=case.project.id))
        elif mode in {"result", "unattributed_result"}:
            session.add(db.LlmRunModel(project_id=case.project.id,
                workflow_run_id=w.id if mode == "result" else None,
                logical_role="editor", provider="offline", model_name="offline"))
        elif mode == "operation_evidence":
            op = await session.get(db.ExecutionOperationModel, "old-root-0")
            op.result_json = {"metadata": {"paid_call_origin": {"workflow_run_id": str(w.id)}}}
        elif mode in {"orphan_completed", "orphan_stopped", "forged_technical", "cross_scope_evidence", "cross_scope_orphan"}:
            session.add(db.ExecutionOperationModel(key="orphan",
                scope_id="other-scope" if mode.startswith("cross_scope_") else case.scope,
                input_hash="a" * 64, owner="offline", lease_until=datetime.now(UTC),
                status=("stopped" if mode == "orphan_stopped" else "transport_result"
                    if mode == "forged_technical" else "completed"),
                result_json={"workflow_run_id": str(w.id)} if mode == "cross_scope_evidence"
                    else {"unbound": True}))
        elif mode == "task_attempt":
            task.attempts = 1
        elif mode == "task_terminal":
            task.status = "failed"
        elif mode == "trigger_changed":
            task.trigger_source_id = uuid4()
        elif mode == "body_changed":
            (await session.get(db.ChapterDraftVersionModel, case.draft.id)).content_md += " Changed."
        elif mode == "identity_changed":
            task.instructions += " Changed."
        else:
            receipt = deepcopy(w.metadata_json[dispatch._KEY])
            receipt["output" if mode == "output" else "claim_id"] = {} if mode == "output" else str(uuid4())
            w.metadata_json = {**w.metadata_json, dispatch._KEY: receipt}
        await session.commit()
    for execute in (False, True):
        with bind_execution_scope(case.scope), pytest.raises(ValueError) as raised:
            async with factory() as session:
                await recovery.reconcile_unstarted_outline_alignment(session,
                    load_settings(env={}), **kwargs(case), execute=execute,
                    expected_witness_sha256=valid["witness_sha256"] if execute else None)
        assert str(raised.value) == "alignment_unsent_recovery:" + guard
    async with factory() as session:
        w = await session.get(db.WorkflowRunModel, case.workflow_id)
        assert w.status == "machine_blocked" and not w.metadata_json.get(recovery.HISTORY)


async def test_historical_charged_unknown_stays_charged_with_original_evidence(recovery_db):
    case = await failed_case(recovery_db)
    async with recovery_db() as session:
        attempt = await session.get(db.ExecutionAttemptModel, "old-attempt-0")
        attempt.status, attempt.actual_tokens = "reconciled_charged", None
        root = await session.get(db.ExecutionOperationModel, attempt.operation_key)
        root.status, root.result_json = "stopped", None
        budget = await session.get(db.ExecutionBudgetModel, case.scope)
        budget.tokens_charged += 5000
        await session.commit()
        before = await financial_rows(session, case)
    with bind_execution_scope(case.scope):
        async with recovery_db() as session:
            p = await recovery.reconcile_unstarted_outline_alignment(session,
                load_settings(env={}), **kwargs(case))
        async with recovery_db() as session:
            await recovery.reconcile_unstarted_outline_alignment(session,
                load_settings(env={}), **kwargs(case), execute=True,
                expected_witness_sha256=p["witness_sha256"])
            await session.commit()
            assert before == await financial_rows(session, case)
            old = await session.get(db.ExecutionAttemptModel, "old-attempt-0")
            assert old.status == "reconciled_charged" and old.actual_tokens is None


async def test_exact_preexisting_unbound_paid_run_preserves_null_and_no_review_binding(recovery_db):
    from bestseller.services.llm import LLMCompletionResult
    from bestseller.services.retained_review_input_proof import _physical_return

    case = await failed_case(recovery_db)
    model = "openai/MiniMax-M3.1-Flash"
    async with recovery_db() as session:
        attempt = await session.get(db.ExecutionAttemptModel, "old-attempt-0")
        attempt.model_name = model
        root = await session.get(db.ExecutionOperationModel, attempt.operation_key)
        origin = {"schema": "paid-call-origin.v1", "status": "recorded",
            "project_id": str(case.project.id), "workflow_run_id": None,
            "logical_role": "planner", "prompt_template": None,
            "prompt_version": None, "prompt_hash": attempt.input_hash}
        effective = {"schema": "effective-input.v1", "hash": attempt.input_hash, "model": model}
        paid = LLMCompletionResult(content="An original paid planning observation.",
            provider="openai", model_name=model, finish_reason="stop", input_tokens=400,
            output_tokens=600, metadata={"paid_call_origin": origin, "effective_input_receipt": effective})
        root.result_json = paid.model_dump(mode="json")
        key = hashlib.sha256(f"transport-result.v1\n{case.scope}\n{attempt.id}".encode()).hexdigest()
        session.add(db.ExecutionOperationModel(key=key, scope_id=case.scope,
            input_hash=attempt.input_hash, status="transport_result", owner=root.owner,
            lease_until=root.lease_until, result_json={"schema": "transport-result.v1",
                "attempt_id": attempt.id, "operation_key": root.key,
                "input_hash": attempt.input_hash, "result": paid.model_dump(mode="json")}))
        run = db.LlmRunModel(project_id=case.project.id, workflow_run_id=None, logical_role="planner",
            provider="openai", model_name=model, prompt_hash=attempt.input_hash, finish_reason="stop",
            # Deliberately later: only actual gateway attempts prove send time.
            created_at=datetime.now(UTC), metadata_json={"paid_call_origin": origin,
                "effective_input_receipt": effective, "execution_receipt": {
                    "policy": "execution-budget.v1", "scope_id": case.scope,
                    "operation_key": root.key, "attempts": [{"attempt_id": attempt.id,
                        "status": "completed", "model": model, "reserved_tokens": attempt.reserved_tokens,
                        "effective_input_hash": attempt.input_hash}]}})
        session.add(run)
        await session.commit()
        rid = run.id
        before = await financial_rows(session, case)
        with pytest.raises(ValueError, match="paid_origin_mismatch"):
            await _physical_return(session, run, scope=case.scope,
                expected_hash=run.prompt_hash, expected_model=run.model_name)
    with bind_execution_scope(case.scope):
        async with recovery_db() as session:
            p = await recovery.reconcile_unstarted_outline_alignment(session,
                load_settings(env={}), **kwargs(case))
            proof = p["witness"]["verified_preexisting_unbound_runs"]
            assert len(proof) == 1 and proof[0]["llm_run_id"] == str(rid)
            assert proof[0]["original_workflow_run_id"] is None
        async with recovery_db() as session:
            await recovery.reconcile_unstarted_outline_alignment(session,
                load_settings(env={}), **kwargs(case), execute=True,
                expected_witness_sha256=p["witness_sha256"])
            await session.commit()
            assert before == await financial_rows(session, case)
            assert (await session.get(db.LlmRunModel, rid)).workflow_run_id is None


@pytest.mark.parametrize("state", ["cancelled", "active", "unknown", "cancelled_bad_count"])
async def test_other_cancelled_unstarted_admission_keeps_unknown_purpose_and_all_charges(recovery_db, state):
    case = await failed_case(recovery_db)
    old_scope = "separate-old-scope"
    old_time = datetime.now(UTC) - timedelta(days=1)
    async with recovery_db() as session:
        session.add(db.ExecutionBudgetModel(scope_id=old_scope, max_calls=10, max_tokens=1000,
            calls_used=2 if state == "cancelled_bad_count" else 1, tokens_charged=500,
            deadline_at=old_time, status="cancelled" if state.startswith("cancelled") else state))
        session.add_all([
            db.ExecutionOperationModel(key="old-zero-admission", scope_id=old_scope,
                input_hash="b" * 64, status="stopped", owner="old-owner", lease_until=old_time),
            db.ExecutionOperationModel(key="old-unknown-paid", scope_id=old_scope,
                input_hash="c" * 64, status="unknown", owner="old-owner", lease_until=old_time),
            db.ExecutionAttemptModel(id="old-unknown-actual", operation_key="old-unknown-paid",
                scope_id=old_scope, input_hash="c" * 64, model_name="offline",
                reserved_tokens=500, actual_tokens=None, status="unknown", created_at=old_time)])
        await session.commit()
        saved = {model.__tablename__: [recovery._row(row) for row in await session.scalars(
            select(model).where(model.scope_id == old_scope))]
            for model in (db.ExecutionBudgetModel, db.ExecutionOperationModel, db.ExecutionAttemptModel)}
    with bind_execution_scope(case.scope):
        if state != "cancelled":
            with pytest.raises(ValueError, match="alignment_unsent_recovery:global_orphan_chargeable_root"):
                async with recovery_db() as session:
                    await recovery.reconcile_unstarted_outline_alignment(session,
                        load_settings(env={}), **kwargs(case))
            return
        async with recovery_db() as session:
            p = await recovery.reconcile_unstarted_outline_alignment(session,
                load_settings(env={}), **kwargs(case))
            isolated = p["witness"]["isolated_cancelled_admissions"]
            assert len(isolated) == 1 and isolated[0]["purpose"] == "unknown_preserved"
            assert isolated[0]["operation_replay_allowed"] is False
            assert isolated[0]["credit_released"] is False
        async with recovery_db() as session:
            await recovery.reconcile_unstarted_outline_alignment(session,
                load_settings(env={}), **kwargs(case), execute=True,
                expected_witness_sha256=p["witness_sha256"])
            await session.commit()
            after = {model.__tablename__: [recovery._row(row) for row in await session.scalars(
                select(model).where(model.scope_id == old_scope))]
                for model in (db.ExecutionBudgetModel, db.ExecutionOperationModel, db.ExecutionAttemptModel)}
            assert saved == after


async def test_readonly_witness_drift_cannot_be_applied(recovery_db):
    case = await failed_case(recovery_db)
    with bind_execution_scope(case.scope):
        async with recovery_db() as session:
            p = await recovery.reconcile_unstarted_outline_alignment(session,
                load_settings(env={}), **kwargs(case))
        async with recovery_db() as session:
            b = await session.get(db.ExecutionBudgetModel, case.scope)
            b.metadata_json = {**b.metadata_json, "technical_change": True}
            await session.commit()
        with pytest.raises(ValueError, match="witness_changed"):
            async with recovery_db() as session:
                await recovery.reconcile_unstarted_outline_alignment(session,
                    load_settings(env={}), **kwargs(case), execute=True,
                    expected_witness_sha256=p["witness_sha256"])
