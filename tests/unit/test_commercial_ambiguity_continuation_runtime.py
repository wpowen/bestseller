"""Actual SQLite prepare/commit boundaries for fixed legacy continuation."""
import hashlib
from uuid import NAMESPACE_URL, uuid5

import pytest
from sqlalchemy import select
from sqlalchemy.schema import CreateTable
from test_commercial_ambiguity_retirement import case as case
from test_commercial_ambiguity_runtime import seed, inspect, authorize_operator
from test_commercial_protocol_transition_runtime import transition_db as transition_db, protected_rows
from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ReviewReportModel, WorkflowRunModel
from bestseller.services import commercial_protocol_transition_runtime as runtime
from bestseller.services.commercial_ambiguity_continuation import SCHEMA, build_continuation_grant
from bestseller.services.commercial_judge_recovery import prepare_commercial_component_slot
from bestseller.services.commercial_source_evidence import PROMPT_VERSION, assemble_commercial_sources
from bestseller.services.budget import bind_execution_scope

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


async def setup(rig, monkeypatch):
    monkeypatch.setattr(runtime, "transition_snapshots", rig.real_snapshots)
    await seed(rig)
    authorize_operator(rig.session)
    await inspect(rig)
    baseline_id = await runtime.persist_operator_ambiguity_retirement(rig.session)
    await rig.session.commit()
    rig.session.info.clear()
    async with rig.engine.begin() as connection:
        for model in (ChapterDraftVersionModel, ReviewReportModel):
            ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
            await connection.exec_driver_sql(ddl.replace('::jsonb',''))
    return baseline_id


async def grant(rig, baseline_id):
    from uuid import UUID
    baseline = await rig.session.get(WorkflowRunModel, UUID(baseline_id))
    auth = runtime.completion_authorization(await rig.session.get(runtime.ExecutionBudgetModel, rig.scope))
    policy = {'prompt_version':PROMPT_VERSION,'source_manifest':runtime.protocol_manifest()}
    args = dict(project_id=str(rig.project_id),execution_scope_id=rig.scope,
        baseline_workflow_id=baseline_id,baseline_receipt=baseline.metadata_json['receipt'],
        target_protocol_policy=policy,original_authorization_ref=auth['authorization_ref'],
        actor='explicit-test-operator',reason='Preserve exactly the fixed baseline for subsequent normal targets')
    result = build_continuation_grant(**args)
    uid=uuid5(NAMESPACE_URL,'bestseller:'+SCHEMA+':'+result['identity'])
    rig.session.add(WorkflowRunModel(id=uid,project_id=rig.project_id,
        workflow_type='commercial_ambiguity_continuation',scope_type='project',scope_id=rig.project_id,
        status='completed',metadata_json={'grant':result,'baseline_workflow_id':baseline_id,
            'execution_scope_id':rig.scope,'target_protocol_policy':policy,'actor':args['actor'],'reason':args['reason']}))
    await rig.session.commit()
    return uid


async def target(rig, *, chapter=None, current=True, body='The observatory reopens after the storm.'):
    chapter = chapter if chapter is not None else await rig.session.get(ChapterModel, rig.chapter_id)
    draft=ChapterDraftVersionModel(project_id=rig.project_id,chapter_id=chapter.id,
        version_no=1,content_md=body,is_current=current,promotion_state='under_review')
    owner=WorkflowRunModel(project_id=rig.project_id,workflow_type='chapter_pipeline',scope_type='chapter',
        scope_id=chapter.id,status='running',metadata_json={})
    rig.session.add_all([draft,owner]);await rig.session.commit()
    return chapter,draft,owner


async def prepare(rig, target, *, contract=None):
    chapter,draft,owner=target
    sources=assemble_commercial_sources(chapter_number=chapter.chapter_number,
        content_md=draft.content_md,source={},previous_chapters=[])
    with bind_execution_scope(rig.scope):
        return await prepare_commercial_component_slot(rig.session,
            project_id=rig.project_id,chapter_id=chapter.id,draft_id=draft.id,workflow_run_id=owner.id,
            content_md=draft.content_md,sources=sources,evaluation_contract=contract or rig.contract,
            sample_slot='chapter-quality.independent-v2:0',samples=3,retry_context=None,
            effective_critic=rig.critic,current_input_hash=sha('actual-current:'+draft.content_md))


async def test_current_target_without_explicit_continuation_stays_blocked(transition_db, monkeypatch):
    rig=transition_db;await setup(rig, monkeypatch);current=await target(rig)
    before=await protected_rows(rig)
    with pytest.raises(ValueError,match='explicit_operator_migration_required'):
        await prepare(rig,current)
    await rig.session.rollback()
    assert before==await protected_rows(rig)


