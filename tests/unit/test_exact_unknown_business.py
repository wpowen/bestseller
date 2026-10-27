"""File SQLite, real stable entry, denied gateway. No physical return or grade.

Approval is synthetic operator consent in this isolated DB, never production
consent. Real rendered request/source hashes are captured before dispatch.
"""
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
from uuid import UUID

import pytest
from sqlalchemy import select
from test_commercial_component_cohort import cohort, _judge as original_judge, SCOPE  # noqa: F401

from bestseller.infra.db.models import (
    ExecutionOperationModel, ExecutionAttemptModel, ExecutionBudgetModel, LlmRunModel, WorkflowRunModel,
)
from bestseller.services import commercial_judge_recovery as runtime
from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services import exact_unknown_business as exact
from bestseller.services.commercial_protocol_transition_runtime import _row
from bestseller.services.execution_authorization import authorize_provider_completion
from bestseller.services.operator_action_approval import record_operator_action_approval

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
ROOT = hashlib.sha256(b"isolated unknown request, no origin witness").hexdigest()


class DeniedDispatch(BaseException):
    pass


class CapturedInput(BaseException):
    pass


async def _judge(r, **kwargs):
    for obj in (r.project, r.chapter, r.draft, r.owner, r.budget):
        await r.session.refresh(obj)
    return await original_judge(r, **kwargs)


@pytest.fixture
async def target(cohort, monkeypatch):  # noqa: F811 - imported pytest fixture
    r = cohort
    r.settings.llm.critic.model = "openai/MiniMax-M3"
    r.settings.llm.critic.model_override = None
    r.settings.llm.critic.api_base = "https://api.minimaxi.com/v1"
    r.project.metadata_json = {**r.project.metadata_json, "execution_scope_id": SCOPE}
    await r.session.commit()
    await authorize_provider_completion(r.engine, scope_id=SCOPE, idempotency_key="isolated",
        actor="isolated-human", authorization_ref="synthetic fixture authorization only",
        objective="isolated exact target", expected_max_calls=100, expected_max_tokens=100000)
    r.session.expire_all()
    # ORM IDs already committed, but expire_all would require async refresh.
    for obj in (r.project, r.chapter, r.draft, r.owner, r.budget):
        await r.session.refresh(obj)
    r.session.add(ExecutionOperationModel(key=ROOT, scope_id=SCOPE, input_hash="b" * 64,
        status="stopped", owner="stopped isolated owner", lease_until=datetime.now(UTC), result_json=None))
    await r.session.commit()
    captured, dispatch = {}, []
    original_prepare = runtime.prepare_commercial_component_slot

    async def capture(session, **kwargs):
        captured.update(kwargs)
        raise CapturedInput()

    async def deny(*args, **kwargs):
        dispatch.append(args[-1] if args else kwargs)
        raise DeniedDispatch()

    monkeypatch.setattr(runtime, "prepare_commercial_component_slot", capture)
    monkeypatch.setattr(judge, "complete_text", deny)
    with pytest.raises(CapturedInput):
        await _judge(r, bound=True)
    await r.session.rollback()
    monkeypatch.setattr(runtime, "prepare_commercial_component_slot", original_prepare)
    r.target = {k: captured[k] for k in (
        "project_id", "chapter_id", "draft_id", "sources", "evaluation_contract", "current_input_hash", "effective_critic", "samples")}
    r.target["scope"] = SCOPE
    r.dispatch = dispatch
    return r


async def snapshot(r):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    async with async_sessionmaker(r.engine, expire_on_commit=False)() as session:
        return {m.__tablename__: [_row(row) for row in await session.scalars(select(m))]
            for m in (ExecutionOperationModel, ExecutionAttemptModel, ExecutionBudgetModel, LlmRunModel)}


