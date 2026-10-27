"""Actual SQLite ledger/source invariants. No provider, production DB or network."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import CreateTable

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services import chapter_primary_timeout_recovery as recovery
from bestseller.services import llm
from bestseller.services.budget import (
    ExecutionBudgetError,
    execution_operation_id,
)
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
MODELS = (ProjectModel, ChapterModel, ChapterDraftVersionModel, WorkflowRunModel,
          ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel, LlmRunModel)


@compiles(JSONB, "sqlite")
def _jsonb(_type, _compiler, **_kw):
    return "JSON"


@compiles(PGUUID, "sqlite")
def _uuid(_type, _compiler, **_kw):
    return "TEXT"


def paid_result(case, template, digest, key):
    return llm.LLMCompletionResult(content="saved paid review", provider="real-fixture", model_name=case.role.model,
        input_tokens=10, output_tokens=20, finish_reason="stop", metadata={
            "paid_call_origin": {"project_id": str(case.project.id), "workflow_run_id": str(case.owner.id),
                                 "prompt_hash": digest, "logical_role": "critic", "prompt_template": template},
            "effective_input_receipt": {"hash": digest},
            "execution_receipt": {"scope_id": case.scope, "operation_key": key},
        }).model_dump(mode="json")


def operation(case, key, digest, status, result=None):
    return ExecutionOperationModel(key=key, scope_id=case.scope, input_hash=digest, status=status,
        owner="retired-dispatcher", lease_until=datetime.now(UTC) - timedelta(days=1), result_json=result)


@pytest.fixture
async def case(tmp_path, monkeypatch):
    path = tmp_path / "primary_timeout_test.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        for model in MODELS:
            await conn.exec_driver_sql(str(CreateTable(model.__table__).compile(dialect=engine.dialect)).replace("::jsonb", ""))
    session = async_sessionmaker(engine, expire_on_commit=False)()
    st = load_settings(env={"BESTSELLER__DATABASE__URL": json.dumps("sqlite+aiosqlite:///:memory:")})
    role = st.llm.critic.model_copy(update={"model": "openai/fixture-review", "stream": False,
                                           "timeout_seconds": 180, "n_candidates": 1})
    p = ProjectModel(slug="timeout-test", title="test", genre="test", target_word_count=10000, target_chapters=4)
    session.add(p)
    await session.flush()
    c = ChapterModel(project_id=p.id, chapter_number=4, chapter_goal="test", metadata_json={})
    session.add(c)
    await session.flush()
    body = "林青关上闸门。\n潮水从另一侧漫来。"
    d = ChapterDraftVersionModel(project_id=p.id, chapter_id=c.id, version_no=1, content_md=body,
                                is_current=True, promotion_state="candidate")
    owner = WorkflowRunModel(project_id=p.id, scope_id=c.id, scope_type="chapter", workflow_type="chapter_pipeline",
                             status="failed", current_step="review_chapter", metadata_json={})
    session.add_all([d, owner])
    await session.flush()
    scope = "project:" + str(p.id)
    p.metadata_json = {"execution_scope_id": scope}
    owner.metadata_json = {"execution_scope_id": scope}
    body_hash = recovery._body(body)
    cid = hashlib.sha256(f"chapter-critic-cycle.v1\n{p.id}\n{c.id}\n{body_hash}\ninitial".encode()).hexdigest()
    namespace = f"chapter-critic:{cid}:1"
    cycle = {"schema_version": "chapter-critic-cycle.v1", "cycle_id": cid, "source_draft_id": str(d.id),
             "source_body_hash": body_hash, "review_workflow_run_id": str(owner.id),
             "policy_source_hash": "a" * 64, "semantic_dependencies_sha256": "b" * 64,
             "state": "running", "attempts": [{"attempt": 1, "state": "running", "operation_namespace": namespace}]}
    c.metadata_json = {"chapter_critic_cycles": {cid: cycle}, "chapter_review_attempts_active": 9}
    auth = {"mode": "completion_bound", "status": "active", "authorization_ref": "original-human-authority",
            "models": [role.model], "endpoint_hosts": ["fixture.invalid"], "objective": "finish original book"}
    budget = ExecutionBudgetModel(scope_id=scope, max_calls=1, max_tokens=100, calls_used=232,
        tokens_charged=17056408, deadline_at=datetime.now(UTC) - timedelta(days=1),
        status="active", metadata_json={"provider_authorization": auth})
    session.add(budget)
    req = llm.LLMCompletionRequest(logical_role="critic", project_id=p.id, workflow_run_id=owner.id,
        system_prompt="Review full body", user_prompt=body, fallback_response="unavailable", prompt_template="chapter_review",
        prompt_version="7.0-citation-tuple-submission", tools=[{"type": "function", "function": {"name": "submit", "parameters": {}}}],
        tool_choice="required", sample_slot=0, metadata={"primary_review_body_sha256": hashlib.sha256(body.encode()).hexdigest()})
    digest = llm._effective_input_hash(req, role)
    req.operation_id = execution_operation_id(namespace, subject=f"critic:chapter_review:{req.prompt_version}", revision=digest)
    root_key = recovery._key(scope, req.operation_id, 0)
    case = SimpleNamespace(engine=engine, session=session, settings=st, role=role, project=p, chapter=c, draft=d,
        owner=owner, scope=scope, cycle=cycle, cid=cid, namespace=namespace, request=req, digest=digest,
        payload=llm._effective_input_payload(req, role), budget=budget, caches=[])
    root = operation(case, root_key, digest, "stopped")
    old = ExecutionAttemptModel(id="quarantined-timeout", operation_key=root_key, scope_id=scope,
        model_name=role.model, input_hash=digest, reserved_tokens=8192, actual_tokens=None,
        status="reconciled_charged", created_at=datetime.now(UTC))
    tk = hashlib.sha256(f"transport-failure.v1\n{scope}\n{old.id}".encode()).hexdigest()
    transport = operation(case, tk, digest, "transport_failure_receipt", {
        "schema": "transport-failure.v1", "attempt_id": old.id, "operation_key": root_key,
        "project_id": str(p.id), "workflow_run_id": str(owner.id), "logical_role": "critic",
        "prompt_template": "chapter_review", "timeout_seconds": 180, "physical_sends": 1,
        "stream": False, "operator_interruption": False, "partial_stream_observed": False,
        "exception_type": "APITimeoutError", "error_chain": [{"type": "ReadTimeout"}],
    })
    rid = "explicit-dispatcher-exit"
    rk = hashlib.sha256(f"reconciliation.v1\n{scope}\n{rid}".encode()).hexdigest()
    recon = operation(case, rk, "c" * 64, "reconciliation_receipt", {
        "reconciliation_id": rid, "request_hash": "c" * 64, "actor": "human-operator",
        "evidence_kind": "dispatcher_exit", "evidence_sha256": "d" * 64,
        "local_dispatcher_stopped": True, "remote_operation_closed": False,
        "receipt": {"scope_id": scope, "reconciliation_id": rid, "attempt_id": old.id,
            "decision": "quarantine_charged", "remaining_unknown": 0, "remote_outcome": "unknown_quarantined",
            "credit_released": False, "automatic_resume": False, "operation_replay_allowed": False,
            "calls_used": budget.calls_used, "tokens_charged": budget.tokens_charged},
    })
    session.add_all([root, old, transport, recon])
    for i in range(4):
        template = "chapter_commercial_quality_judge" if i < 3 else "chapter_window_quality_judge"
        cr = req.model_copy(update={"prompt_template": template, "prompt_version": "v1", "user_prompt": f"source-{i}",
                                   "sample_slot": f"independent:{i}", "metadata": {"chapter_number": 4}})
        ch = llm._effective_input_hash(cr, role)
        cr.operation_id = execution_operation_id(namespace, subject=f"critic:{template}:v1", revision=ch)
        key = recovery._key(scope, cr.operation_id, cr.sample_slot)
        cache = operation(case, key, ch, "completed", paid_result(case, template, ch, key))
        session.add(cache)
        case.caches.append((cr, cache))
    receipt = {"schema": recovery.SCHEMA, "authorization_ref": "explicit-once-only-replacement", "actor": "main-agent",
        "project_id": str(p.id), "chapter_id": str(c.id), "draft_id": str(d.id), "body_sha256": body_hash,
        "cycle_id": cid, "operation_namespace": namespace, "review_workflow_run_id": str(owner.id),
        "semantic_dependencies_sha256": cycle["semantic_dependencies_sha256"], "policy_source_hash": cycle["policy_source_hash"],
        "scope_id": scope, "provider_authorization_sha256": recovery.content_hash(auth),
        "primary_operation_key": root_key, "primary_attempt_id": old.id, "primary_input_hash": digest,
        "primary_prompt_version": req.prompt_version, "primary_sample_slot": 0,
        "completed_operations": [{"operation_key": o.key, "input_hash": o.input_hash,
                                  "result_sha256": recovery.content_hash(o.result_json)} for _, o in case.caches]}
    case.receipt, case.root, case.old, case.transport, case.recon = receipt, root, old, transport, recon
    await session.commit()
    async def forbidden(*args, **kwargs):
        raise AssertionError("No paid/provider call permitted")
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)
    yield case
    await session.close()
    await engine.dispose()


async def prepare(case, receipt=None):
    return await recovery.prepare_authorization_receipt(case.session, chapter_id=case.chapter.id,
                                                       receipt=receipt or case.receipt)


async def authorize(case):
    proof = await recovery.apply_authorization_receipt(case.session, chapter_id=case.chapter.id, receipt=case.receipt)
    await case.session.commit()
    return proof


async def guard(case, request=None, **kwargs):
    args = {"role_settings": case.role, "effective_payload": case.payload, "scope_id": case.scope, **kwargs}
    return await recovery.guard_prepared_request(case.session, case.settings, request or case.request,
        args.pop("prompt_hash", case.digest), args.pop("semantic_namespace", (case.namespace, 0)), **args)


async def snapshot(case):
    return [await recovery._rows(case.session, model) for model in MODELS]


async def test_prepare_is_select_only_and_failure_never_authorizes(case):
    before = await snapshot(case)
    statements = []
    def track(_conn, _cursor, statement, *_):
        statements.append(statement.lstrip().split()[0].upper())
    event.listen(case.engine.sync_engine, "before_cursor_execute", track)
    try:
        proof = await prepare(case)
        assert await guard(case) is case.request
    finally:
        event.remove(case.engine.sync_engine, "before_cursor_execute", track)
    assert set(statements) == {"SELECT"}
    assert await snapshot(case) == before
    assert not case.session.new and not case.session.dirty
    assert proof["authorized"] and not proof["attempt_reset_allowed"] and not proof["credit_released"]


async def test_explicit_apply_and_successor_copy_preserve_every_other_field_and_spend(case):
    baseline = await snapshot(case)
    proof = await authorize(case)
    a, b = await guard(case), await guard(case)
    assert a is not case.request and a == b
    assert a.operation_id == proof["successor_operation_id"] != case.request.operation_id
    assert {**case.request.model_dump(), "operation_id": a.operation_id} == a.model_dump()
    assert await guard(case, a) == a
    after = await snapshot(case)
    for i, model in enumerate(MODELS):
        if model is not ChapterModel:
            assert after[i] == baseline[i]
    stored = deepcopy(case.chapter.metadata_json)
    del stored["chapter_critic_cycles"][case.cid][recovery.AUTHORIZATION_FIELD]
    assert stored == baseline[1][0]["metadata_json"]
    assert proof == await recovery.apply_authorization_receipt(case.session, chapter_id=case.chapter.id, receipt=case.receipt)


@pytest.mark.parametrize("role,namespace", [("writer", "chapter-critic:any:1"), ("critic", "knowledge:chapter"),
                                          ("critic", None), ("planner", "planning:one")])
async def test_normal_requests_do_not_query_database(case, role, namespace):
    class NoDatabase:
        def __getattr__(self, key):
            raise AssertionError("normal request touched database: " + key)
    request = case.request.model_copy(update={"logical_role": role})
    assert await recovery.guard_prepared_request(NoDatabase(), case.settings, request, case.digest,
        (namespace, 0) if namespace else None) is request


@pytest.mark.parametrize("index", range(4))
async def test_exact_four_paid_caches_pass_and_changed_hash_is_blocked(case, index):
    await authorize(case)
    request, cache = case.caches[index]
    assert await guard(case, request, prompt_hash=cache.input_hash) is request
    with pytest.raises(ExecutionBudgetError, match="cache_input_changed"):
        await guard(case, request, prompt_hash="e" * 64)
    changed = request.model_copy(update={"operation_id": request.operation_id + ":new"})
    with pytest.raises(ExecutionBudgetError, match="unlisted_request"):
        await guard(case, changed, prompt_hash=cache.input_hash)


async def test_unknown_request_only_blocked_inside_authorized_namespace(case):
    await authorize(case)
    changed = case.request.model_copy(update={"prompt_template": "unknown_new_judge"})
    with pytest.raises(ExecutionBudgetError, match="unlisted_request"):
        await guard(case, changed)
    assert await guard(case, changed, semantic_namespace=("chapter-critic:" + "f" * 64 + ":1", 0)) is changed
    assert await guard(case, changed, semantic_namespace=("knowledge:chapter", 0)) is changed


async def test_component_namespace_override_cannot_add_new_commercial_send(case):
    await authorize(case)
    component = hashlib.sha256(json.dumps({"project_id": str(case.project.id), "chapter_id": str(case.chapter.id),
        "body_sha256": case.receipt["body_sha256"]}, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    namespace = f"commercial-component:{component}:round:1"
    request, cache = case.caches[0]
    changed = request.model_copy(update={"operation_id": execution_operation_id(namespace,
        subject=f"critic:{request.prompt_template}:{request.prompt_version}", revision=cache.input_hash)})
    with pytest.raises(ExecutionBudgetError, match="unlisted_request"):
        await guard(case, changed, prompt_hash=cache.input_hash, semantic_namespace=(namespace, 0))
    # A source-exact cache from a component namespace can only reuse its frozen key.
    assert await guard(case, request, prompt_hash=cache.input_hash, semantic_namespace=(namespace, 0)) is request


@pytest.mark.parametrize("field,value", [("state", "completed"), ("source_draft_id", str(uuid4())),
    ("source_body_hash", "f" * 64), ("semantic_dependencies_sha256", "f" * 64),
    ("policy_source_hash", "f" * 64), ("review_workflow_run_id", str(uuid4())),
    ("semantic_dependency_change_detected", True)])
async def test_changed_cycle_fails_closed(case, field, value):
    meta = deepcopy(case.chapter.metadata_json)
    meta["chapter_critic_cycles"][case.cid][field] = value
    case.chapter.metadata_json = meta
    await case.session.commit()
    with pytest.raises(ExecutionBudgetError):
        await prepare(case)


@pytest.mark.parametrize("fault", ["root_completed", "root_result", "root_hash", "duplicate_attempt", "unresolved",
    "wrong_attempt", "missing_transport", "stream", "timeout", "not_timeout", "zero_sends", "credit_returned",
    "no_reconciliation", "wrong_reconciliation_key", "paused_authority", "changed_authority", "changed_body",
    "cache_changed", "cache_stopped", "completed_primary", "another_owner"])
async def test_durable_proof_rejects_source_ledger_and_authority_faults(case, fault):
    if fault == "root_completed": case.root.status = "completed"
    elif fault == "root_result": case.root.result_json = {"content": "late result"}
    elif fault == "root_hash": case.root.input_hash = "f" * 64
    elif fault == "duplicate_attempt":
        case.session.add(ExecutionAttemptModel(id="second-old-send", operation_key=case.root.key, scope_id=case.scope,
            model_name=case.role.model, input_hash=case.digest, status="reconciled_charged", reserved_tokens=10, created_at=datetime.now(UTC)))
    elif fault == "unresolved":
        case.session.add(operation(case, "f" * 64, "e" * 64, "pending"))
    elif fault == "wrong_attempt": case.old.status = "completed"
    elif fault == "missing_transport": await case.session.delete(case.transport)
    elif fault in {"stream", "timeout", "not_timeout", "zero_sends"}:
        t = deepcopy(case.transport.result_json)
        if fault == "stream": t["stream"] = True
        elif fault == "timeout": t["timeout_seconds"] = 360
        elif fault == "zero_sends": t["physical_sends"] = 0
        else: t["exception_type"], t["error_chain"] = "ConnectionError", []
        case.transport.result_json = t
    elif fault == "credit_returned":
        record = deepcopy(case.recon.result_json)
        record["receipt"]["credit_released"] = True
        case.recon.result_json = record
    elif fault == "no_reconciliation": await case.session.delete(case.recon)
    elif fault == "wrong_reconciliation_key": case.recon.key = "f" * 64
    elif fault in {"paused_authority", "changed_authority"}:
        bm = deepcopy(case.budget.metadata_json)
        bm["provider_authorization"]["status" if fault == "paused_authority" else "objective"] = "changed"
        case.budget.metadata_json = bm
    elif fault == "changed_body": case.draft.content_md += "新增正文。"
    elif fault == "cache_changed": case.caches[0][1].result_json = {"content": "tampered"}
    elif fault == "cache_stopped": case.caches[0][1].status = "stopped"
    elif fault == "completed_primary":
        case.session.add(LlmRunModel(project_id=case.project.id, workflow_run_id=case.owner.id, logical_role="critic",
            provider="real-fixture", model_name=case.role.model, prompt_template="chapter_review", prompt_hash=case.digest,
            finish_reason="tool_calls", metadata_json={"primary_review_body_sha256": case.request.metadata["primary_review_body_sha256"]}))
    elif fault == "another_owner":
        case.session.add(WorkflowRunModel(project_id=case.project.id, scope_id=case.chapter.id, scope_type="chapter",
            workflow_type="chapter_pipeline", status="running", metadata_json={"execution_scope_id": case.scope}))
    await case.session.commit()
    with pytest.raises(ExecutionBudgetError):
        await prepare(case)


@pytest.mark.parametrize("status", ["pending", "unknown", "stopped"])
async def test_successor_failure_or_owner_never_gets_a_third_root(case, status):
    proof = await authorize(case)
    successor = operation(case, proof["successor_operation_key"], case.digest, status)
    case.session.add(successor)
    await case.session.commit()
    before = await snapshot(case)
    with pytest.raises(ExecutionBudgetError):
        await guard(case)
    with pytest.raises(ExecutionBudgetError):
        await guard(case, case.request.model_copy(update={"operation_id": proof["successor_operation_id"]}))
    assert await snapshot(case) == before


async def test_completed_successor_uses_same_identity_and_never_mints_another(case):
    proof = await authorize(case)
    successor = operation(case, proof["successor_operation_key"], case.digest, "completed",
        paid_result(case, "chapter_review", case.digest, proof["successor_operation_key"]))
    case.session.add(successor)
    await case.session.commit()
    result = await guard(case)
    assert result.operation_id == proof["successor_operation_id"]
    assert await guard(case, result) == result
    forged = deepcopy(case.receipt)
    forged["primary_operation_key"] = successor.key
    with pytest.raises(ExecutionBudgetError, match="original_identity"):
        await prepare(case, forged)


@pytest.mark.parametrize("fault", ["missing_role", "missing_payload", "deadline", "wire", "hash", "body", "owner", "scope", "sample"])
async def test_prepared_primary_binds_final_transport_and_owner(case, fault):
    await authorize(case)
    kwargs, request = {}, case.request
    if fault == "missing_role": kwargs["role_settings"] = None
    elif fault == "missing_payload": kwargs["effective_payload"] = None
    elif fault == "deadline": kwargs["role_settings"] = case.role.model_copy(update={"timeout_seconds": 360})
    elif fault == "wire": kwargs["effective_payload"] = {**case.payload, "max_tokens": 1}
    elif fault == "hash": kwargs["prompt_hash"] = "f" * 64
    elif fault == "body": request = request.model_copy(update={"metadata": {"primary_review_body_sha256": "f" * 64}})
    elif fault == "owner": request = request.model_copy(update={"workflow_run_id": uuid4()})
    elif fault == "scope": kwargs["scope_id"] = "project:other"
    elif fault == "sample": request = request.model_copy(update={"sample_slot": 1})
    with pytest.raises(ExecutionBudgetError):
        await guard(case, request, **kwargs)


@pytest.mark.parametrize("fault", [None, "scope", "body", "cycle", "two_wrappers"])
async def test_native_pipeline_wrapper_requires_exact_persisted_source_binding(case, fault):
    # Metadata is the actual shape created by run_chapter_pipeline and its review checkpoint.
    wm = {"execution_scope_id": case.scope, "critic_cycle_id": case.cid,
          "review_recovery_source": {"draft_id": str(case.draft.id), "body_hash": case.receipt["body_sha256"]}}
    if fault == "scope": wm["execution_scope_id"] = "another-scope"
    if fault == "body": wm["review_recovery_source"]["body_hash"] = "f" * 64
    if fault == "cycle": wm["critic_cycle_id"] = "f" * 64
    count = 2 if fault == "two_wrappers" else 1
    for _ in range(count):
        case.session.add(WorkflowRunModel(project_id=case.project.id, scope_id=case.chapter.id, scope_type="chapter",
            workflow_type="chapter_pipeline", status="running", metadata_json=deepcopy(wm)))
    await case.session.commit()
    if fault:
        with pytest.raises(ExecutionBudgetError, match="foreign_owner"):
            await prepare(case)
    else:
        proof = await authorize(case)
        assert (await guard(case)).operation_id == proof["successor_operation_id"]


async def test_policy_migration_proves_old_then_new_in_same_transaction_without_credit(case):
    before = deepcopy(case.budget.metadata_json), case.budget.calls_used, case.budget.tokens_charged
    await prepare(case)
    new = deepcopy(case.receipt)
    new["policy_source_hash"] = "e" * 64
    metadata = deepcopy(case.chapter.metadata_json)
    metadata["chapter_critic_cycles"][case.cid]["policy_source_hash"] = new["policy_source_hash"]
    case.chapter.metadata_json = metadata
    await case.session.flush()
    proof = await recovery.apply_authorization_receipt(case.session, chapter_id=case.chapter.id, receipt=new)
    assert proof["cycle_id"] == case.cid
    await case.session.rollback()
    stored = await recovery._rows(case.session, ExecutionBudgetModel)
    assert (stored[0]["metadata_json"], stored[0]["calls_used"], stored[0]["tokens_charged"]) == before
    assert recovery.AUTHORIZATION_FIELD not in (await recovery._rows(case.session, ChapterModel))[0]["metadata_json"]["chapter_critic_cycles"][case.cid]