async def test_normal_prepare_commits_child_and_same_cohort_without_financial_credit(transition_db, monkeypatch):
    rig=transition_db;baseline=await setup(rig, monkeypatch);await grant(rig,baseline);current=await target(rig)
    before=await protected_rows(rig)
    result,call=await prepare(rig,current)
    assert result is None and call['attempt']==1
    async with rig.factory() as reader:
        chapter=await reader.get(ChapterModel,current[0].id)
        owner=await reader.get(WorkflowRunModel,current[2].id)
        pointer=chapter.metadata_json['commercial_component_cohorts'][call['key']]
        journal=owner.metadata_json['commercial_component_cohorts'][call['key']]
        from uuid import UUID
        child=await reader.get(WorkflowRunModel,UUID(journal['legacy_ambiguity_retirement_workflow_id']))
        assert pointer['workflow_run_id']==str(owner.id)
        assert child and child.status=='completed'
        assert child.metadata_json['target_draft_id']==str(current[1].id)
        assert child.metadata_json['baseline_workflow_id']==baseline
        assert journal['slots']=={'chapter-quality.independent-v2:0':{'state':'prepared','used_attempts':1}}
    after=await protected_rows(rig)
    for table in ('execution_budgets','execution_operations','execution_attempts','llm_runs'):
        assert before[table]==after[table]
    # A second workflow cannot create a second cohort or another round.
    other=WorkflowRunModel(project_id=rig.project_id,workflow_type='chapter_pipeline',scope_type='chapter',
        scope_id=current[0].id,status='running',metadata_json={})
    rig.session.add(other);await rig.session.commit()
    _,replay=await prepare(rig,(current[0],current[1],other))
    assert replay==call
    with pytest.raises(ValueError,match='contract_changed_requires_explicit_recovery'):
        await prepare(rig,current,contract={**rig.contract,'changed':True})
    await rig.session.rollback()


async def test_unselected_candidate_gets_no_derived_authority(transition_db, monkeypatch):
    rig=transition_db;baseline=await setup(rig, monkeypatch);await grant(rig,baseline)
    unselected=await target(rig,current=False)
    before=await protected_rows(rig)
    with pytest.raises(ValueError,match='continuation_current_draft_required'):
        await prepare(rig,unselected)
    await rig.session.rollback()
    assert before==await protected_rows(rig)


async def test_following_chapter_uses_same_fixed_baseline_but_own_existing_cohort(transition_db, monkeypatch):
    rig=transition_db;baseline=await setup(rig, monkeypatch);await grant(rig,baseline)
    first=await target(rig);_,one=await prepare(rig,first)
    chapter=ChapterModel(project_id=rig.project_id,chapter_number=3,chapter_goal='Another event',metadata_json={})
    rig.session.add(chapter);await rig.session.commit()
    later=await target(rig,chapter=chapter,body='A different visitor brings a new instrument.')
    _,two=await prepare(rig,later,contract={**rig.contract,'chapter-specific-tools':'different exact catalog'})
    assert two['key']!=one['key'] and two['attempt']==one['attempt']==1
    async with rig.factory() as reader:
        children=list(await reader.scalars(select(WorkflowRunModel).where(
            WorkflowRunModel.workflow_type=='commercial_ambiguity_retirement')))
        derived=[c for c in children if c.current_step=='fixed_baseline_continuation']
        assert len(derived)==2
        assert {c.metadata_json['baseline_workflow_id'] for c in derived}=={baseline}
        assert len({c.metadata_json['receipt']['binding']['stable_cohort_key'] for c in derived})==2


async def test_unlisted_valid_paid_ambiguity_cannot_be_absorbed_by_continuation(transition_db, monkeypatch):
    from copy import deepcopy
    from datetime import UTC, datetime
    from bestseller.infra.db.models import ExecutionOperationModel, ExecutionAttemptModel
    rig=transition_db;baseline=await setup(rig,monkeypatch);await grant(rig,baseline)
    current=await target(rig)
    root=next(o for o in await rig.session.scalars(select(ExecutionOperationModel)) if o.status=='completed')
    original_attempt=next(a for a in await rig.session.scalars(select(ExecutionAttemptModel)) if a.operation_key==root.key)
    operation=deepcopy(runtime._row(root));key=sha('new unlisted actual request');aid='new-unlisted-attempt'
    operation['key']=key
    operation['result_json']['metadata']['execution_receipt']['operation_key']=key
    operation['result_json']['metadata']['execution_receipt']['attempts'][0]['attempt_id']=aid
    operation['lease_until']=datetime.now(UTC)
    attempt=runtime._row(original_attempt)
    attempt.update(id=aid,operation_key=key,created_at=datetime.now(UTC))
    transport=dict(operation)
    transport.update(key=sha(f'transport-result.v1\n{rig.scope}\n{aid}'),status='transport_result',result_json={
        'schema':'transport-result.v1','attempt_id':aid,'operation_key':key,
        'input_hash':operation['input_hash'],'result':deepcopy(operation['result_json'])})
    rig.session.add_all([ExecutionOperationModel(**operation),ExecutionOperationModel(**transport),
                         ExecutionAttemptModel(**attempt)])
    await rig.session.commit()
    before=await protected_rows(rig)
    with pytest.raises(ValueError,match='receipt_or_snapshot_drift'):
        await prepare(rig,current)
    await rig.session.rollback()
    assert before==await protected_rows(rig)
