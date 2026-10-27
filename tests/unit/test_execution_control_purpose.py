"""Producer-created control rows with real SQLite parents; never a provider call."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import JSON, MetaData, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from bestseller.infra.db import models as m
from bestseller.services import conception_continuation as continuation
from bestseller.services import execution_control_purpose as control
from bestseller.services import execution_phase_handoff as handoff
from bestseller.services import independent_acceptance as independent
from bestseller.services import llm
from bestseller.services.budget import ExecutionLedger, OperationClaim, bind_execution_scope
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
KINDS = sorted(control.STATUSES)
MODELS = (
    m.ProjectModel,
    m.ChapterModel,
    m.WorkflowRunModel,
    m.LlmRunModel,
    m.ExecutionBudgetModel,
    m.ExecutionOperationModel,
    m.ExecutionAttemptModel,
)


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("execution control proof forbids network/PG/provider")

    async def paid_denied(*args, **kwargs):
        denied()

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(llm, "complete_text", paid_denied)
    monkeypatch.setattr(llm, "_complete_text_impl", paid_denied)
    import litellm

    monkeypatch.setattr(litellm, "acompletion", paid_denied)


def source_reference(parent, run):
    return {
        "source_scope": parent.scope_id,
        "operation_key": parent.key,
        "input_hash": parent.input_hash,
        "result_sha256": independent.digest(parent.result_json),
        "source_llm_run_id": str(run.id),
        "role": run.logical_role,
        "template": run.prompt_template,
    }


def declared_source(r):
    record = r.op.result_json
    if r.op.status == "recovered_success_reference":
        return record
    if r.op.status == "review_revision":
        return record["previous_sources"][0]
    if r.op.status == "identity_recovery":
        return record["source"]
    return None


def resign(r):
    """Use the real producer key contracts so field tests pass the identity fence."""
    op, record = r.op, r.op.result_json
    if op.status == "recovered_success_reference":
        op.key = sha(op.scope_id + "\nadopt\n" + str(record.get("operation_key")))
    elif op.status == "review_revision":
        op.key = independent.digest(
            {
                "schema": record["schema"],
                "scope": op.scope_id,
                "role": record["role"],
                "template": record["template"],
                "effective_input_hash": op.input_hash,
            }
        )
    elif op.status == "identity_recovery":
        op.key = independent.digest(record)
    elif op.status == "phase_handoff":
        op.key = handoff.receipt_key(record["binding"])
    else:
        op.key = sha(f"provider-return.v1\n{op.scope_id}\n{record['attempt_id']}")


async def produce(r, kind, monkeypatch):
    """Call actual producers; only phase handoff's upstream admission is stubbed.

    Admission itself has separate integration tests. Its stub grants no purpose
    here: this verifier still reads the actual parent/root/attempt/run/transport.
    """
    if kind in {"recovered_success_reference", "review_revision"}:
        request = llm.LLMCompletionRequest(
            logical_role="critic",
            project_id=r.project.id,
            prompt_template=r.run.prompt_template,
            prompt_version="v1",
            system_prompt="Frozen system",
            user_prompt="Frozen source"
            if kind == "recovered_success_reference"
            else "Changed source",
            fallback_response="never dispatched",
        )
        h = (
            r.parent.input_hash
            if kind == "recovered_success_reference"
            else sha("changed effective input")
        )
        payload = {"messages": [{"role": "user", "content": "changed"}], "tools": None}
        if kind == "review_revision":
            h = hashlib.sha256(
                json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
        with bind_execution_scope(r.scope):
            value = await continuation.adopt_cached(
                r.session, request, h, effective_payload=payload
            )
        assert (value is None) == (kind == "review_revision")
    elif kind == "identity_recovery":
        await continuation.recover_completed_market_identity_stop(
            r.session,
            task_id=r.task,
            scope=r.scope,
            payload={"slug": r.project.slug, "_active_continuation_scope": r.scope},
        )
        await r.session.commit()
    elif kind == "phase_handoff":
        binding = {
            "schema": handoff.SCHEMA,
            "scope_id": r.scope,
            "task_id": r.task,
            "project_id": str(r.project.id),
            "from_phase": "approved_conception",
            "to_phase": "project_execution",
            "derived_binding_sha256": sha("derived actual payload"),
            "quality_operation": r.parent.key,
            "quality_run_id": str(r.run.id),
            "quality_result_sha256": independent.digest(r.parent.result_json),
            "unknown_reconciled": False,
            "credit_released": False,
            "grade_reused_for_changed_content": False,
        }

        async def upstream_admission(*args, **kwargs):
            return deepcopy(binding), r.project, r.budget

        monkeypatch.setattr(handoff, "proof", upstream_admission)
        await handoff.create(r.session, task_id=r.task, payload={})
        await r.session.commit()
    else:
        aid, h = "retained-partial-send", sha("retained partial input")
        parent = m.ExecutionOperationModel(
            key=sha("retained partial root"),
            scope_id=r.scope,
            input_hash=h,
            status="stopped",
            owner="original",
            lease_until=r.budget.deadline_at,
        )
        attempt = m.ExecutionAttemptModel(
            id=aid,
            operation_key=parent.key,
            scope_id=r.scope,
            input_hash=h,
            status="reconciled_charged",
            model_name=independent.MODEL,
            reserved_tokens=1000,
            actual_tokens=None,
            created_at=datetime.now(UTC),
        )
        r.session.add_all([parent, attempt])
        await r.session.commit()
        ledger = ExecutionLedger(r.engine, r.scope, r.settings.budget)
        await ledger.record_transport_failure(
            aid,
            {
                "schema": "transport-failure.v1",
                "physical_sends": 1,
                "exception_type": "UnusableProviderResponse",
                "http_status": 200,
                "logical_role": "critic",
                "prompt_template": r.run.prompt_template,
                "project_id": None,
                "workflow_run_id": None,
                "operator_interruption": False,
            },
        )
        await ledger.record_provider_return(
            OperationClaim(parent.key, parent.owner),
            aid,
            {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 2}},
            wire_body_hash=sha("retained wire body"),
        )
        r.partial_parent, r.partial_attempt = parent, attempt
    r.op = await r.session.scalar(
        select(m.ExecutionOperationModel).where(
            m.ExecutionOperationModel.scope_id == r.scope, m.ExecutionOperationModel.status == kind
        )
    )
    assert r.op is not None
    assert not await r.session.scalar(
        select(m.ExecutionAttemptModel.id).where(m.ExecutionAttemptModel.operation_key == r.op.key)
    )


@pytest.fixture
async def case(tmp_path, monkeypatch, request):
    kind = getattr(request, "param", "recovered_success_reference")
    path = tmp_path / "execution_control_purpose_test.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        for model in MODELS:
            table = model.__table__.to_metadata(MetaData())
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                if column.server_default is not None and "::jsonb" in str(
                    column.server_default.arg
                ):
                    column.server_default = None
            await connection.run_sync(
                lambda c, t=table: c.execute(CreateTable(t, include_foreign_key_constraints=[]))
            )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            task, now = "unrelated-control-test", datetime.now(UTC)
            scope = "conception-continuation:" + task + ":existing-authority"
            old = m.ExecutionBudgetModel(
                scope_id="frozen-parent-scope",
                status="unknown",
                max_calls=54,
                max_tokens=1000000,
                calls_used=54,
                tokens_charged=123456,
                deadline_at=now - timedelta(days=1),
                metadata_json={"original_holds": ["keep"]},
            )
            session.add(old)
            await session.commit()
            objective = {
                "mode": "completion_bound",
                "status": "active",
                "authorization_ref": "existing fixture authority",
                "models": [independent.MODEL],
                "endpoint_hosts": ["fixture.invalid"],
                "objective": "existing same project",
            }
            req = {
                "scope": scope,
                "counter_sha256": independent.CONTRACT_SHA256,
                "new_calls": 100,
                "new_tokens": 9000000,
                "model": independent.MODEL,
                "window_seconds": 7200,
                "authorization_ref": "old authority",
                "old_snapshot": independent.snapshot(old),
                "old_scope": old.scope_id,
                "cached_results": [],
                "blocked_input_hash": sha("quarantined input"),
                "completion_authorization_ref": objective["authorization_ref"],
            }
            budget = m.ExecutionBudgetModel(
                scope_id=scope,
                max_calls=100,
                max_tokens=9000000,
                calls_used=7,
                tokens_charged=34567,
                deadline_at=now + timedelta(hours=2),
                status="active",
                metadata_json={
                    independent.KEY: {
                        "schema": independent.SCHEMA,
                        "request": req,
                        "request_sha256": independent.digest(req),
                    },
                    "provider_authorization": objective,
                },
            )
            project = m.ProjectModel(
                id=uuid4(),
                slug="control-purpose-story",
                title="Tidal museum",
                genre="fantasy",
                target_chapters=4,
                target_word_count=12000,
                status="paused",
                metadata_json={
                    "execution_scope_id": scope,
                    "production_pause_reason": "execution_operation_identity_required",
                    "execution_terminal": {"code": "execution_operation_identity_required"},
                    "raw_history": ["keep"],
                },
            )
            h, aid = sha("original paid input"), "original-actual-send"
            semantic = hashlib.sha256(
                json.dumps(
                    {
                        "input_hash": h,
                        "role": "critic",
                        "template": "market_validation_collision_judge",
                        "version": "v1",
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
            key = sha(scope + "\n" + semantic + ":slot:0")
            metadata = {
                "paid_call_origin": {
                    "schema": "paid-call-origin.v1",
                    "status": "recorded",
                    "project_id": None,
                    "workflow_run_id": None,
                    "step_run_id": None,
                    "logical_role": "critic",
                    "prompt_template": "market_validation_collision_judge",
                    "prompt_version": "v1",
                    "prompt_hash": h,
                },
                "effective_input_receipt": {
                    "schema": "effective-input.v1",
                    "hash": h,
                    "model": independent.MODEL,
                },
                "execution_receipt": {
                    "scope_id": scope,
                    "operation_key": key,
                    "attempts": [
                        {"attempt_id": aid, "model": independent.MODEL, "effective_input_hash": h}
                    ],
                },
            }
            result = {
                "content": "Retained original evaluation.",
                "provider": "openai",
                "model_name": independent.MODEL,
                "finish_reason": "stop",
                "input_tokens": 1,
                "output_tokens": 2,
                "fallback_used": False,
                "tool_calls": None,
                "raw_message": None,
                "metadata": deepcopy(metadata),
            }
            parent = m.ExecutionOperationModel(
                key=key,
                scope_id=scope,
                input_hash=h,
                status="completed",
                owner="original",
                lease_until=budget.deadline_at,
                result_json=result,
            )
            attempt = m.ExecutionAttemptModel(
                id=aid,
                operation_key=key,
                scope_id=scope,
                input_hash=h,
                model_name=independent.MODEL,
                status="completed",
                actual_tokens=3,
                reserved_tokens=100,
                created_at=now,
            )
            run = m.LlmRunModel(
                id=uuid4(),
                project_id=None,
                workflow_run_id=None,
                step_run_id=None,
                logical_role="critic",
                provider="openai",
                model_name=independent.MODEL,
                prompt_template="market_validation_collision_judge",
                prompt_version="v1",
                prompt_hash=h,
                input_tokens=1,
                output_tokens=2,
                finish_reason="stop",
                metadata_json=deepcopy(metadata),
            )
            transport = m.ExecutionOperationModel(
                key=sha(f"transport-result.v1\n{scope}\n{aid}"),
                scope_id=scope,
                input_hash=h,
                status="transport_result",
                owner="original",
                lease_until=budget.deadline_at,
                result_json={
                    "schema": "transport-result.v1",
                    "operation_key": key,
                    "attempt_id": aid,
                    "input_hash": h,
                    "result": deepcopy(result),
                },
            )
            session.add_all([budget, project, parent, attempt, run, transport])
            await session.commit()
            r = SimpleNamespace(
                session=session,
                factory=factory,
                engine=engine,
                scope=scope,
                task=task,
                project=project,
                budget=budget,
                parent=parent,
                attempt=attempt,
                run=run,
                transport=transport,
                settings=load_settings().model_copy(deep=True),
            )
            await produce(r, kind, monkeypatch)
            yield r
    finally:
        await engine.dispose()


async def snapshot(session):
    return {
        model.__tablename__: sorted(
            [control._row(row) for row in await session.scalars(select(model))],
            key=independent.digest,
        )
        for model in MODELS
    }


async def call(r, **overrides):
    operations = list(await r.session.scalars(select(m.ExecutionOperationModel)))
    attempts = list(await r.session.scalars(select(m.ExecutionAttemptModel)))
    return await control.verify_non_dispatch_control(
        r.session, operation=r.op, operations=operations, attempts=attempts, **overrides
    )


@pytest.mark.parametrize("case", KINDS, indirect=True)
async def test_actual_producer_key_zero_own_sends_parent_purpose_readonly_idempotent(
    case, monkeypatch
):
    r = case
    before, sql = await snapshot(r.session), []

    def track(_conn, _cursor, statement, *_):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "COMMIT")):
            sql.append(statement)

    async def denied(*args, **kwargs):
        raise AssertionError("control purpose cannot flush/commit")

    monkeypatch.setattr(r.session, "flush", denied)
    monkeypatch.setattr(r.session, "commit", denied)
    event.listen(r.engine.sync_engine, "before_cursor_execute", track)
    try:
        proof = await call(r)
        assert proof == await call(r)
        assert proof["schema"] == control.SCHEMA
        assert proof["control_key"] == r.op.key and proof["control_status"] == r.op.status
        assert type(proof["own_physical_sends"]) is int and proof["own_physical_sends"] == 0
        assert proof["control_row_sha256"] == independent.digest(control._row(r.op))
        assert (
            proof["grade_granted"]
            is proof["credit_granted"]
            is proof["unknown_reconciled"]
            is False
        )
        assert r.session.info[control.INFO] == {r.op.key: proof}
        assert await snapshot(r.session) == before and sql == []
        assert not r.session.new and not r.session.dirty and not r.session.deleted
        if r.op.status == "unusable_provider_return":
            assert proof["parents"] == [
                {
                    "operation_key": r.partial_parent.key,
                    "attempt_id": r.partial_attempt.id,
                    "outcome_still_unknown": True,
                }
            ]
            assert r.partial_attempt.status == "reconciled_charged"
            assert (
                r.partial_attempt.actual_tokens is None
                and r.partial_attempt.reserved_tokens == 1000
            )
        else:
            assert proof["parents"] == [
                {
                    "operation_key": r.parent.key,
                    "scope_id": r.scope,
                    "input_hash": r.parent.input_hash,
                    "result_sha256": independent.digest(r.parent.result_json),
                }
            ]
    finally:
        event.remove(r.engine.sync_engine, "before_cursor_execute", track)


@pytest.mark.parametrize("case", KINDS, indirect=True)
async def test_any_own_actual_attempt_rejects_status_only_exemption(case):
    r = case
    r.session.add(
        m.ExecutionAttemptModel(
            id="illegal-control-send",
            operation_key=r.op.key,
            scope_id=r.scope,
            input_hash=r.op.input_hash,
            model_name=independent.MODEL,
            status="completed",
            actual_tokens=1,
            reserved_tokens=100,
            created_at=datetime.now(UTC),
        )
    )
    await r.session.commit()
    before = await snapshot(r.session)
    with pytest.raises(ValueError, match="control_has_actual_send"):
        await call(r)
    assert await snapshot(r.session) == before and control.INFO not in r.session.info


@pytest.mark.parametrize("case", KINDS, indirect=True)
async def test_wrong_producer_key_is_not_control_evidence(case):
    r = case
    r.op.key = sha("forged control key")
    await r.session.commit()
    with pytest.raises(ValueError):
        await call(r)
    assert control.INFO not in r.session.info


@pytest.mark.parametrize(
    "case", [k for k in KINDS if k != "unusable_provider_return"], indirect=True
)
async def test_changed_original_parent_result_rejects_before_purpose(case):
    r = case
    r.parent.result_json = {**r.parent.result_json, "content": "changed original physical result"}
    await r.session.commit()
    with pytest.raises(ValueError):
        await call(r)
    assert control.INFO not in r.session.info


@pytest.mark.parametrize(
    "case", [k for k in KINDS if k != "unusable_provider_return"], indirect=True
)
async def test_matching_result_hash_cannot_bypass_parent_commercial_purpose(case):
    r = case
    paid = deepcopy(r.parent.result_json)
    paid["metadata"]["commercial_source_receipt"] = {"body_sha256": sha("unrelated body")}
    r.parent.result_json = paid
    record = deepcopy(r.op.result_json)
    r.op.result_json = record
    if r.op.status == "phase_handoff":
        record["binding"]["quality_result_sha256"] = independent.digest(paid)
    else:
        declared_source(r)["result_sha256"] = independent.digest(paid)
    resign(r)
    await r.session.commit()
    with pytest.raises(ValueError):
        await call(r)
    assert control.INFO not in r.session.info


@pytest.mark.parametrize("case", ["identity_recovery", "phase_handoff"], indirect=True)
@pytest.mark.parametrize("calls", [False, True, 0.0, "0", 1])
async def test_provider_calls_must_be_json_integer_zero(case, calls):
    r = case
    r.op.result_json = {**r.op.result_json, "provider_calls": calls}
    resign(r)
    await r.session.commit()
    with pytest.raises(ValueError):
        await call(r)
    assert control.INFO not in r.session.info


@pytest.mark.parametrize(
    "case,field",
    [
        ("identity_recovery", "unknown_reconciled"),
        ("identity_recovery", "grade_changed"),
        ("identity_recovery", "credit_released"),
        ("phase_handoff", "budget_regranted"),
        ("unusable_provider_return", "usable"),
        ("unusable_provider_return", "adoption_authorized"),
        ("unusable_provider_return", "credit_released"),
        ("review_revision", "grade_reused"),
    ],
    indirect=["case"],
)
@pytest.mark.parametrize("false_alias", [0, None, "false"])
async def test_no_authority_boolean_requires_exact_json_false(case, field, false_alias):
    r = case
    r.op.result_json = {**r.op.result_json, field: false_alias}
    resign(r)
    await r.session.commit()
    with pytest.raises(ValueError):
        await call(r)
    assert control.INFO not in r.session.info


@pytest.mark.parametrize("case", ["unusable_provider_return"], indirect=True)
async def test_partial_response_hash_binds_boolean_and_integer_distinctly(case):
    r = case
    record = deepcopy(r.op.result_json)
    assert record["response"]["usage"]["prompt_tokens"] == 1
    record["response"]["usage"]["prompt_tokens"] = True
    r.op.result_json = record
    await r.session.commit()
    with pytest.raises(ValueError, match="partial_payload"):
        await call(r)
    assert control.INFO not in r.session.info


@pytest.mark.parametrize("case", ["unusable_provider_return"], indirect=True)
@pytest.mark.parametrize(
    "mode", ["missing_failure", "changed_parent_hash", "unknown_parent", "missing_wire_hash"]
)
async def test_partial_return_requires_original_producer_and_parent_evidence(case, mode):
    r = case
    if mode == "missing_failure":
        failure = await r.session.scalar(
            select(m.ExecutionOperationModel).where(
                m.ExecutionOperationModel.status == "transport_failure_receipt"
            )
        )
        await r.session.delete(failure)
    elif mode == "changed_parent_hash":
        r.partial_parent.input_hash = sha("different parent input")
    elif mode == "unknown_parent":
        r.partial_parent.status = "unknown"
    else:
        record = deepcopy(r.op.result_json)
        record.pop("wire_body_hash")
        r.op.result_json = record
    await r.session.commit()
    with pytest.raises(ValueError):
        await call(r)
    assert control.INFO not in r.session.info


async def test_unsupported_status_returns_none_not_generic_exemption(case):
    r = case
    r.op.status = "completed"
    await r.session.commit()
    before = await snapshot(r.session)
    assert await call(r) is None
    assert await snapshot(r.session) == before and control.INFO not in r.session.info