async def authorize(r):
    p = await exact.prepare_exact_unknown_business(r.session, root_keys=[ROOT], **r.target)
    aid = await record_operator_action_approval(r.session, purpose="exact_unknown_new_business",
        binding=exact.approval_binding(p), capabilities=["isolate_unknown"], actor="isolated-human",
        authorization_ref="synthetic consent in file SQLite only", explicit_user_approval=True)
    await r.session.commit()
    receipt = await exact.persist_exact_unknown_business(r.session, root_keys=[ROOT], **r.target,
        approval_id=aid, expected_plan_sha256=p["plan_sha256"])
    await r.session.commit()
    return p, aid, receipt


async def test_explicit_exact_target_reaches_denied_dispatch_and_reuses_same_slot(target):
    r = target
    before = await snapshot(r)
    p, aid, receipt = await authorize(r)
    for _ in range(2):
        with pytest.raises(DeniedDispatch):
            await _judge(r, bound=True)
        await r.session.rollback()
    assert len(r.dispatch) == 2
    assert await snapshot(r) == before  # No claimed call, fake actual, grade, released credit.
    await r.session.refresh(r.chapter)
    pointer = r.chapter.metadata_json["commercial_component_cohorts"]
    assert list(pointer) == [p["binding"]["stable_cohort_key"]]
    await r.session.refresh(r.owner)
    journal = r.owner.metadata_json["commercial_component_cohorts"][next(iter(pointer))]
    assert journal["exact_unknown_business"]["receipt_id"] == receipt["receipt_id"]
    assert len(journal["slots"]) == 1
    assert next(iter(journal["slots"].values()))["used_attempts"] == 1
    assert r.dispatch[0].sample_slot == r.dispatch[1].sample_slot
    # This boundary precedes complete_text identity assignment. Real nonempty
    # operation identities are asserted separately in the synthetic-ledger test.
    assert all(request.operation_id is None for request in r.dispatch)
    assert (await exact.persist_exact_unknown_business(r.session, root_keys=[ROOT], **r.target,
        approval_id=aid, expected_plan_sha256=p["plan_sha256"]))["status"] == "already_persisted"


async def test_no_approval_is_not_an_active_budget_exception(target):
    r = target
    before = await snapshot(r)
    p = await exact.prepare_exact_unknown_business(r.session, root_keys=[ROOT], **r.target)
    with pytest.raises(ValueError, match="approval_missing"):
        await exact.persist_exact_unknown_business(r.session, root_keys=[ROOT], **r.target,
            expected_plan_sha256=p["plan_sha256"])
    value = await _judge(r, bound=True)
    assert value.infrastructure_failure and not r.dispatch
    await r.session.rollback()
    assert await snapshot(r) == before


@pytest.mark.parametrize("drift", ["input", "source", "critic", "contract", "scope", "missing_target", "helper_policy", "approval_policy", "budget_policy", "history", "approval_revoked", "unlisted_root"])
async def test_exact_target_or_policy_drift_never_dispatches(target, monkeypatch, drift):
    r = target
    p, aid, _ = await authorize(r)
    args = dict(r.target)
    if drift == "input":
        args["current_input_hash"] = "c" * 64
    elif drift == "source":
        args["sources"] = deepcopy(args["sources"])
        args["sources"].receipt["semantic_source_sha256"] = "c" * 64
    elif drift == "critic":
        args["effective_critic"] = {**args["effective_critic"], "temperature": 0.123456}
    elif drift == "contract":
        args["evaluation_contract"] = {**args["evaluation_contract"], "new_policy": True}
    elif drift == "scope":
        args["scope"] = "test:other-project-budget"
    elif drift == "missing_target":
        args["draft_id"] = UUID(int=0)
    elif drift.endswith("policy"):
        name = {"helper_policy": "exact_unknown_business.py", "approval_policy": "operator_action_approval.py",
                "budget_policy": "budget.py"}[drift]
        original = exact.isolation_policy_manifest
        monkeypatch.setattr(exact, "isolation_policy_manifest", lambda: {**original(), name: "f" * 64})
    elif drift == "history":
        root = await r.session.get(ExecutionOperationModel, ROOT)
        root.owner = "changed owner history"
        await r.session.commit()
    elif drift == "approval_revoked":
        approval = await r.session.get(WorkflowRunModel, aid)
        approval.status = "cancelled"
        await r.session.commit()
    elif drift == "unlisted_root":
        r.session.add(ExecutionOperationModel(key="d" * 64, scope_id=SCOPE, input_hash="e" * 64,
            status="stopped", owner="unlisted", lease_until=datetime.now(UTC), result_json=None))
        await r.session.commit()
    before = await snapshot(r)
    with pytest.raises(ValueError):
        await exact.inspect_exact_unknown_business(r.session, ambiguous_roots={ROOT}, **args)
    if drift in {"helper_policy", "approval_policy", "budget_policy", "history", "approval_revoked", "unlisted_root"}:
        result = await _judge(r, bound=True)
        assert result.infrastructure_failure
    assert not r.dispatch
    await r.session.rollback()
    assert await snapshot(r) == before


