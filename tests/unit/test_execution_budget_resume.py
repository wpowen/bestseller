"""Read-only budget-resume proof over real isolated SQLite and native grants.

No model, HTTP, production database, task dispatch or grade is involved. The
positive provider authorization is written by authorize_provider_completion;
negative tests corrupt those fixture rows rather than fabricate valid grants.
"""
from copy import deepcopy
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import hashlib
import importlib
import json
from pathlib import Path
import socket
import threading
from types import SimpleNamespace as NS
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event, inspect, select, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.schema import CreateTable

from bestseller.infra.db import models as m
from bestseller.services import llm
from bestseller.services.budget import (
    ExecutionLedger, completion_authorization, execution_scope_block_code,
)
from bestseller.services.conception_checkpoints import stable_conception_input_hash
from bestseller.services.creation_intent_contract import ConceptionAttemptInput, CreationIntentContract
from bestseller.services.execution_authorization import authorize_provider_completion, set_provider_completion_pause
from bestseller.services.genre_intent_contract import contract_from_selection
from bestseller.services.model_catalog import get_model_catalog_entry
from bestseller.settings import BudgetSettings
from support.database_safety import require_test_engine_url

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
MODEL = "openai/MiniMax-M3.1-Flash"
CODES = ("execution_budget_exhausted", "execution_deadline_exhausted", "execution_downstream_budget_reserved")
TABLES = (m.ProjectModel, m.WorkflowRunModel, m.ExecutionBudgetModel,
          m.ExecutionOperationModel, m.ExecutionAttemptModel, m.LlmRunModel)


@compiles(JSONB, "sqlite")
def sqlite_jsonb(_type, _compiler, **_kwargs):
    return "JSON"


@compiles(PGUUID, "sqlite")
def sqlite_uuid(_type, _compiler, **_kwargs):
    return "TEXT"


def stable_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


