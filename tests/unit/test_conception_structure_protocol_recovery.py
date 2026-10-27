"""Synthetic retained-paid evidence over real hashes and isolated SQLite rows.

No PostgreSQL/provider use. These receipts are fixtures, never production spend,
grades, adoption, or evidence that the live diagnosis is suitable for v2 repair.
"""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import inspect
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import CreateTable

from bestseller.infra.db.models import (
    ExecutionAttemptModel, ExecutionBudgetModel, ExecutionOperationModel, LlmRunModel, WorkflowRunModel,
)
from bestseller.services import conception_checkpoints as checkpoints
from bestseller.services import conception_structure_protocol_recovery as recovery
from bestseller.services import llm
from bestseller.services.conception import _conception_structure_diagnosis
from bestseller.services.conception_structure_budget import KEY as COUNTS_KEY, reserve_structure_repair, structure_repair_budget
from bestseller.services.conception_structure_repair import STRUCTURE_REPAIR_PROMPT_VERSION, build_structure_repair_request
from bestseller import settings as settings_module

pytestmark = pytest.mark.unit
MODELS = (WorkflowRunModel, LlmRunModel, ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel)
ERROR = {"type": "ValueError", "message": "structure_repair_candidate_unchanged"}


@compiles(JSONB, "sqlite")
def _sqlite_json(type_, compiler, **kwargs):
    return "JSON"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def forbid_dispatch(*args, **kwargs):
    raise AssertionError("This recovery test may not call a provider or production database")


@pytest.fixture
def case(monkeypatch):
    settings = settings_module.load_settings().model_copy(deep=True)
    settings.llm.editor = settings.llm.editor.model_copy(update={
        "model": "openai/MiniMax-M3", "api_base": "https://api.minimaxi.com/v1",
        "temperature": 0.4, "max_tokens": 32768, "thinking_type": "disabled", "stream": False,
        "n_candidates": 1, "reasoning_effort": None,
    })
    monkeypatch.setattr(settings_module, "apply_runtime_llm_profile", lambda _: settings)
    monkeypatch.setattr(llm, "complete_text", forbid_dispatch)
    state = {"premise": "执事必须在天亮前追回失控的护城符。", "title": "测试候选",
        "synopsis": "执事追查失控护城符并付出行动代价。", "story_spine": {"who": "执事", "wants": "追回护城符"},
        "writing_profile": {}, "concept_contract": {"schema_version": "concept-contract.v2"}, "ctx": {},
        "tags": [], "commercial_brief": {}, "world_model_payload": {}, "chapter_count": 30,
        "genre_key": "xuanhuan", "conception_log": [{"historical_rejection": "preserve"}],
        "llm_run_ids": [], "structure_repair_attempts": 1,
        "repair_quotas": {"structure": {"spent": 1}},
        "last_failure": {"violations": ["FINAL_MATERIALIZATION_LOGLINE_REVIEW_UNRESOLVED"],
            "blocked_by": ["logline_gate"], "structure_repair_error": deepcopy(ERROR),
            "conception_log": [{"agent": "materialized_logline_blocked", "report": {
                "repair_scope": "story", "axis_why": {"goal": {"repair_scope": "story",
                    "reason": "需要补足行动受阻的原因", "evidence": [{"field": "premise", "quote": "天亮前追回"}]}}}}]}}
    checkpoint = checkpoints.build_conception_checkpoint(state, input_hash="synthetic-creation-input")
    wid, rid, aid = uuid4(), uuid4(), uuid4().hex
    scope, root = "conception:" + str(uuid4()), sha(uuid4().hex)
    source = deepcopy(checkpoint["state"])
    source["last_failure"].pop("structure_repair_error")
    user = encoded({"state": source, "diagnosis": _conception_structure_diagnosis(source)})
    request = llm.LLMCompletionRequest(logical_role="editor", model_tier="strong", system_prompt="保留历史 v1 系统提示。",
        user_prompt=user, fallback_response="{}", prompt_template="conception_structure_repair", prompt_version="v1",
        workflow_run_id=wid, max_tokens_override=8192)
    request_json = encoded(request.model_dump(mode="json"))
    prompt_hash = llm._effective_input_hash(request, settings.llm.editor)
    proof = {"schema": "derived-missing-structure-input-proof.v1", "kind": "derived_missing_input_proof",
        "workflow_run_id": str(wid), "scope": scope, "checkpoint_sha256": recovery.digest(checkpoint),
        "checkpoint_state_sha256": checkpoint["state_sha256"], "prompt_template": request.prompt_template,
        "prompt_version": "v1", "only_removed_path": "last_failure.structure_repair_error",
        "removed_value": deepcopy(ERROR), "original_request_replayable": False,
        "old_physical_request_bytes_verified": False, "old_payload_refs_rewritten": False,
        "credit_released": False, "new_grade_created": False,
        "reconstructed_request": request_json, "reconstructed_request_sha256": sha(request_json),
        "prompt_hash": prompt_hash, "user_chars": len(user), "user_sha256": sha(user),
        "source_state_sha256": recovery.digest(source), "system_sha256": sha(request.system_prompt),
        "system_chars": len(request.system_prompt), "llm_run_id": str(rid), "operation_key": root,
        "actual_attempt_ids": [aid]}
    return SimpleNamespace(settings=settings, checkpoint=checkpoint, source=source, proof=proof,
        wid=wid, rid=rid, aid=aid, scope=scope, root=root, request=request)


