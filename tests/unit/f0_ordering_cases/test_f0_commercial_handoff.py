"""Native negative fences for the forward F0 cohort handoff.

No paid/cycle/grade authority is fabricated as a positive. Full custody and
normal successor issuance require the separate isolated CP1 snapshot run.
"""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from bestseller.infra.db import models as m
from bestseller.services import recorded_review_sources, review_source_ordering as service
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.retry_ledger import project_execution_scope

from .f0_ordering_support import uid
from .test_context_native_ordering import source_db as source_db

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def untrusted_claim(case, *, control=True, financial="active"):
    # Claims alone must never turn these ordinary rows into a paid review.
    draft = m.ChapterDraftVersionModel(id=uid(9000), project_id=case.project.id,
        chapter_id=case.chapter.id, version_no=1, content_md="An ordinary candidate.",
        is_current=True, promotion_state="under_review")
    scope = project_execution_scope(case.project)
    budget = m.ExecutionBudgetModel(scope_id=scope, status=financial,
        max_calls=10, max_tokens=10000, calls_used=2, tokens_charged=31,
        deadline_at=datetime.now(UTC)+timedelta(hours=1), metadata_json={})
    case.session.add_all([draft, budget])
    if control:
        case.session.add(m.BookProductionControlModel(project_id=case.project.id,
            desired_state="run", command_serial=1, resume_pending=False, detail_json={}))
    receipt = {"schema":service.SCHEMA, "receipt_sha256":"a"*64, "source_cycle_id":"b"*64,
        "successor_cycle_id":"c"*64, "draft_id":str(draft.id)}
    case.chapter.metadata_json = {
        service.HISTORY_KEY:[receipt],
        "chapter_review_active_cycle":{"cycle_id":"c"*64,
            "f0_supersession_receipt_sha256":"a"*64},
        "chapter_critic_cycles":{"c"*64:{"cycle_id":"c"*64,
            "review_workflow_run_id":str(uid(9001)),"state":"running"}},
    }
    await case.session.commit()
    return draft, dict(project_id=str(case.project.id),chapter_id=str(case.chapter.id),
        draft_id=str(draft.id),scope_id=scope,
        source_binding={"body_sha256":recorded_review_sources._sha(draft.content_md.encode())})


async def invoke(case, binding):
    return await service.prepare_fresh_commercial_cohort(
        case.session, SimpleNamespace(), chapter=case.chapter,
        old_owner=SimpleNamespace(id=uid(9002)), old_journal={},key="d"*64,
        pointer={},binding=binding,workflow_run_id=uid(9001),
    )


async def test_ordinary_cohort_has_no_f0_transition(source_db):
    case=source_db
    before=deepcopy(case.chapter.metadata_json)
    assert await invoke(case,{}) is None
    assert case.chapter.metadata_json==before
    assert not case.session.dirty and not case.session.new


async def test_f0_transition_requires_actual_run_control(source_db):
    case=source_db
    _,binding=await untrusted_claim(case,control=False)
    before=deepcopy(case.chapter.metadata_json)
    with pytest.raises(service.ReviewSourceOrderingError) as caught:
        await invoke(case,binding)
    assert caught.value.differences[0]["field"]=="commercial_successor.control_run"
    assert case.chapter.metadata_json==before
    assert not case.session.dirty


async def test_f0_unknown_budget_remains_unknown_without_new_credit(source_db):
    case=source_db
    _,binding=await untrusted_claim(case,financial="unknown")
    before=deepcopy(case.chapter.metadata_json)
    with pytest.raises(ExecutionBudgetError):
        await invoke(case,binding)
    budget=await case.session.get(m.ExecutionBudgetModel,binding['scope_id'])
    assert (budget.status,budget.calls_used,budget.tokens_charged)==("unknown",2,31)
    assert case.chapter.metadata_json==before
    assert not case.session.dirty


async def test_f0_self_consistent_metadata_is_not_native_paid_custody(source_db):
    case=source_db
    _,binding=await untrusted_claim(case)
    before=deepcopy(case.chapter.metadata_json)
    with pytest.raises(recorded_review_sources.RecordedReviewSourceError,match='available_cycle_required'):
        await invoke(case,binding)
    assert case.chapter.metadata_json==before
    assert not list(await case.session.scalars(select(m.ReviewReportModel)))
    assert not list(await case.session.scalars(select(m.LlmRunModel)))
    assert not case.session.dirty


@pytest.mark.parametrize("forged_key", [False, True])
async def test_f0_new_cohort_cannot_import_an_old_or_absent_paid_root(source_db, forged_key):
    from bestseller.services.budget import execution_operation_id

    case=source_db
    slot="chapter-quality.independent-v2:0"
    scope=project_execution_scope(case.project)
    digest,key="a"*64,"d"*64
    namespace=f"commercial-component:{key}:f0:{digest}:round:1"
    identity=execution_operation_id(namespace,
        subject="critic:chapter_commercial_quality_judge:v12-source-authority",revision="e"*64)
    expected=recorded_review_sources._sha(f"{scope}\n{identity}:sample:{slot}".encode())
    run=m.LlmRunModel(id=uid(9100),project_id=case.project.id,logical_role="critic",
        provider="synthetic-negative",model_name="synthetic-negative",
        prompt_template="chapter_commercial_quality_judge",prompt_version="v12-source-authority",
        prompt_hash="e"*64,metadata_json={"execution_receipt":{
            "operation_key":expected if forged_key else "f"*64}})
    case.session.add(run);await case.session.commit()
    with pytest.raises(service.ReviewSourceOrderingError) as caught:
        await service.verify_fresh_commercial_paid_origin(case.session,
            journal={"f0_supersession_receipt_sha256":digest,"binding":{"scope_id":scope}},
            key=key,slot=slot,entry={"used_attempts":1},report={"llm_run_id":str(run.id)})
    assert caught.value.differences[0]["field"]==(
        "commercial_successor.paid_root" if forged_key else "commercial_successor.paid_namespace")
    assert not case.session.dirty