@pytest.fixture(autouse=True)
def no_provider_or_network(monkeypatch):
    attempts = []
    def forbidden(*_args, **_kwargs):
        attempts.append(True)
        raise AssertionError("Budget resume tests forbid HTTP/SDK/model calls")
    for name in ("complete_text", "_call_litellm", "_get_litellm", "_paid_transport_attempt"):
        monkeypatch.setattr(llm, name, forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    yield
    assert not attempts, "Swallowed exceptions must not hide an external call"


@pytest.fixture
def api():
    return importlib.import_module("bestseller.services.execution_budget_resume")


@pytest.fixture
async def case(tmp_path):
    path = tmp_path / "execution_budget_resume_test.sqlite3"
    assert path.name.endswith("_test.sqlite3")
    url = f"sqlite+aiosqlite:///{path}"
    require_test_engine_url(url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
    now, tid, wid = datetime.now(UTC), str(uuid4()), uuid4()
    scope = "conception:" + tid
    attempt_id = tid + ":resume:" + uuid4().hex
    contract = CreationIntentContract(genre_intent=contract_from_selection({"channel": "male", "genre": "xianxia"}),
        chapter_count=4, llm_model_id="minimax-m3.1-flash", concept_seed="合成测试：守灯人必须亲手选择熄灭一座灯塔。")
    assert get_model_catalog_entry(contract.llm_model_id).model == MODEL
    payload = {"slug": "synthetic-budget-resume-" + tid[:8], "_run_conception": True,
        "llm_model_id": contract.llm_model_id,
        "_conception_workflow_run_id": str(wid), "_conception_attempt_id": attempt_id,
        "creation_intent_contract": contract.model_dump(mode="json"),
        "premise": "合成测试：守灯人必须亲手选择熄灭一座灯塔。",
        "user_hints": {"production_profile": "complete"}, "genre": "仙侠", "sub_genre": "修行",
        "target_chapters": 4}
    attempt = ConceptionAttemptInput(contract=contract,
        input_payload={k: deepcopy(payload[k]) for k in ("premise", "user_hints", "genre", "sub_genre", "target_chapters")},
        attempt_id=attempt_id, idempotency_key=attempt_id)
    task = {"task_id": tid, "task_type": "autowrite", "status": "failed",
        "created_at": (now - timedelta(minutes=5)).isoformat(),
        "updated_at": (now - timedelta(minutes=1)).isoformat(), "project_slug": payload["slug"],
        "cancel_requested": False, "current_stage": CODES[0], "error": CODES[0],
        "result": {"execution_terminal": {"terminal": True, "code": CODES[0]}},
        "payload": payload}
    try:
        async with engine.begin() as connection:
            for model in TABLES:
                ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
                await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
            databases = (await connection.execute(text("PRAGMA database_list"))).all()
            assert Path(next(row[2] for row in databases if row[1] == "main")) == path
        async with factory() as session, session.begin():
            session.add(m.WorkflowRunModel(id=wid, project_id=None, workflow_type="conception_initial",
                status="failed", current_step="failed", error_message=CODES[0],
                metadata_json={"execution_scope_id": scope, "attempt_id": attempt_id,
                    "creation_intent_hash": contract.contract_hash(), "creation_intent_schema": contract.schema_version,
                    "input_hash": attempt.input_hash(), "conception_checkpoint_input_hash": stable_conception_input_hash(attempt)}))
            session.add(m.ExecutionBudgetModel(scope_id=scope, max_calls=64, max_tokens=1_000_000,
                calls_used=64, tokens_charged=1_000_000, status="exhausted",
                deadline_at=now - timedelta(hours=1), metadata_json={"fixture": "retain-original-spend"}))
        receipt = await authorize_provider_completion(engine, scope_id=scope, idempotency_key="native-M31-completion",
            actor="synthetic-human-operator", authorization_ref="synthetic-explicit-user-continuation",
            objective="Resume the same bounded fixture task", expected_max_calls=64,
            expected_max_tokens=1_000_000, model=MODEL)
        async with factory() as session:
            row = await session.get(m.ExecutionBudgetModel, scope)
            assert completion_authorization(row) == receipt["authorization"]
            assert execution_scope_block_code(row) is None
        yield NS(engine=engine, factory=factory, path=path, task_id=tid, wid=wid, scope=scope,
                 task=task, payload=payload, contract=contract, attempt=attempt, receipt=receipt)
    finally:
        await engine.dispose()


async def state(case):
    result = {}
    async with case.factory() as session:
        for model in TABLES:
            rows = (await session.execute(select(model.__table__))).mappings().all()
            result[model.__tablename__] = sorted(json.dumps(dict(row), sort_keys=True, default=str) for row in rows)
    return result


async def prepare(api, case, *, task=None, task_id=None):
    snapshot = case.task if task is None else task
    before, task_before = await state(case), deepcopy(snapshot)
    statements = []
    raw_statements = []
    def raw_read_only(_connection, _cursor, statement, _parameters, _context, _executemany):
        raw_statements.append(statement)
        assert statement.lstrip().upper().startswith("SELECT"), "Resume proof executed non-SELECT driver SQL"
    async with case.factory() as session:
        def read_only(execution):
            statements.append(execution.statement)
            assert execution.is_select, "Resume proof performed non-SELECT SQL"
            assert getattr(execution.statement, "_for_update_arg", None) is None, "Resume proof acquired a row lock"
        def no_mutation(*_args, **_kwargs):
            pytest.fail("Read-only resume proof flushed or committed")
        event.listen(session.sync_session, "do_orm_execute", read_only)
        event.listen(session.sync_session, "before_flush", no_mutation)
        event.listen(session.sync_session, "before_commit", no_mutation)
        event.listen(case.engine.sync_engine, "before_cursor_execute", raw_read_only)
        try:
            return await api.prepare_authorized_budget_resume(session,
                task_id=case.task_id if task_id is None else task_id, task_snapshot=snapshot)
        finally:
            event.remove(case.engine.sync_engine, "before_cursor_execute", raw_read_only)
            assert not session.new and not session.dirty and not session.deleted
            assert snapshot == task_before
            assert await state(case) == before


async def edit(case, model, key, change):
    async with case.factory() as session, session.begin():
        row = await session.get(model, key)
        change(row)
        for attr in inspect(row).mapper.column_attrs:
            if attr.key == "metadata_json":
                flag_modified(row, attr.key)


async def test_native_completion_authorization_opens_spent_scope_without_changing_limits_or_usage(case):
    async with case.factory() as session:
        row = await session.get(m.ExecutionBudgetModel, case.scope)
        assert (row.max_calls, row.max_tokens, row.calls_used, row.tokens_charged) == (64, 1_000_000, 64, 1_000_000)
        assert row.deadline_at.replace(tzinfo=UTC) < datetime.now(UTC)
        assert execution_scope_block_code(row) is None
        assert completion_authorization(row)["models"] == [MODEL]
        assert row.metadata_json["provider_authorizations"]["native-M31-completion"] == case.receipt
        assert case.receipt["request"]["model"] == MODEL
        assert case.receipt["before"]["deadline_at"] == row.deadline_at.isoformat()


@pytest.mark.parametrize("code", CODES)
async def test_real_native_authorization_prepares_only_original_task_without_new_credit(api, case, code):
    case.task.update(current_stage=code, error=code)
    case.task["result"]["execution_terminal"]["code"] = code
    proof = await prepare(api, case)
    assert isinstance(proof, dict)
    assert proof["task_snapshot_sha256"] == digest(case.task)
    assert proof["provider_calls"] == 0
    assert proof["new_credit_allowed"] is False
    assert proof["automatic_resume"] is False
    assert proof["native_pause_resume_pair"] is None
    assert proof["actual_authorization_receipt"] == case.receipt
    assert proof["actual_authorization_receipt_sha256"] == digest(case.receipt)
    assert proof["scope_id"] == proof["fresh_scope"]["scope_id"] == case.scope
    assert proof["source_workflow_run_id"] == str(case.wid)
    assert proof["fresh_scope"]["status"] == "active"
    assert proof["fresh_scope"]["max_calls"] == proof["fresh_scope"]["calls_used"] == 64
    assert proof["fresh_scope"]["max_tokens"] == proof["fresh_scope"]["tokens_charged"] == 1_000_000
    assert proof["fresh_scope"]["provider_authorization"]["models"] == [MODEL]
    assert proof["stable_input_hash"] == stable_conception_input_hash(case.attempt)
    assert case.payload["_conception_attempt_id"] == case.attempt.attempt_id


@pytest.mark.parametrize("fault", ["stale_auth", "later_stop", "cancel_requested", "cancelled_task",
    "wrong_type", "wrong_task_id", "not_uuid", "nonterminal", "unknown_terminal", "changed_payload",
    "changed_contract", "missing_source", "project_payload"])
async def test_task_snapshot_drift_or_non_budget_stop_never_prepares_resume(api, case, fault):
    task = deepcopy(case.task)
    tid = case.task_id
    if fault == "stale_auth": task["updated_at"] = (datetime.fromisoformat(case.receipt["authorized_at"]) + timedelta(seconds=1)).isoformat()
    elif fault == "later_stop":
        task.update(current_stage="execution_result_unknown", error="execution_result_unknown")
        task["result"]["execution_terminal"]["code"] = "execution_result_unknown"
    elif fault == "cancel_requested": task["cancel_requested"] = True
    elif fault == "cancelled_task": task["status"] = "cancelled"
    elif fault == "wrong_type": task["task_type"] = "repair"
    elif fault == "wrong_task_id": task["task_id"] = str(uuid4())
    elif fault == "not_uuid": tid = task["task_id"] = "synthetic-not-a-uuid"
    elif fault == "nonterminal": task["result"]["execution_terminal"]["terminal"] = False
    elif fault == "unknown_terminal": task["result"]["execution_terminal"]["code"] = "execution_result_unknown"
    elif fault == "changed_payload": task["payload"]["premise"] += "changed"
    elif fault == "changed_contract": task["payload"]["creation_intent_contract"]["chapter_count"] = 5
    elif fault == "missing_source": task["payload"].pop("_conception_workflow_run_id")
    elif fault == "project_payload": task["payload"]["_run_conception"] = False
    with pytest.raises(api.ExecutionBudgetResumeRejected):
        await prepare(api, case, task=task, task_id=tid)


@pytest.mark.parametrize("fault", ["scope", "intent", "stable_hash", "project", "active", "kind"])
async def test_foreign_or_changed_source_workflow_is_rejected(api, case, fault):
    def change(row):
        if fault == "scope": row.metadata_json = {**row.metadata_json, "execution_scope_id": "conception:" + str(uuid4())}
        elif fault == "intent": row.metadata_json = {**row.metadata_json, "creation_intent_hash": "0" * 64}
        elif fault == "stable_hash": row.metadata_json = {**row.metadata_json, "conception_checkpoint_input_hash": "0" * 64}
        elif fault == "project": row.project_id = uuid4()
        elif fault == "active": row.status = "running"
        elif fault == "kind": row.workflow_type = "chapter_pipeline"
    await edit(case, m.WorkflowRunModel, case.wid, change)
    with pytest.raises(api.ExecutionBudgetResumeRejected):
        await prepare(api, case)


async def test_top_level_model_cannot_override_frozen_creation_contract(api, case):
    task = deepcopy(case.task)
    task["payload"]["llm_model_id"] = "minimax-m3"
    assert task["payload"]["creation_intent_contract"]["llm_model_id"] == "minimax-m3.1-flash"
    with pytest.raises(api.ExecutionBudgetResumeRejected, match="creation_model_route"):
        await prepare(api, case, task=task)


async def test_matching_foreign_contract_and_native_catalog_still_cannot_use_M31_grant(api, case):
    task = deepcopy(case.task)
    raw_contract = case.contract.model_dump(mode="json")
    raw_contract["llm_model_id"] = "minimax-m3"
    contract = CreationIntentContract.model_validate(raw_contract)
    assert get_model_catalog_entry(contract.llm_model_id).model == "openai/MiniMax-M3"
    task["payload"]["llm_model_id"] = contract.llm_model_id
    task["payload"]["creation_intent_contract"] = contract.model_dump(mode="json")
    attempt = ConceptionAttemptInput(contract=contract, attempt_id=case.attempt.attempt_id,
        idempotency_key=case.attempt.idempotency_key,
        input_payload={k: deepcopy(task["payload"][k]) for k in
                       ("premise", "user_hints", "genre", "sub_genre", "target_chapters")})
    def bind_actual_source(row):
        row.metadata_json = {**row.metadata_json, "creation_intent_hash": contract.contract_hash(),
            "input_hash": attempt.input_hash(), "conception_checkpoint_input_hash": stable_conception_input_hash(attempt)}
    await edit(case, m.WorkflowRunModel, case.wid, bind_actual_source)
    with pytest.raises(api.ExecutionBudgetResumeRejected, match="creation_model_catalog"):
        await prepare(api, case, task=task)


@pytest.mark.parametrize("fault", ["unknown", "cancelled", "mode", "model", "endpoint", "provider", "paused",
    "policy_reference", "duplicate", "receipt_policy", "receipt_request", "receipt_timestamp", "caps", "deadline", "usage"])
async def test_native_policy_and_receipt_drift_reject_without_writes(api, case, fault):
    def change(row):
        meta = deepcopy(row.metadata_json)
        policy = meta["provider_authorization"]
        receipt = meta["provider_authorizations"]["native-M31-completion"]
        if fault in {"unknown", "cancelled"}: row.status = fault
        elif fault == "mode": policy["mode"] = "other"
        elif fault == "model": policy["models"] = ["openai/MiniMax-M3"]
        elif fault == "endpoint": policy["endpoint_hosts"] = ["unexpected.invalid"]
        elif fault == "provider": policy["provider"] = "other"
        elif fault == "paused": policy["status"] = "paused"
        elif fault == "policy_reference": policy["authorization_ref"] = "different"
        elif fault == "duplicate": meta["provider_authorizations"]["duplicate"] = deepcopy(receipt)
        elif fault == "receipt_policy": receipt["policy"] = "invented-policy"
        elif fault == "receipt_request": receipt["request"]["model"] = "other"
        elif fault == "receipt_timestamp": receipt["authorized_at"] = "invalid"
        elif fault == "caps": row.max_calls += 1
        elif fault == "deadline": row.deadline_at += timedelta(seconds=1)
        elif fault == "usage": row.calls_used = receipt["before"]["calls_used"] - 1
        row.metadata_json = meta
    await edit(case, m.ExecutionBudgetModel, case.scope, change)
    with pytest.raises(api.ExecutionBudgetResumeRejected):
        await prepare(api, case)


@pytest.mark.parametrize("kind,status", [("root", "pending"), ("root", "unknown"),
                                         ("attempt", "reserved"), ("attempt", "unknown")])
async def test_unresolved_paid_state_is_never_cleared_or_reclassified(api, case, kind, status):
    now = datetime.now(UTC)
    async with case.factory() as session, session.begin():
        session.add(m.ExecutionOperationModel(key="synthetic-root", scope_id=case.scope, input_hash="h" * 64,
            status=status if kind == "root" else "completed", owner="fixture", lease_until=now))
        if kind == "attempt":
            session.add(m.ExecutionAttemptModel(id="synthetic-hold", operation_key="synthetic-root", scope_id=case.scope,
                model_name=MODEL, input_hash="h" * 64, reserved_tokens=100, actual_tokens=None,
                status=status, created_at=now))
    with pytest.raises(api.ExecutionBudgetResumeRejected):
        await prepare(api, case)


async def test_another_active_workflow_on_original_scope_blocks_resume(api, case):
    async with case.factory() as session, session.begin():
        session.add(m.WorkflowRunModel(id=uuid4(), project_id=None, workflow_type="conception_initial",
            status="running", metadata_json={"execution_scope_id": case.scope}))
    with pytest.raises(api.ExecutionBudgetResumeRejected):
        await prepare(api, case)


async def test_materialized_project_with_same_slug_blocks_projectless_resume(api, case):
    async with case.factory() as session, session.begin():
        session.add(m.ProjectModel(id=uuid4(), slug=case.payload["slug"], title="Synthetic materialized project",
            genre="仙侠", target_chapters=4, target_word_count=8000, metadata_json={}))
    with pytest.raises(api.ExecutionBudgetResumeRejected):
        await prepare(api, case)


async def test_same_scope_stopped_root_without_attempts_is_allowed_and_native_reclaim_costs_nothing(api, case):
    operation_id, input_hash = "synthetic-stopped-unsent", "a" * 64
    key = hashlib.sha256((case.scope + "\n" + operation_id).encode()).hexdigest()
    async with case.factory() as session, session.begin():
        session.add(m.ExecutionOperationModel(key=key, scope_id=case.scope, input_hash=input_hash,
            status="stopped", owner="prior-fixture-owner", lease_until=datetime.now(UTC) - timedelta(minutes=5)))
    proof = await prepare(api, case)
    assert proof["provider_calls"] == 0 and proof["new_credit_allowed"] is False
    assert proof["operation_count"] == 1 and proof["attempt_count"] == 0
    witness, = proof["no_attempt_stopped_operations"]
    assert witness["row"]["key"] == key and witness["row"]["status"] == "stopped"
    assert witness["row_sha256"] == digest(witness["row"])
    async with case.factory() as session:
        original = await session.get(m.ExecutionBudgetModel, case.scope)
        usage = original.calls_used, original.tokens_charged
    ledger = ExecutionLedger(case.engine, case.scope, BudgetSettings())
    claim = await ledger.claim(operation_id, input_hash)
    assert claim.key == key and claim.cached_result is None
    async with case.factory() as session:
        row = await session.get(m.ExecutionBudgetModel, case.scope)
        assert (row.calls_used, row.tokens_charged) == usage
        assert (await session.get(m.ExecutionOperationModel, key)).status == "pending"
    await ledger.finish(claim, None)


@pytest.fixture
def web_owner(case, tmp_path, monkeypatch):
    from bestseller.web import server
    manager = server.WebTaskManager(persist_path=tmp_path / "budget_resume_tasks_test.json")
    task = server.WebTaskState(**deepcopy(case.task))
    task.result["preserved_result"] = {"failed_title": "keep exact", "attempts": 24}
    task.record_event("synthetic_original_stop", {"keep": "history"}, milestone=True)
    manager._tasks[case.task_id] = task
    manager._save_to_disk()  # Actual local persistence, never patched.
    owner = NS(server=server, manager=manager, task=task, sessions=[], after_proof=None, dispatches=[])
    settings_token = object()
    def no_dispatch(*args, **kwargs):
        owner.dispatches.append((args, kwargs))
        pytest.fail("Rearming a terminal is not authorization to dispatch")
    monkeypatch.setattr(server, "threading", NS(**{**vars(threading), "Thread": no_dispatch}))
    monkeypatch.setattr(server, "load_settings", lambda: settings_token)
    @asynccontextmanager
    async def scope(actual_settings):
        assert actual_settings is settings_token
        async with case.factory() as session:
            owner.sessions.append(session)
            def read_only(execution):
                assert execution.is_select
                assert getattr(execution.statement, "_for_update_arg", None) is None
            def no_mutation(*_args, **_kwargs):
                pytest.fail("Real manager proof must not flush or commit DB state")
            event.listen(session.sync_session, "do_orm_execute", read_only)
            event.listen(session.sync_session, "before_flush", no_mutation)
            event.listen(session.sync_session, "before_commit", no_mutation)
            yield session
            if owner.after_proof is not None:
                owner.after_proof()  # Real task race, after real helper returned.
    monkeypatch.setattr(server, "session_scope", scope)
    return owner


async def test_actual_manager_rearms_only_terminal_and_journals_full_result_without_dispatch(case, web_owner):
    owner = web_owner
    before = deepcopy(owner.manager.get_task(case.task_id))
    db_before = await state(case)
    assert await owner.manager.rearm_authorized_budget_resume(case.task_id) is True
    current = owner.manager.get_task(case.task_id)
    assert current["task_id"] == before["task_id"] and current["created_at"] == before["created_at"]
    assert current["status"] == "failed" and current["cancel_requested"] is False
    assert current["payload"] == before["payload"]
    assert current["payload"]["_conception_attempt_id"] == case.attempt.attempt_id
    assert current["current_stage"] == "execution_budget_authorized_manual_resume_required"
    assert current["error"] is None and "execution_terminal" not in current["result"]
    receipt = current["result"]["last_execution_budget_authorization"]
    assert receipt["task_snapshot_sha256"] == digest(before)
    assert receipt["actual_authorization_receipt"] == case.receipt
    assert current["result"] == {**{k: v for k, v in before["result"].items() if k != "execution_terminal"},
                                 "last_execution_budget_authorization": receipt}
    event_record = current["milestone_events"][-1]
    assert event_record["stage"] == "execution_budget_authorization_applied"
    assert event_record["payload"]["previous_terminal"] == before["result"]
    assert event_record["payload"]["previous_error"] == before["error"]
    assert event_record["payload"]["previous_stage"] == before["current_stage"]
    assert event_record["payload"]["receipt"] == receipt
    assert current["milestone_events"][:-1] == before["milestone_events"]
    assert json.loads(owner.manager._persist_path.read_text()) == [current]
    assert not owner.dispatches and not owner.manager._executing_task_ids
    assert not owner.manager._pending_auto_resume_ids and len(owner.sessions) == 1
    assert await state(case) == db_before


async def test_actual_manager_cas_preserves_new_stop_after_real_proof(case, web_owner):
    owner = web_owner
    persisted = owner.manager._persist_path.read_bytes()
    db_before = await state(case)
    def stop_after_proof():
        with owner.manager._lock:
            owner.task.result = {**owner.task.result,
                "execution_terminal": {"terminal": True, "code": "execution_result_unknown"}}
            owner.task.error = owner.task.current_stage = "execution_result_unknown"
            owner.task.updated_at = datetime.now(UTC).isoformat()
    owner.after_proof = stop_after_proof
    with pytest.raises(ValueError, match="task changed"):
        await owner.manager.rearm_authorized_budget_resume(case.task_id)
    current = owner.manager.get_task(case.task_id)
    assert current["result"]["execution_terminal"]["code"] == "execution_result_unknown"
    assert "last_execution_budget_authorization" not in current["result"]
    assert all(e["stage"] != "execution_budget_authorization_applied" for e in current["milestone_events"])
    assert owner.manager._persist_path.read_bytes() == persisted
    assert not owner.dispatches and len(owner.sessions) == 1
    assert await state(case) == db_before


@pytest.mark.parametrize("busy", [False, True])
async def test_actual_manager_missing_or_executing_task_returns_false_without_proof(case, web_owner, busy):
    owner = web_owner
    if busy:
        owner.manager._executing_task_ids.add(case.task_id)
    else:
        owner.manager._tasks.pop(case.task_id)
    persisted = owner.manager._persist_path.read_bytes()
    assert await owner.manager.rearm_authorized_budget_resume(case.task_id) is False
    assert not owner.sessions and not owner.dispatches
    assert owner.manager._persist_path.read_bytes() == persisted


async def test_actual_manager_retains_task_and_persisted_history_when_native_proof_rejects(api, case, web_owner):
    owner = web_owner
    await edit(case, m.ExecutionBudgetModel, case.scope, lambda row: setattr(row, "status", "unknown"))
    before = deepcopy(owner.manager.get_task(case.task_id))
    persisted = owner.manager._persist_path.read_bytes()
    with pytest.raises(api.ExecutionBudgetResumeRejected):
        await owner.manager.rearm_authorized_budget_resume(case.task_id)
    assert owner.manager.get_task(case.task_id) == before
    assert owner.manager._persist_path.read_bytes() == persisted
    assert not owner.dispatches and len(owner.sessions) == 1


async def native_pause(case, *, paused):
    return await set_provider_completion_pause(case.engine, scope_id=case.scope, paused=paused,
        actor="synthetic-human-operator", reason="Synthetic manual handoff; same unfinished grant")


async def prepare_paused_case(case, *, prior_cycles=0):
    for _ in range(prior_cycles):
        await native_pause(case, paused=True)
        await native_pause(case, paused=False)
    paused = await native_pause(case, paused=True)
    stopped_at = datetime.now(UTC)
    code = "execution_authorization_paused"
    case.task.update(current_stage=code, error=code, updated_at=stopped_at.isoformat())
    case.task["result"]["execution_terminal"] = {"terminal": True, "code": code}
    active = await native_pause(case, paused=False)
    history = active["history"]
    assert datetime.fromisoformat(case.receipt["authorized_at"]) < datetime.fromisoformat(paused["history"][-1]["at"])
    assert datetime.fromisoformat(history[-2]["at"]) <= stopped_at < datetime.fromisoformat(history[-1]["at"])
    case.native_pause_history = deepcopy(history)
    return case


@pytest.fixture
async def paused_case(case):
    return await prepare_paused_case(case)


@pytest.mark.parametrize("prior_cycles", [0, 1])
async def test_native_unpause_proves_same_grant_without_new_authorization_or_usage(api, case, prior_cycles):
    await prepare_paused_case(case, prior_cycles=prior_cycles)
    before = await state(case)
    proof = await prepare(api, case)
    pair = proof["native_pause_resume_pair"]
    assert pair == {"paused": case.native_pause_history[-2], "active": case.native_pause_history[-1],
                    "history_sha256": digest(case.native_pause_history)}
    assert proof["terminal_code"] == "execution_authorization_paused"
    assert proof["task_snapshot_sha256"] == digest(case.task)
    assert proof["actual_authorization_receipt"] == case.receipt
    assert proof["actual_authorization_receipt_sha256"] == digest(case.receipt)
    assert proof["previous_terminal_snapshot"]["result"] == case.task["result"]
    assert proof["provider_calls"] == 0 and proof["new_credit_allowed"] is False
    assert proof["automatic_resume"] is False
    assert await state(case) == before
    async with case.factory() as session:
        row = await session.get(m.ExecutionBudgetModel, case.scope)
        assert list(row.metadata_json["provider_authorizations"]) == ["native-M31-completion"]
        assert row.metadata_json["provider_authorizations"]["native-M31-completion"] == case.receipt
        assert (row.calls_used, row.tokens_charged, row.max_calls, row.max_tokens) == (64, 1_000_000, 64, 1_000_000)


@pytest.mark.parametrize("fault", ["no_history", "wrong_order", "stale_latest_pair", "currently_paused",
    "blank_actor", "blank_reason", "extra_key", "invalid_timestamp", "equal_timestamps",
    "future_timestamp", "stop_after_active", "cancelled"])
async def test_paused_resume_requires_complete_current_native_history_and_latest_covering_pair(api, case, fault):
    # Four genuine transitions ensure old entries are validated too, rather
    # than trusting only an otherwise valid last pair.
    await prepare_paused_case(case, prior_cycles=1)
    if fault == "stale_latest_pair":
        await native_pause(case, paused=True)
        await native_pause(case, paused=False)
    elif fault == "currently_paused":
        await native_pause(case, paused=True)
    elif fault == "stop_after_active":
        case.task["updated_at"] = datetime.now(UTC).isoformat()
    elif fault == "cancelled":
        case.task["status"] = "cancelled"
        case.task["cancel_requested"] = True
    else:
        def corrupt_history(row):
            history = row.metadata_json["provider_pause_history"]
            if fault == "no_history": row.metadata_json.pop("provider_pause_history")
            elif fault == "wrong_order": history[0]["status"] = "active"
            elif fault == "blank_actor": history[0]["actor"] = " "
            elif fault == "blank_reason": history[0]["reason"] = ""
            elif fault == "extra_key": history[0]["untrusted"] = True
            elif fault == "invalid_timestamp": history[0]["at"] = "not-a-timestamp"
            elif fault == "equal_timestamps": history[1]["at"] = history[0]["at"]
            elif fault == "future_timestamp": history[-1]["at"] = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
            else: raise AssertionError(fault)
        await edit(case, m.ExecutionBudgetModel, case.scope, corrupt_history)
    with pytest.raises(api.ExecutionBudgetResumeRejected):
        await prepare(api, case)


@pytest.mark.parametrize("code", CODES)
async def test_old_budget_stops_cannot_use_unpause_in_place_of_a_newer_grant(api, paused_case, code):
    case = paused_case
    assert datetime.fromisoformat(case.receipt["authorized_at"]) < datetime.fromisoformat(case.task["updated_at"])
    case.task.update(current_stage=code, error=code)
    case.task["result"]["execution_terminal"]["code"] = code
    with pytest.raises(api.ExecutionBudgetResumeRejected, match="authorization_not_newer_than_stop"):
        await prepare(api, case)


@pytest.mark.parametrize("guard", ["unknown_scope", "reserved_attempt"])
async def test_native_unpause_does_not_bypass_unknown_or_inflight_fences(api, paused_case, guard):
    case = paused_case
    if guard == "unknown_scope":
        await edit(case, m.ExecutionBudgetModel, case.scope, lambda row: setattr(row, "status", "unknown"))
    else:
        now = datetime.now(UTC)
        async with case.factory() as session, session.begin():
            session.add(m.ExecutionOperationModel(key="paused-inflight-root", scope_id=case.scope,
                input_hash="a" * 64, status="completed", owner="fixture", lease_until=now))
            session.add(m.ExecutionAttemptModel(id="paused-reserved-hold", operation_key="paused-inflight-root",
                scope_id=case.scope, input_hash="a" * 64, model_name=MODEL, status="reserved",
                reserved_tokens=100, actual_tokens=None, created_at=now))
    with pytest.raises(api.ExecutionBudgetResumeRejected):
        await prepare(api, case)


async def test_actual_manager_rearms_native_paused_terminal_without_reauthorizing_or_dispatching(paused_case, web_owner):
    case, owner = paused_case, web_owner
    before = deepcopy(owner.manager.get_task(case.task_id))
    assert before["current_stage"] == "execution_authorization_paused"
    db_before = await state(case)
    assert await owner.manager.rearm_authorized_budget_resume(case.task_id) is True
    current = owner.manager.get_task(case.task_id)
    receipt = current["result"]["last_execution_budget_authorization"]
    assert receipt["actual_authorization_receipt"] == case.receipt
    assert receipt["task_snapshot_sha256"] == digest(before)
    assert receipt["native_pause_resume_pair"] == {
        "paused": case.native_pause_history[-2], "active": case.native_pause_history[-1],
        "history_sha256": digest(case.native_pause_history)}
    assert "execution_terminal" not in current["result"]
    assert current["result"]["preserved_result"] == before["result"]["preserved_result"]
    assert current["payload"] == before["payload"] and current["status"] == "failed"
    assert current["error"] is None
    assert current["milestone_events"][-1]["payload"]["previous_terminal"] == before["result"]
    assert current["milestone_events"][-1]["payload"]["receipt"] == receipt
    assert json.loads(owner.manager._persist_path.read_text()) == [current]
    assert not owner.dispatches and not owner.manager._executing_task_ids
    assert await state(case) == db_before