async def test_policy_fence_hashes_actual_authority_and_dependencies():
    manifest = exact.isolation_policy_manifest()
    from pathlib import Path
    for name in ("exact_unknown_business.py", "operator_action_approval.py", "budget.py", "execution_authorization.py", "prompt_source_policy.py"):
        assert manifest[name] == hashlib.sha256((Path(exact.__file__).parent / name).read_bytes()).hexdigest()


async def test_existing_isolation_does_not_authorize_second_round_or_new_owner(target):
    r = target
    await authorize(r)
    with pytest.raises(DeniedDispatch):
        await _judge(r, bound=True)
    await r.session.rollback()
    sent = len(r.dispatch)
    value = await _judge(r, bound=True, retry={"attempt": 2})
    assert value.infrastructure_failure and len(r.dispatch) == sent


async def test_three_normal_slots_share_original_cohort_without_new_namespace(target):
    r = target
    p, _, _ = await authorize(r)
    from bestseller.services.budget import bind_execution_scope
    before = await snapshot(r)
    descriptors = []
    for slot in p["binding"]["allowed_slot_labels"]:
        for obj in (r.owner, r.draft):
            await r.session.refresh(obj)
        args = {k: v for k, v in r.target.items() if k != "scope"}
        with bind_execution_scope(SCOPE):
            result, descriptor = await runtime.prepare_commercial_component_slot(r.session, **args,
                workflow_run_id=r.owner.id, content_md=r.draft.content_md,
                retry_context=None, sample_slot=slot, settings=r.settings)
        assert result is None
        descriptors.append(descriptor)
    assert {d["namespace"] for d in descriptors} == {f"commercial-component:{p['binding']['stable_cohort_key']}:round:1"}
    assert {d["attempt"] for d in descriptors} == {1}
    assert {d["slot"] for d in descriptors} == set(p["binding"]["allowed_slot_labels"])
    assert await snapshot(r) == before and not r.dispatch


async def test_stopped_owner_control_is_preserved_without_becoming_purpose(target):
    r = target
    from test_unsent_reclaim_control import control
    root = await r.session.get(ExecutionOperationModel, ROOT)
    value = control(_row(root), "original isolated owner", root.owner)
    value["lease_until"] = datetime.now(UTC)
    r.session.add(ExecutionOperationModel(**value))
    await r.session.commit()
    before = await snapshot(r)
    p, _, _ = await authorize(r)
    assert len(p["baseline"]["controls"]) == 1
    assert p["baseline"]["purpose"] == "Unknown" and not p["baseline"]["credit_released"]
    with pytest.raises(DeniedDispatch):
        await _judge(r, bound=True)
    await r.session.rollback()
    assert await snapshot(r) == before


@pytest.mark.parametrize("name", ["exact_unknown_business.py", "operator_action_approval.py"])
async def test_actual_source_byte_drift_denies_real_stable_entry(target, monkeypatch, name):
    r = target
    await authorize(r)
    from pathlib import Path
    original = Path.read_bytes

    def changed(path):
        data = original(path)
        return data + b"\n# isolated policy byte drift\n" if path.name == name else data

    monkeypatch.setattr(Path, "read_bytes", changed)
    result = await _judge(r, bound=True)
    assert result.infrastructure_failure and not r.dispatch