def derive(case, *, checkpoint=None, proof=None, **overrides):
    kwargs = {"workflow_id": case.wid, "scope": case.scope, "proof": proof or case.proof,
              "actor": "authorized-test-operator", "reason": "Explicit test-only v1 to v2 protocol migration"}
    kwargs.update(overrides)
    return recovery.derive(checkpoint or case.checkpoint, **kwargs)


def test_derive_preserves_legacy_spend_and_all_original_state(case):
    before = encoded(case.checkpoint), encoded(case.proof)
    result = derive(case)
    assert (encoded(case.checkpoint), encoded(case.proof)) == before
    expected = deepcopy(case.checkpoint["state"])
    expected[COUNTS_KEY] = {"v1": 1, STRUCTURE_REPAIR_PROMPT_VERSION: 0}
    assert result["derived_checkpoint"]["state"] == expected
    assert result["derived_checkpoint"]["input_hash"] == case.checkpoint["input_hash"]
    assert result["cumulative_attempts_before"] == result["cumulative_attempts_after"] == 1
    assert result["new_protocol_attempt_limit"] == 1
    assert result["credit_released"] is result["old_checkpoint_modified"] is result["grade_modified"] is False
    assert result["all_new_calls_charged_to_original_scope"] is True


def test_new_protocol_has_one_draw_then_total_two_and_cannot_repeat(case):
    state = derive(case)["derived_checkpoint"]["state"]
    assert structure_repair_budget(state, protocol=STRUCTURE_REPAIR_PROMPT_VERSION) == (1, 0)
    reserve_structure_repair(state, protocol=STRUCTURE_REPAIR_PROMPT_VERSION)
    assert structure_repair_budget(state, protocol=STRUCTURE_REPAIR_PROMPT_VERSION) == (2, 1)
    assert state[COUNTS_KEY] == {"v1": 1, STRUCTURE_REPAIR_PROMPT_VERSION: 1}
    before = encoded(state)
    with pytest.raises(ValueError):
        reserve_structure_repair(state, protocol=STRUCTURE_REPAIR_PROMPT_VERSION)
    assert encoded(state) == before


def test_source_policy_imports_and_hashes_actual_v2_builder(case):
    policy = recovery.source_policy()
    assert policy["protocol"] == STRUCTURE_REPAIR_PROMPT_VERSION
    assert policy["functions"]["build_structure_repair_request"] == sha(inspect.getsource(build_structure_repair_request))
    assert derive(case)["source_policy"] == policy
    request = build_structure_repair_request(state=case.source, diagnosis=_conception_structure_diagnosis(case.source))
    assert request.prompt_version == STRUCTURE_REPAIR_PROMPT_VERSION


