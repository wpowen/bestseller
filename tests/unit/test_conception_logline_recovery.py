"""Native isolated SQLite receipts; no provider, network or production database."""

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import event
from test_conception_checkpoints import _state
from test_execution_budget_resume import case as case  # noqa: F401
from test_execution_budget_resume import edit, state

from bestseller.infra.db import models as m
from bestseller.services import conception_logline_recovery as recovery
from bestseller.services.budget import (
    ExecutionBudgetError,
    ExecutionLedger,
    ExecutionReconciliation,
    bind_execution_scope,
)
from bestseller.services.conception import (
    _creation_intent_prompt_block,
    _seed_fidelity_context_projection,
)
from bestseller.services.conception_checkpoints import (
    build_conception_checkpoint,
    stable_conception_input_hash,
)
from bestseller.services.llm import LLMCompletionRequest, LLMCompletionResult, bind_conception_model
from bestseller.services.logline_gate import _reader_judge_messages, load_logline_gate_config
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def recovered(case):
    c = case
    c.ledger = ExecutionLedger(c.engine, c.scope, load_settings().budget)
    st = _state()
    st.update(
        explicit_concept_seed=c.contract.concept_seed,
        _ap_genre="仙侠",
        _ap_sub="修行",
        _appeal_cfg={},
    )
    cp = build_conception_checkpoint(st, input_hash=stable_conception_input_hash(c.attempt))
    intent = {
        "controls": _seed_fidelity_context_projection(st["ctx"]),
        "explicit_choices": _creation_intent_prompt_block(st["ctx"]),
        "concept_seed": st["explicit_concept_seed"],
    }
    story = {
        "synopsis": st["synopsis"],
        "story_spine": st["story_spine"],
        "story_architecture": st["concept_contract"].get("story_architecture", {}),
        "character": {},
        "world": {},
    }
    system, user, _ = _reader_judge_messages(
        text=st["writing_profile"]["market"]["logline"],
        premise=st["premise"],
        genre=st["_ap_genre"],
        sub_genre=st["_ap_sub"],
        gate=load_logline_gate_config({}),
        creation_intent=intent,
        story_context=story,
    )
    c.request = LLMCompletionRequest(
        logical_role="critic",
        system_prompt=system,
        user_prompt=user,
        fallback_response="{}",
        prompt_template="logline_gate",
        prompt_version="v3",
        max_tokens_override=8000,
    )
    c.input_hash = "b" * 64
    claim = await c.ledger.claim("native-original-logline", c.input_hash)
    aid = await c.ledger.reserve(
        claim, recovery.MODEL, c.input_hash, 47934, endpoint=recovery.ENDPOINT
    )
    await c.ledger.record_provider_return(
        claim,
        aid,
        {
            "id": "synthetic-provider-response",
            "stream_complete": False,
            "chunks_observed": 709,
            "choices": [{"message": {"content": "partial, never a grade"}, "finish_reason": None}],
            "usage": {"prompt_tokens": None, "completion_tokens": None},
        },
        wire_body_hash="c" * 64,
    )
    await c.ledger.record_transport_failure(
        aid,
        {
            "schema": "transport-failure.v1",
            "physical_sends": 1,
            "exception_type": "MidStreamFallbackError",
            "http_status": 500,
            "prompt_template": "logline_gate",
            "prompt_version": "v3",
            "logical_role": "critic",
            "project_id": None,
            "workflow_run_id": str(c.wid),
            "operator_interruption": False,
            "partial_stream_observed": True,
            "stream": True,
            "error_chain": [],
        },
    )
    await c.ledger.settle(aid, "unknown")
    await c.ledger.finish(claim, None)
    c.claim, c.aid, c.cp = claim, aid, cp
    c.task.update(
        error="execution_result_unknown",
        current_stage="execution_result_unknown",
        result={"execution_terminal": {"code": "execution_result_unknown", "terminal": True}},
    )

    def source(row):
        row.error_message = "execution_result_unknown"
        row.metadata_json = {**row.metadata_json, "conception_checkpoint": cp}

    await edit(c, m.WorkflowRunModel, c.wid, source)
    c.evidence = ExecutionReconciliation(
        reconciliation_id="synthetic-dispatcher-exit",
        attempt_id=aid,
        expected_input_hash=c.input_hash,
        decision="quarantine_charged",
        evidence_kind="dispatcher_exit",
        evidence_sha256="d" * 64,
        actor="synthetic-operator",
        remote_operation_closed=False,
        local_dispatcher_stopped=True,
    )
    c.quarantine = await c.ledger.reconcile(c.evidence)
    c.settings = load_settings().model_copy(deep=True)
    c.settings.llm.mock = False
    yield c