async def test_pointer_cannot_drop_isolation_and_bypass_revalidation(target):
    r = target
    await authorize(r)
    with pytest.raises(DeniedDispatch):
        await _judge(r, bound=True)
    await r.session.rollback()
    await r.session.refresh(r.owner)
    metadata = deepcopy(r.owner.metadata_json)
    for journal in metadata["commercial_component_cohorts"].values():
        journal.pop("exact_unknown_business")
    r.owner.metadata_json = metadata
    await r.session.commit()
    sent = len(r.dispatch)
    result = await _judge(r, bound=True)
    assert result.infrastructure_failure and len(r.dispatch) == sent


async def test_real_gateway_synthetic_transport_reenters_without_more_actuals(target, monkeypatch, tmp_path, request):
    """Real complete_text/ledger/retention; only lowest transport is synthetic."""
    import json
    from bestseller.services import llm
    from bestseller.services.commercial_source_evidence import PROMPT_VERSION
    from bestseller.services.budget import execution_operation_id
    from test_commercial_judge_recovery import report
    r = target
    r.settings.llm.mock = False
    r.settings.artifact_store.mode = "local"
    r.settings.artifact_store.local_dir = tmp_path / "synthetic-physical"
    r.settings.artifact_store.retain_prompt_payloads = True
    # Synthetic historical counters are intentionally nonzero; no request
    # attribution or actual row is fabricated to explain these old charges.
    await r.session.refresh(r.budget)
    r.budget.calls_used, r.budget.tokens_charged = 17, 12345
    await r.session.commit()
    p, _, _ = await authorize(r)
    before = await snapshot(r)
    physical = []

    async def synthetic(request, role):
        assert request.operation_id and request.operation_id.startswith("semantic.v1:")
        physical.append((request.operation_id, request.sample_slot))
        raw = json.dumps(report(0.71), ensure_ascii=False)
        tools = [{"id": "isolated-submission", "type": "function", "function": {
            "name": "submit_chapter_review", "arguments": raw}}]
        return "", 11, 7, "tool_calls", tools, llm._build_raw_assistant_message("", tools)

    monkeypatch.setattr(judge, "complete_text", llm.complete_text)
    monkeypatch.setattr(llm, "_call_litellm", synthetic)
    first = await _judge(r, bound=True)
    assert not first.infrastructure_failure, first.raw_excerpt
    await r.session.commit()
    after = await snapshot(r)
    expected_operation = execution_operation_id(
        f"commercial-component:{p['binding']['stable_cohort_key']}:round:1",
        subject=f"critic:chapter_commercial_quality_judge:{PROMPT_VERSION}",
        revision=r.target["current_input_hash"])
    expected_keys = {hashlib.sha256(f"{SCOPE}\n{expected_operation}:sample:{label}".encode()).hexdigest()
        for label in p["binding"]["allowed_slot_labels"]}
    actuals = after[ExecutionAttemptModel.__tablename__]
    assert len(actuals) == 3 and {a["operation_key"] for a in actuals} == expected_keys
    assert all(a["input_hash"] == r.target["current_input_hash"] and a["status"] == "completed" for a in actuals)
    assert len(physical) == 3 and {op for op, _ in physical} == {expected_operation}
    assert [o for o in after[ExecutionOperationModel.__tablename__] if o["key"] == ROOT] == before[ExecutionOperationModel.__tablename__]
    assert after[ExecutionBudgetModel.__tablename__][0]["calls_used"] == before[ExecutionBudgetModel.__tablename__][0]["calls_used"] + 3
    second = await _judge(r, bound=True)
    assert not second.infrastructure_failure, second.raw_excerpt
    await r.session.commit()
    assert len(physical) == 3 and await snapshot(r) == after
    # Keep per-case audit properties without record_property's xunit2 warning
    # (the repository treats warnings as errors).
    request.node.user_properties.extend([
        ("evidence_boundary", "file SQLite / real complete_text+ledger+retention / synthetic lowest transport / zero vendor network / no literary acceptance"),
        ("expected_semantic_operation_id", expected_operation),
        ("actual_operation_keys", json.dumps(sorted(expected_keys))),
        ("actual_sample_slots", json.dumps(sorted(slot for _, slot in physical))),
        ("synthetic_actual_count_before_after_reentry", "3/3"),
        ("original_unknown_whole_row_unchanged", "true"),
        ("synthetic_calls_before_first_after_reentry", "17/20/20"),
        ("synthetic_historical_tokens_retained", "12345"),
    ])