@pytest.mark.parametrize("mutate_return", [False, True])
async def test_first_slot_survives_native_checkpoint_and_fresh_session(source_db, monkeypatch, mutate_return):
    """Storage-boundary unit: real ORM + producer/freeze, no grade/adoption claim.

    Only the upstream expensive paid-custody read is stubbed here. All source,
    control, budget and workflow rows are real; the separate SDK rehearsal must
    exercise the actual paid reader too. The fixture grants no quality grade.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from bestseller.services import commercial_judge_recovery as producer
    from bestseller.services.budget import bind_execution_scope
    from bestseller.services.llm import LLMCompletionRequest

    case = source_db
    draft, _ = await untrusted_claim(case)
    scope = project_execution_scope(case.project)
    source_receipt = {name: "d" * 64 for name in producer._BINDING_KEYS}
    source_receipt["body_sha256"] = recorded_review_sources._sha(draft.content_md.encode())
    sources = SimpleNamespace(receipt=source_receipt)
    effective = {"model": "storage-fixture"}
    contract = {"version": "storage-fixture"}
    binding = dict(project_id=str(case.project.id), chapter_id=str(case.chapter.id),
        draft_id=str(draft.id), source_binding=source_receipt, evaluation_contract=contract,
        samples=3, scope_id=scope, effective_critic_sha256=producer._digest(effective))
    key = producer._digest(dict(project_id=str(case.project.id), chapter_id=str(case.chapter.id),
        body_sha256=source_receipt["body_sha256"]))
    old_journal = dict(schema=producer.COHORT_SCHEMA, binding=binding, historical=True, slots={})
    old = m.WorkflowRunModel(id=uid(9002), project_id=case.project.id, workflow_type="chapter_pipeline",
        scope_type="chapter", scope_id=case.chapter.id, status="completed", requested_by="storage-test",
        metadata_json={"commercial_component_cohorts": {key: deepcopy(old_journal)}})
    new = m.WorkflowRunModel(id=uid(9001), project_id=case.project.id, workflow_type="chapter_pipeline",
        scope_type="chapter", scope_id=case.chapter.id, status="running", requested_by="storage-test",
        metadata_json={"critic_cycle_id": "c" * 64})
    receipt = deepcopy(case.chapter.metadata_json[service.HISTORY_KEY][0])
    receipt["commercial_cohort"] = dict(workflow_run_id=str(old.id), key=key,
        binding_sha256=producer._digest(binding), journal_sha256=producer._digest(old_journal))
    metadata = deepcopy(case.chapter.metadata_json)
    metadata[service.HISTORY_KEY] = [receipt]
    metadata["commercial_component_cohorts"] = {key: dict(workflow_run_id=str(old.id),
        binding_sha256=producer._digest(binding))}
    case.chapter.metadata_json = metadata
    case.session.add_all([old, new]); await case.session.commit()
    custody_reads = []
    async def custody_boundary(*args, **kwargs):
        custody_reads.append(kwargs)
        return deepcopy(receipt)
    monkeypatch.setattr(service, "_receipt_for_original", custody_boundary)
    slot = "chapter-quality.independent-v2:0"
    with bind_execution_scope(scope):
        result, prepared = await producer.prepare_commercial_component_slot(case.session,
            project_id=case.project.id, chapter_id=case.chapter.id, draft_id=draft.id,
            workflow_run_id=new.id, sources=sources, content_md=draft.content_md,
            evaluation_contract=contract, sample_slot=slot, samples=3,
            retry_context=None, effective_critic=effective, settings=SimpleNamespace())
    assert result is None and len(custody_reads) == 1
    request = LLMCompletionRequest(logical_role="critic", system_prompt="Storage boundary",
        user_prompt="No model is called", fallback_response="No model is called",
        metadata={"nested":{"values":["before"]}}, tools=[{"type":"function",
            "function":{"name":"unused","parameters":{"nested":["before"]}}}],
        project_id=case.project.id, workflow_run_id=new.id)
    factory = async_sessionmaker(case.engine, expire_on_commit=False)
    async with factory() as fresh:
        # This exact production consumer raised KeyError in the failed CP1 run.
        frozen, returned_sources = await producer.freeze_commercial_component_request(
            fresh, prepared, request, source_receipt)
        assert frozen == request and returned_sources == source_receipt
        if mutate_return:
            local_owner=await fresh.get(m.WorkflowRunModel,new.id)
            before_metadata=deepcopy(local_owner.metadata_json)
            frozen.metadata["nested"]["values"].append("caller changed")
            frozen.tools[0]["function"]["parameters"]["nested"].append("caller changed")
            assert local_owner.metadata_json == before_metadata
    async with factory() as fresh:
        persisted = await fresh.get(m.WorkflowRunModel, new.id)
        entry = persisted.metadata_json["commercial_component_cohorts"][key]["slots"][slot]
        assert entry["state"] == "prepared" and entry["used_attempts"] == 1
        assert entry["request"] == request.model_dump(mode="json")
        previous = await fresh.get(m.WorkflowRunModel, old.id)
        assert previous.metadata_json["commercial_component_cohorts"][key] == old_journal
        budget = await fresh.get(m.ExecutionBudgetModel, scope)
        assert (budget.calls_used, budget.tokens_charged) == (2, 31)