@pytest.mark.parametrize("fault", ["checkpoint_hash", "state_hash", "source", "system", "late_hash", "workflow", "scope"])
def test_derive_refuses_stale_original_input(case, fault):
    checkpoint, proof = deepcopy(case.checkpoint), deepcopy(case.proof)
    if fault == "checkpoint_hash":
        proof["checkpoint_sha256"] = "0" * 64
    elif fault == "state_hash":
        checkpoint["state_sha256"] = "0" * 64
    elif fault == "source":
        checkpoint["state"]["title"] += "异"
        checkpoint["state_sha256"] = recovery.digest(checkpoint["state"])
        proof.update(checkpoint_sha256=recovery.digest(checkpoint), checkpoint_state_sha256=checkpoint["state_sha256"])
    elif fault in {"system", "late_hash"}:
        request = json.loads(proof["reconstructed_request"])
        if fault == "system":
            request["system_prompt"] += "异"
        else:
            request["user_prompt"] = request["user_prompt"][:-1] + " "
        proof["reconstructed_request"] = encoded(request)
        proof["reconstructed_request_sha256"] = sha(proof["reconstructed_request"])
    elif fault == "workflow":
        proof["workflow_run_id"] = str(uuid4())
    else:
        proof["scope"] = "other-scope"
    with pytest.raises(ValueError):
        derive(case, checkpoint=checkpoint, proof=proof)


@pytest.mark.parametrize("total,map_", [(0, None), (2, None), (-1, None), (True, None),
    (1, {"v1": 1}), (1, {"v1": 0, STRUCTURE_REPAIR_PROMPT_VERSION: 1}),
    (1, {"v1": 1, "unreviewed-protocol": 0})])
def test_only_genuine_spent_legacy_v1_without_prior_map_is_migratable(case, total, map_):
    checkpoint = deepcopy(case.checkpoint)
    checkpoint["state"]["structure_repair_attempts"] = total
    if map_ is not None:
        checkpoint["state"][COUNTS_KEY] = map_
    checkpoint["state_sha256"] = recovery.digest(checkpoint["state"])
    # Keep the synthetic retained input internally consistent so a stale proof
    # cannot accidentally mask a missing counter/protocol-map guard.
    proof = deepcopy(case.proof)
    source = deepcopy(checkpoint["state"])
    source["last_failure"].pop("structure_repair_error")
    request = case.request.model_copy(update={"user_prompt": encoded({
        "state": source, "diagnosis": _conception_structure_diagnosis(source)})})
    request_json = encoded(request.model_dump(mode="json"))
    proof.update(checkpoint_sha256=recovery.digest(checkpoint), checkpoint_state_sha256=checkpoint["state_sha256"],
        reconstructed_request=request_json, reconstructed_request_sha256=sha(request_json),
        prompt_hash=llm._effective_input_hash(request, case.settings.llm.editor),
        user_chars=len(request.user_prompt), user_sha256=sha(request.user_prompt), source_state_sha256=recovery.digest(source))
    with pytest.raises(ValueError, match="legacy_count|invalid conception structure repair budget"):
        derive(case, checkpoint=checkpoint, proof=proof)


@pytest.mark.parametrize("field,value", [("type", "RuntimeError"), ("message", "some_other_failure")])
def test_other_failures_do_not_gain_a_protocol_draw(case, field, value):
    checkpoint = deepcopy(case.checkpoint)
    checkpoint["state"]["last_failure"]["structure_repair_error"][field] = value
    checkpoint["state_sha256"] = recovery.digest(checkpoint["state"])
    with pytest.raises(ValueError, match="legacy_failure"):
        derive(case, checkpoint=checkpoint)


@pytest.mark.parametrize("actor,reason", [("", "explicit"), ("explicit", "")])
def test_explicit_operator_authority_is_required(case, actor, reason):
    with pytest.raises(ValueError):
        derive(case, actor=actor, reason=reason)


@pytest.mark.parametrize("field,value", [("logical_role", "critic"), ("prompt_template", "other_call"),
    ("prompt_version", "v2-unrelated"), ("model_tier", "standard"), ("max_tokens_override", 4096),
    ("tools", [{"type": "function", "function": {"name": "unrelated"}}])])
def test_legacy_request_api_is_closed_even_if_attacker_rehashes_request(case, field, value):
    proof = deepcopy(case.proof)
    request = json.loads(proof["reconstructed_request"])
    request[field] = value
    proof["reconstructed_request"] = encoded(request)
    proof["reconstructed_request_sha256"] = sha(proof["reconstructed_request"])
    proof["prompt_hash"] = llm._effective_input_hash(llm.LLMCompletionRequest.model_validate(request), case.settings.llm.editor)
    with pytest.raises(ValueError):
        derive(case, proof=proof)