async def verify(c, **kwargs):
    async with c.factory() as s:
        return await recovery.verified_projectless_quarantine(
            s,
            c.settings,
            task_id=c.task_id,
            task_snapshot=kwargs.get("task", c.task),
            scope_id=c.scope,
            receipt=kwargs.get("receipt", c.quarantine),
        )


async def authorize(c, **kwargs):
    return await recovery.authorize_logline_replacement(
        c.ledger,
        task_id=c.task_id,
        task_snapshot=c.task,
        receipt=c.quarantine,
        actor="synthetic-operator",
        authorization_ref=kwargs.get("authorization_ref", "synthetic-explicit-user-continuation"),
    )


async def running_owner(c):
    wid = uuid4()
    async with c.factory() as s, s.begin():
        s.add(
            m.WorkflowRunModel(
                id=wid,
                project_id=None,
                workflow_type="conception_initial",
                status="running",
                metadata_json={
                    "execution_scope_id": c.scope,
                    "conception_checkpoint_input_hash": c.cp["input_hash"],
                },
            )
        )
    return wid


async def test_native_proof_select_only_preserves_every_row_and_remote_unknown(recovered):
    c = recovered
    before = await state(c)
    statements = []

    def only_select(_conn, _cursor, sql, *_):
        statements.append(sql)
        assert sql.lstrip().upper().startswith("SELECT")
        assert "FOR UPDATE" not in sql.upper()

    event.listen(c.engine.sync_engine, "before_cursor_execute", only_select)
    try:
        proof = await verify(c)
    finally:
        event.remove(c.engine.sync_engine, "before_cursor_execute", only_select)
    assert proof and proof["remote_outcome"] == "unknown_quarantined"
    assert (
        proof["adoption_authorized"]
        is proof["credit_released"]
        is proof["old_operation_replay_allowed"]
        is False
    )
    assert statements and await state(c) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "running"),
        ("cancel_requested", True),
        ("error", "other"),
        ("current_stage", "other"),
        ("task_id", str(uuid4())),
        ("task_type", "project"),
        ("project_slug", "foreign"),
    ],
)
async def test_task_mismatch_cannot_clear_unknown(recovered, field, value):
    task = deepcopy(recovered.task)
    task[field] = value
    assert await verify(recovered, task=task) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", "recover_completed"),
        ("credit_released", True),
        ("operation_replay_allowed", True),
        ("scope_id", "foreign"),
        ("remaining_unknown", 1),
    ],
)
async def test_caller_flags_cannot_replace_native_quarantine(recovered, field, value):
    receipt = deepcopy(recovered.quarantine)
    receipt[field] = value
    assert await verify(recovered, receipt=receipt) is None


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("source", "status", "running"),
        ("source", "error_message", "other"),
        ("attempt", "status", "unknown"),
        ("attempt", "actual_tokens", 1),
        ("attempt", "model_name", "other"),
        ("root", "status", "completed"),
        ("root", "result_json", {"forged": "grade"}),
        ("budget", "status", "unknown"),
    ],
)
async def test_native_changed_rows_deny(recovered, target, field, value):
    c = recovered
    model, key = {
        "source": (m.WorkflowRunModel, c.wid),
        "attempt": (m.ExecutionAttemptModel, c.aid),
        "root": (m.ExecutionOperationModel, c.claim.key),
        "budget": (m.ExecutionBudgetModel, c.scope),
    }[target]
    await edit(c, model, key, lambda row: setattr(row, field, value))
    assert await verify(c) is None


async def test_broken_checkpoint_and_missing_native_quarantine_deny(recovered):
    c = recovered
    await edit(
        c,
        m.WorkflowRunModel,
        c.wid,
        lambda r: r.metadata_json["conception_checkpoint"]["state"].update(title="drift"),
    )
    assert await verify(c) is None