@pytest.mark.parametrize("fault", ["revoke", "root_actual", "root_change", "draft_changed"])
async def test_reservation_rechecks_after_prepare_before_any_send(target, monkeypatch, tmp_path, fault):
    from bestseller.services import llm
    from bestseller.services.budget import ExecutionBudgetError
    from sqlalchemy.ext.asyncio import async_sessionmaker
    r = target
    r.settings.llm.mock = False
    r.settings.artifact_store.local_dir = tmp_path / "pre-reserve-physical"
    _, aid, _ = await authorize(r)
    original = llm._paid_transport_attempt
    physical = []
    mutated = False

    async def mutate_then_reserve(request, role):
        nonlocal mutated
        if not mutated:
            mutated = True
            async with async_sessionmaker(r.engine, expire_on_commit=False)() as session:
                if fault == "revoke":
                    row = await session.get(WorkflowRunModel, aid)
                    row.status = "cancelled"
                elif fault == "root_actual":
                    session.add(ExecutionAttemptModel(id="late-original-evidence", operation_key=ROOT,
                        scope_id=SCOPE, model_name="unknown-original-model", input_hash="b" * 64,
                        status="stopped", reserved_tokens=13, created_at=datetime.now(UTC)))
                elif fault == "draft_changed":
                    from bestseller.infra.db.models import ChapterDraftVersionModel
                    row = await session.get(ChapterDraftVersionModel, r.target["draft_id"])
                    row.content_md += "\nUnapproved concurrent body change."
                else:
                    row = await session.get(ExecutionOperationModel, ROOT)
                    row.owner = "concurrent root change"
                await session.commit()
        return await original(request, role)

    async def forbidden(*args):
        physical.append("forbidden")
        raise AssertionError("reservation must reject before transport")

    monkeypatch.setattr(judge, "complete_text", llm.complete_text)
    monkeypatch.setattr(llm, "_paid_transport_attempt", mutate_then_reserve)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)
    with pytest.raises(ExecutionBudgetError, match="execution_exact_unknown_admission_rejected"):
        await _judge(r, bound=True)
    after = await snapshot(r)
    assert not physical and after[ExecutionBudgetModel.__tablename__][0]["calls_used"] == 0
    actuals = after[ExecutionAttemptModel.__tablename__]
    assert len(actuals) == (1 if fault == "root_actual" else 0)
    assert all(a["operation_key"] == ROOT for a in actuals)