@pytest.fixture
async def dbcase(tmp_path, case):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'structure_protocol_test.sqlite3'}")
    async with engine.begin() as connection:
        for model in MODELS:
            await connection.exec_driver_sql(str(CreateTable(model.__table__).compile(dialect=connection.dialect)))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 1, 2, 3, 4, 5)
    declared = [{"attempt_id": case.aid, "status": "completed", "effective_input_hash": case.proof["prompt_hash"],
                 "model": "openai/MiniMax-M3"}]
    paid = {"content": encoded({key: case.source[key] for key in ("premise", "synopsis", "story_spine")}),
        "provider": "openai", "model_name": "openai/MiniMax-M3", "finish_reason": "stop",
        "input_tokens": 10, "output_tokens": 4, "tool_calls": None,
        "raw_message": {"role": "assistant", "content": "unchanged fixture return"},
        "fallback_used": False, "fallback_source": None}
    origin = {"schema": "paid-call-origin.v1", "status": "recorded", "project_id": None,
        "workflow_run_id": str(case.wid), "step_run_id": None, "logical_role": "editor",
        "prompt_template": "conception_structure_repair", "prompt_version": "v1", "prompt_hash": case.proof["prompt_hash"]}
    execution = {"scope_id": case.scope, "operation_key": case.root, "attempts": declared}
    paid["metadata"] = {"paid_call_origin": origin, "effective_input_receipt": {"hash": case.proof["prompt_hash"]}}
    completed = deepcopy(paid)
    completed["latency_ms"] = 99
    completed["metadata"]["runtime_provenance"] = {"retained": "gateway enrichment"}
    policy = {"mode": "completion_bound", "provider": "minimax", "status": "active",
        "models": ["openai/MiniMax-M3"], "endpoint_hosts": ["api.minimaxi.com"],
        "authorization_ref": "test-only-human-authorization", "objective": "bounded synthetic acceptance"}
    metadata = {"execution_scope_id": case.scope, checkpoints.CHECKPOINT_KEY: deepcopy(case.checkpoint),
        checkpoints.CHECKPOINT_INPUT_HASH_KEY: case.checkpoint["input_hash"],
        "attempt_history": [{"status": "failed", "evidence": "keep exact"}], "paid_calls": 1}
    transport = sha(f"transport-result.v1\n{case.scope}\n{case.aid}")
    async with factory() as session:
        session.add_all([
            WorkflowRunModel(id=case.wid, workflow_type="conception_initial", project_id=None,
                status="failed", current_step="failed", error_message="retained failure", metadata_json=metadata),
            LlmRunModel(id=case.rid, created_at=now, project_id=None, workflow_run_id=case.wid,
                logical_role="editor", provider="openai", model_name="openai/MiniMax-M3",
                prompt_template="conception_structure_repair", prompt_version="v1", prompt_hash=case.proof["prompt_hash"],
                input_tokens=10, output_tokens=4, finish_reason="stop", request_payload_ref="legacy.request.json",
                response_payload_ref="legacy.response.json", metadata_json={"execution_receipt": execution,
                    "paid_call_origin": origin, "effective_input_receipt": {"hash": case.proof["prompt_hash"]},
                    "prompt_payload_receipt": {"replayable": False}}),
            ExecutionBudgetModel(scope_id=case.scope, max_calls=1, max_tokens=14, calls_used=1, tokens_charged=14,
                deadline_at=now-timedelta(days=1), status="active", metadata_json={"provider_authorization": policy,
                    "history": [{"spent": 14, "preserve": True}]}),
            ExecutionOperationModel(key=case.root, scope_id=case.scope, input_hash=case.proof["prompt_hash"],
                status="completed", owner="fixture", lease_until=now, result_json=completed),
            ExecutionOperationModel(key=transport, scope_id=case.scope, input_hash=case.proof["prompt_hash"],
                status="transport_result", owner="fixture", lease_until=now, result_json={"operation_key": case.root,
                    "attempt_id": case.aid, "input_hash": case.proof["prompt_hash"], "result": deepcopy(paid)}),
            ExecutionAttemptModel(id=case.aid, operation_key=case.root, scope_id=case.scope,
                model_name="openai/MiniMax-M3", input_hash=case.proof["prompt_hash"], reserved_tokens=32,
                actual_tokens=14, status="completed", created_at=now)])
        await session.commit()
    async with factory() as session:
        run = await session.get(LlmRunModel, case.rid)
        attempt = await session.get(ExecutionAttemptModel, case.aid)
        root_row = await session.get(ExecutionOperationModel, case.root)
        transport_row = await session.get(ExecutionOperationModel, transport)
        case.proof.update(run_row_sha256=recovery.digest(recovery.row(run)),
            root_row_sha256=recovery.digest(recovery.row(root_row)),
            transport_row_sha256=recovery.digest(recovery.row(transport_row)),
            declared_attempts_sha256=recovery.digest(declared), actual_attempt_rows_sha256=recovery.digest([recovery.row(attempt)]),
            response_proof={"paid_result_sha256": recovery.digest(paid)},
            counters={"calls_used": 1, "tokens_charged": 14})
    case.factory, case.transport, case.old_metadata = factory, transport, metadata
    case.prepared = derive(case)
    case.raw_files = [tmp_path / "legacy.request.json", tmp_path / "legacy.response.json"]
    case.raw_files[0].write_text(encoded({"replayable": False, "preview": case.request.user_prompt[:120]}))
    case.raw_files[1].write_text(encoded(paid))
    yield case
    await engine.dispose()