async def test_operator_persistence_idempotent_and_no_new_credit(recovered):
    c = recovered
    async with c.factory() as s:
        b = await s.get(m.ExecutionBudgetModel, c.scope)
        before = (b.calls_used, b.tokens_charged, b.max_calls, b.max_tokens, b.deadline_at)
    plan = await authorize(c)
    assert await authorize(c) == plan
    async with c.factory() as s:
        b = await s.get(m.ExecutionBudgetModel, c.scope)
        assert (b.calls_used, b.tokens_charged, b.max_calls, b.max_tokens, b.deadline_at) == before
        assert (await s.get(m.ExecutionAttemptModel, c.aid)).status == "reconciled_charged"
        assert (await s.get(m.ExecutionOperationModel, c.claim.key)).status == "stopped"


async def test_foreign_completion_authority_rejected(recovered):
    with pytest.raises(ExecutionBudgetError):
        await authorize(recovered, authorization_ref="foreign")


async def test_request_binding_and_grant_native_once(recovered):
    c = recovered
    plan = await authorize(c)
    wid = await running_owner(c)
    with (
        bind_execution_scope(c.scope),
        bind_conception_model("minimax-m3.1-flash", task_id=c.task_id, workflow_run_id=wid),
    ):
        async with c.factory() as s:
            settings, req = await recovery.prepare_logline_request(s, c.settings, c.request)
    assert req.operation_id == plan["operation_id"] and req.prompt_version == plan["prompt_version"]
    assert req.metadata["prepared_effective_input"] == {
        "schema": "effective-input.v1",
        "hash": c.input_hash,
    }
    assert settings.llm.retry.max_attempts == 1 and settings is not c.settings
    async with c.factory() as s:
        b = await s.get(m.ExecutionBudgetModel, c.scope)
        grant = b.metadata_json["single_dispatch_grants"][
            req.metadata["single_dispatch_authorization_id"]
        ]
        assert grant["physical_attempt_cap"] == 1 and grant["status"] == "available"
        assert grant["binding"]["workflow_id"] == str(wid)


async def test_no_record_keeps_existing_request_and_settings(recovered):
    c = recovered
    with bind_execution_scope(c.scope):
        async with c.factory() as s:
            settings, req = await recovery.prepare_logline_request(s, c.settings, c.request)
    assert settings is c.settings and req is c.request


@pytest.mark.parametrize("complete", [False, True])
async def test_future_changed_logline_requires_completed_replacement_not_only_a_plan(
    recovered, complete
):
    c = recovered
    await authorize(c)
    wid = await running_owner(c)
    with (
        bind_execution_scope(c.scope),
        bind_conception_model("minimax-m3.1-flash", task_id=c.task_id, workflow_run_id=wid),
    ):
        async with c.factory() as session:
            _, req = await recovery.prepare_logline_request(session, c.settings, c.request)
        if complete:
            claim = await c.ledger.claim(req.operation_id, c.input_hash)
            aid = await c.ledger.reserve(
                claim,
                recovery.MODEL,
                c.input_hash,
                100,
                endpoint=recovery.ENDPOINT,
                workflow_id=str(wid),
                template=req.prompt_template,
                version=req.prompt_version,
                single_dispatch_id=req.metadata["single_dispatch_authorization_id"],
            )
            result = LLMCompletionResult(
                content="synthetic complete return",
                provider="openai",
                model_name=recovery.MODEL,
                input_tokens=3,
                output_tokens=2,
                finish_reason="stop",
            ).model_dump(mode="json")
            await c.ledger.settle(aid, "completed", actual_tokens=5, claim=claim, result=result)
        changed = c.request.model_copy(
            update={"user_prompt": c.request.user_prompt + "\nnew candidate"}
        )
        async with c.factory() as session:
            if complete:
                settings, actual = await recovery.prepare_logline_request(
                    session, c.settings, changed
                )
                assert actual is changed and settings is c.settings
            else:
                with pytest.raises(
                    ExecutionBudgetError, match="execution_logline_replacement_denied"
                ):
                    await recovery.prepare_logline_request(session, c.settings, changed)