async def test_rate_limit_cannot_reserve_second_actual_in_exact_slot(target, monkeypatch, tmp_path):
    from bestseller.services import llm
    from bestseller.services.budget import ExecutionBudgetError
    r = target
    r.settings.llm.mock = False
    r.settings.llm.retry.rate_limit_max_attempts = 3
    r.settings.llm.retry.rate_limit_wait_min_seconds = 0
    r.settings.llm.retry.rate_limit_wait_max_seconds = 0
    r.settings.artifact_store.local_dir = tmp_path / "rate-limit-physical"
    await authorize(r)
    before = await snapshot(r)
    physical = []

    class Rejected429(Exception):
        status_code = 429

    async def rate_limit(request, role):
        physical.append(request.operation_id)
        llm._transport_send_count.get()[0] = 1  # Synthetic known rejection, not zero-send.
        raise Rejected429("synthetic local rejection")

    monkeypatch.setattr(judge, "complete_text", llm.complete_text)
    monkeypatch.setattr(llm, "_call_litellm", rate_limit)
    with pytest.raises(ExecutionBudgetError, match="execution_exact_unknown_admission_rejected"):
        await _judge(r, bound=True)
    after = await snapshot(r)
    assert len(physical) == 1
    assert len(after[ExecutionAttemptModel.__tablename__]) == 1
    assert after[ExecutionAttemptModel.__tablename__][0]["status"] == "rejected"
    assert after[ExecutionBudgetModel.__tablename__][0]["calls_used"] == 1
    assert [o for o in after[ExecutionOperationModel.__tablename__] if o["key"] == ROOT] == before[ExecutionOperationModel.__tablename__]


@pytest.mark.parametrize('fault',[None,'claimed_slot','changed_history','changed_contract','missing_approval'])
async def test_explicit_unspent_successor_preserves_parent_and_same_three_slots(target,monkeypatch,fault):
    from bestseller.services.prompt_source_policy import content_hash
    from bestseller.services.budget import execution_operation_id
    from bestseller.services.commercial_source_evidence import PROMPT_VERSION
    r=target
    old,_,receipt=await authorize(r)
    parent=await r.session.get(WorkflowRunModel,UUID(receipt['receipt_id']))
    original=deepcopy(parent.metadata_json)
    original_manifest=exact.protocol_manifest()
    monkeypatch.setattr(exact,'protocol_manifest',lambda:{**original_manifest,'isolated-control-migration':'e'*64})
    if fault=='changed_history':
        root=await r.session.get(ExecutionOperationModel,ROOT);root.owner='unexpected-change';await r.session.commit()
    if fault=='changed_contract':r.target['evaluation_contract']={**r.target['evaluation_contract'],'unexpected':True}
    if fault=='claimed_slot':
        b=old['binding'];op=execution_operation_id(f"commercial-component:{b['stable_cohort_key']}:round:1",subject=f'critic:chapter_commercial_quality_judge:{PROMPT_VERSION}',revision=b['current_effective_input_hash'])
        key=hashlib.sha256((SCOPE+'\n'+op+':sample:chapter-quality.independent-v2:0').encode()).hexdigest()
        r.session.add(ExecutionOperationModel(key=key,scope_id=SCOPE,input_hash=b['current_effective_input_hash'],status='stopped',owner='isolated-owner',lease_until=datetime.now(UTC)))
        await r.session.commit()
    fresh=await exact.prepare_exact_unknown_business(r.session,root_keys=[ROOT],**r.target)
    approval=None
    if fault!='missing_approval':
        approval=await record_operator_action_approval(r.session,purpose='exact_unknown_new_business',binding=exact.approval_binding(fresh),capabilities=['isolate_unknown'],actor='isolated-human',authorization_ref='isolated migration only',explicit_user_approval=True)
        await r.session.commit()
    kwargs=dict(parent_receipt_id=parent.id,expected_parent_metadata_sha256=content_hash(original),expected_plan_sha256=fresh['plan_sha256'],approval_id=approval,root_keys=[ROOT],**r.target)
    if fault:
        with pytest.raises(ValueError):await exact.persist_unspent_exact_unknown_successor(r.session,**kwargs)
        assert parent.metadata_json==original
    else:
        successor=await exact.persist_unspent_exact_unknown_successor(r.session,**kwargs);await r.session.commit()
        assert successor['new_slot_credit'] is False and parent.metadata_json==original
        assert old['binding']['stable_cohort_key']==fresh['binding']['stable_cohort_key']
        assert old['binding']['allowed_slot_labels']==fresh['binding']['allowed_slot_labels']
        await exact.inspect_exact_unknown_business(r.session,ambiguous_roots={ROOT},**r.target)
        assert r.session.info['exact_unknown_business_verified']['receipt_id']==successor['receipt_id']