async def inventory(session):
    result = {}
    for model in MODELS:
        rows = list(await session.scalars(select(model)))
        result[model.__tablename__] = sorted([recovery.row(row) for row in rows], key=encoded)
    return result


@pytest.mark.asyncio
async def test_sqlite_additive_idempotent_persistence_and_normal_loader_preserve_every_old_row(dbcase):
    case = dbcase
    async with case.factory() as session:
        before = await inventory(session)
        raw = [p.read_bytes() for p in case.raw_files]
        assert await checkpoints.load_conception_checkpoint(session, workflow_run_id=case.wid,
            input_hash=case.checkpoint["input_hash"]) == case.checkpoint
        await recovery.persist_recovery(session, workflow_run_id=case.wid, recovery=case.prepared)
        await session.rollback()
    async with case.factory() as session:
        assert await inventory(session) == before
        await recovery.persist_recovery(session, workflow_run_id=case.wid, recovery=case.prepared)
        await session.commit()
    async with case.factory() as session:
        row = await session.get(WorkflowRunModel, case.wid)
        assert row.metadata_json[checkpoints.CHECKPOINT_KEY] == case.checkpoint
        assert {k: v for k, v in row.metadata_json.items() if k != recovery.KEY} == case.old_metadata
        after = await inventory(session)
        after["workflow_runs"][0]["metadata_json"].pop(recovery.KEY)
        # The additive metadata write may advance only this row's audit clock.
        assert after["workflow_runs"][0]["updated_at"] >= before["workflow_runs"][0]["updated_at"]
        after["workflow_runs"][0]["updated_at"] = before["workflow_runs"][0]["updated_at"]
        assert after == before
        selected = await checkpoints.load_conception_checkpoint(session, workflow_run_id=case.wid,
            input_hash=case.checkpoint["input_hash"])
        assert selected == case.prepared["derived_checkpoint"]
        assert selected["state"]["structure_repair_attempts"] == 1
        selected["state"]["title"] = "caller local edit"
        assert row.metadata_json[recovery.KEY]["derived_checkpoint"]["state"]["title"] != selected["state"]["title"]
        repeated = await recovery.persist_recovery(session, workflow_run_id=case.wid, recovery=case.prepared)
        assert repeated == case.prepared and not session.dirty
        assert [p.read_bytes() for p in case.raw_files] == raw


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("models", ["other-model"]), ("models", ["openai/MiniMax-M3", "other-model"]),
    ("endpoint_hosts", ["other.example"]), ("provider", "other-provider"), ("status", "paused"),
    ("authorization_ref", "")])
async def test_persist_authorization_is_original_minimax_only(dbcase, field, value):
    case = dbcase
    async with case.factory() as session:
        budget = await session.get(ExecutionBudgetModel, case.scope)
        metadata = deepcopy(budget.metadata_json)
        metadata["provider_authorization"][field] = value
        budget.metadata_json = metadata
        await session.commit()
        with pytest.raises(ValueError):
            await recovery.persist_recovery(session, workflow_run_id=case.wid, recovery=case.prepared)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["source_policy", "nonmonotonic_total", "wrong_protocol", "other_target"])
async def test_prepared_recovery_cannot_be_changed_or_retargeted(dbcase, fault):
    case = dbcase
    prepared = deepcopy(case.prepared)
    if fault == "source_policy":
        prepared["source_policy"]["functions"]["build_structure_repair_request"] = "0" * 64
    elif fault == "nonmonotonic_total":
        prepared["derived_checkpoint"]["state"]["structure_repair_attempts"] = 0
    elif fault == "wrong_protocol":
        prepared["new_protocol"] = "unreviewed-next-version"
    async with case.factory() as session:
        with pytest.raises(ValueError):
            await recovery.persist_recovery(session, workflow_run_id=uuid4() if fault == "other_target" else case.wid,
                recovery=prepared)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["run", "extra_attempt", "transport", "transport_fallback", "root_and_transport", "root_telemetry", "transport_telemetry", "budget_counter"])
async def test_persist_rechecks_paid_rows_all_attempts_transport_and_counters(dbcase, fault):
    case = dbcase
    async with case.factory() as session:
        if fault == "run":
            run = await session.get(LlmRunModel, case.rid)
            run.output_tokens += 1
        elif fault == "extra_attempt":
            attempt = await session.get(ExecutionAttemptModel, case.aid)
            values = {key: value for key, value in recovery.row(attempt).items() if key != "created_at"}
            values["id"] = uuid4().hex
            session.add(ExecutionAttemptModel(**values, created_at=datetime.now(UTC)))
        elif fault == "budget_counter":
            budget = await session.get(ExecutionBudgetModel, case.scope)
            budget.calls_used = budget.tokens_charged = 0
        elif fault in {"root_telemetry", "transport_telemetry"}:
            op = await session.get(ExecutionOperationModel, case.root if fault == "root_telemetry" else case.transport)
            value = deepcopy(op.result_json)
            value["unexpected_enrichment"] = "must not silently normalize"
            op.result_json = value
        else:
            transport = await session.get(ExecutionOperationModel, case.transport)
            value = deepcopy(transport.result_json)
            if fault == "transport_fallback":
                value["result"]["fallback_used"] = True
            else:
                value["result"]["content"] = "changed paid response"
            transport.result_json = value
            if fault == "root_and_transport":
                root = await session.get(ExecutionOperationModel, case.root)
                root.result_json = deepcopy(value["result"])
        await session.commit()
        with pytest.raises(ValueError):
            await recovery.persist_recovery(session, workflow_run_id=case.wid, recovery=case.prepared)


@pytest.mark.asyncio
async def test_normal_loader_refuses_tampered_additive_receipt(dbcase):
    case = dbcase
    async with case.factory() as session:
        await recovery.persist_recovery(session, workflow_run_id=case.wid, recovery=case.prepared)
        await session.commit()
        run = await session.get(WorkflowRunModel, case.wid)
        metadata = deepcopy(run.metadata_json)
        metadata[recovery.KEY]["derived_checkpoint"]["state"]["structure_repair_attempts"] = 0
        run.metadata_json = metadata
        await session.commit()
        with pytest.raises(ValueError):
            await checkpoints.load_conception_checkpoint(session, workflow_run_id=case.wid,
                input_hash=case.checkpoint["input_hash"])


def test_already_migrated_checkpoint_cannot_receive_another_protocol_mapping(case):
    prepared = derive(case)
    with pytest.raises(ValueError, match="legacy_count"):
        derive(case, checkpoint=prepared["derived_checkpoint"])


@pytest.mark.asyncio
async def test_repeat_target_with_different_review_receipt_is_refused(dbcase):
    case = dbcase
    async with case.factory() as session:
        await recovery.persist_recovery(session, workflow_run_id=case.wid, recovery=case.prepared)
        await session.commit()
        repeat = derive(case, reason="A different review must not replace the original receipt")
        with pytest.raises(ValueError, match="existing_receipt_changed"):
            await recovery.persist_recovery(session, workflow_run_id=case.wid, recovery=repeat)
        await session.rollback()
    async with case.factory() as session:
        run = await session.get(WorkflowRunModel, case.wid)
        assert run.metadata_json[recovery.KEY] == case.prepared
        assert run.metadata_json[checkpoints.CHECKPOINT_KEY] == case.checkpoint
