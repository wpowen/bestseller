"""Fixed source pins + actual native lifecycle/paid ledger, no provider sends."""
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.schema import CreateTable
from test_commercial_component_cohort import cohort  # noqa:F401
from test_retained_review_input_proof import retained  # noqa:F401

from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores
from bestseller.infra.db import models as db
from bestseller.services import chapter_review_reconciliation as service
from bestseller.services import chapter_review_policy_history_8y24 as archive
from bestseller.services import pipelines, llm
from bestseller.services import waiting_chapter_review_resume as waiting
from bestseller.services import chapter_review_preflight as preflight
from bestseller.services.chapter_review_prerequisite import WAITING, wait_binding, prerequisite_wait_receipt

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def native_scope(monkeypatch):
    # Existing paid fixtures use this original creation scope consistently in
    # requests/roots/attempts; use a native project_execution_scope spelling.
    import test_commercial_component_cohort as c
    import test_retained_review_input_proof as r
    scope='project:r09-test-'+uuid4().hex
    monkeypatch.setattr(c,'SCOPE',scope)
    monkeypatch.setattr(r,'SCOPE',scope)
    return scope


async def install_tables(r):
    async with r.engine.begin() as connection:
        for table in db.ChapterModel.metadata.tables.values():
            ddl=str(CreateTable(table,if_not_exists=True,include_foreign_key_constraints=[]).compile(dialect=connection.dialect))
            if 'VECTOR(' in ddl:
                continue  # No retrieval/embedding code is involved in this native proof.
            await connection.exec_driver_sql(ddl.replace('::jsonb',''))


async def base(r,scope):
    await install_tables(r)
    r.project.metadata_json={**r.project.metadata_json,'execution_scope_id':scope}
    prior=db.ChapterModel(project_id=r.project.id,chapter_number=1,chapter_goal='Retained predecessor',metadata_json={})
    r.session.add(prior);await r.session.flush()
    r.session.add(db.ChapterDraftVersionModel(project_id=r.project.id,chapter_id=prior.id,version_no=1,
        is_current=True,promotion_state='promoted',content_md='The predecessor closes the sealed ledger.',word_count=8))
    r.owner.status='completed'
    r.chapter.target_word_count=2600
    r.session.add(db.SceneCardModel(project_id=r.project.id,chapter_id=r.chapter.id,scene_number=1,
        scene_type='development',purpose={'story':'Read the complete retained body'},metadata_json={}))
    r.session.add(db.BookProductionControlModel(project_id=r.project.id,desired_state='run',command_serial=3))
    await r.session.commit()
    semantic=await pipelines._chapter_critic_semantic_dependencies(r.session,r.settings,r.chapter)
    await r.session.commit()
    return semantic


async def deny_calls(monkeypatch):
    async def denied(*a,**kw):raise AssertionError('R09 gateway/slot/producer dispatch forbidden')
    from bestseller.services import chapter_llm_quality_judge as judge, commercial_judge_recovery as commercial
    monkeypatch.setattr(llm,'complete_text',denied)
    monkeypatch.setattr(judge,'complete_text',denied)
    monkeypatch.setattr(commercial,'prepare_commercial_component_slot',denied)
    monkeypatch.setattr(llm,'_paid_transport_attempt',denied)
    monkeypatch.setattr(llm,'_call_litellm',denied)


async def snapshot(r):
    result={}
    for model in (db.ProjectModel,db.ChapterModel,db.ChapterDraftVersionModel,db.WorkflowRunModel,
        db.ReviewReportModel,db.QualityScoreModel,db.ExecutionBudgetModel,db.ExecutionOperationModel,db.ExecutionAttemptModel,db.RewriteTaskModel):
        result[model.__tablename__]={str(inspect(row).identity[0]):waiting._row(row)
            for row in await r.session.scalars(select(model).execution_options(populate_existing=True))}
    return result


@pytest.fixture
async def completed(retained,native_scope,monkeypatch):  # noqa:F811
    case=retained;r=case.rig
    semantic=await base(r,native_scope)
    report=case.report
    report.verdict='pass'
    report.structured_output={**report.structured_output,'reviewed_body_fingerprint':pipelines.chapter_review_body_fingerprint(r.draft.content_md),
        'critic_evidence':{'status':'verified','passed':True}}
    quality=db.QualityScoreModel(project_id=r.project.id,target_type='chapter',target_id=r.chapter.id,
        review_report_id=report.id,chapter_draft_version_id=r.draft.id,score_overall=.81,is_current=True)
    r.session.add(quality);await r.session.flush()
    result=ChapterReviewResult(verdict='pass',severity_max='low',scores=ChapterReviewScores(**dict.fromkeys(ChapterReviewScores.model_fields,.81)))
    body=pipelines.chapter_review_body_fingerprint(r.draft.content_md)
    cid=pipelines._chapter_critic_cycle_id(r.project.id,r.chapter.id,body['digest'])
    cycle={**case.cycle,'cycle_id':cid,'policy_source_hash':archive.OLD_POLICY,'semantic_dependencies_sha256':semantic,
        'attempts':[{'attempt':1,'state':'completed','outcome':'available','operation_namespace':f'chapter-critic:{cid}:1',
        'draft_id':str(r.draft.id),'body_fingerprint':body,'review_report_id':str(report.id),'quality_score_id':str(quality.id),
        'rewrite_task_id':None,'review_result':result.model_dump(mode='json')}]}
    r.chapter.metadata_json={**r.chapter.metadata_json,'chapter_critic_cycles':{cid:cycle}}
    await r.session.commit()
    await deny_calls(monkeypatch)
    r.cid,r.cycle,r.report,r.quality=cid,cycle,report,quality
    return r


@pytest.fixture
async def native_wait(cohort,native_scope,monkeypatch):  # noqa:F811
    r=cohort
    semantic=await base(r,native_scope)
    r.owner.status='machine_blocked';r.owner.current_step=WAITING
    body=pipelines.chapter_review_body_fingerprint(r.draft.content_md)
    cid=pipelines._chapter_critic_cycle_id(r.project.id,r.chapter.id,body['digest'])
    scores=ChapterReviewScores(**dict.fromkeys(ChapterReviewScores.model_fields,.1))
    result=ChapterReviewResult(verdict='unknown',severity_max='high',scores=scores)
    payload={'draft_id':str(r.draft.id),'reviewed_body_fingerprint':body,'primary_submission_original':None,
        'primary_submission_receipt':None,'rewrite_instructions':None,'critic_evidence':{'status':'unavailable','passed':False},
        'evidence_summary':{'primary_review_not_dispatched':{'reason':'required_judge_evidence_unavailable',
            'provider_calls':0,'body_fingerprint':body}}}
    report=db.ReviewReportModel(project_id=r.project.id,target_type='chapter',target_id=r.chapter.id,
        reviewer_type='synthetic-native-wait',verdict='unknown',severity_max='high',structured_output=payload)
    r.session.add(report);await r.session.flush()
    quality=db.QualityScoreModel(project_id=r.project.id,target_type='chapter',target_id=r.chapter.id,
        review_report_id=report.id,chapter_draft_version_id=r.draft.id,score_overall=.1)
    r.session.add(quality);await r.session.flush()
    cycle=dict(cycle_id=cid,state=WAITING,source_draft_id=str(r.draft.id),source_body_hash=body['digest'],
        policy_source_hash=archive.OLD_POLICY,semantic_dependencies_sha256=semantic,review_workflow_run_id=str(r.owner.id))
    entry=dict(attempt=1,state=WAITING,operation_namespace=f'chapter-critic:{cid}:1',draft_id=str(r.draft.id),body_fingerprint=body)
    receipt=prerequisite_wait_receipt(report,binding=wait_binding(cycle,entry,project_id=r.project.id,chapter_id=r.chapter.id,body_fingerprint=body))
    assert receipt
    record=dict(attempt=1,outcome='unknown',draft_id=str(r.draft.id),body_fingerprint=body,review_report_id=str(report.id),
        quality_score_id=str(quality.id),rewrite_task_id=None,review_result=result.model_dump(mode='json'),waiting_receipt=receipt)
    entry['infrastructure_history']=[record];cycle['attempts']=[entry]
    r.chapter.metadata_json={**r.chapter.metadata_json,'chapter_critic_cycles':{cid:cycle}}
    await r.session.commit()
    # Only upstream storage/context is substituted. Normal producer AST capture,
    # renderer/effective hash/history/orphan census/paid ledger are actual code.
    from types import SimpleNamespace
    from bestseller.services import context, commercial_review_context
    async def packet(*a,**kw):return SimpleNamespace(story_bible={})
    async def history(*a,**kw):return {'recent_adopted_prose':[]}
    monkeypatch.setattr(context,'build_chapter_writer_context',packet)
    monkeypatch.setenv('JUDGE_SAMPLES','3')
    await deny_calls(monkeypatch)
    r.cid,r.cycle,r.report,r.quality=cid,cycle,report,quality
    return r


async def test_fixed_original_263_and_exact_current_transition():
    proof=archive.verified_transition()
    assert proof['previous_policy_hash']==archive.OLD_POLICY
    assert proof['current_policy_hash']==pipelines.chapter_review_recovery_source_hash()
    original=json.loads((archive.HERE/'original.json').read_text())
    assert len(original['inventory'])==263
    assert archive._aggregate(original['inventory'])==archive.OLD_POLICY
    assert not proof['new_credit'] and not proof['dispatch_authorized']


async def test_completed_alias_native_paid_chain_once_and_real_consumer(completed):
    r=completed
    before=await snapshot(r)
    result=await service.prepare_native_review_resume(r.session,r.settings,project_id=r.project.id)
    assert result['status']=='prepared' and len(result['chapter_receipts'])==1
    proof=await preflight.validate_retained_review_before_processing(r.session,r.settings,r.chapter)
    assert proof['reuse_kind']=='available_review'
    assert len(result['chapter_receipts'][0]['policy_alias']['input_proof']['paid_rows'])==2
    consumed=await pipelines._review_chapter_with_evidence_retry(r.session,r.settings,r.project.slug,r.chapter.chapter_number,
        workflow=r.owner,draft=r.draft)
    assert consumed[1].id==r.report.id and consumed[2].id==r.quality.id
    after=await snapshot(r)
    for name, rows in before.items():
        if name!='chapters':assert after[name]==rows,name
    assert r.chapter.metadata_json['chapter_critic_cycles'][r.cid]['attempts']==r.cycle['attempts']
    again=await service.prepare_native_review_resume(r.session,r.settings,project_id=r.project.id)
    assert again['status']=='unchanged'
    assert (await snapshot(r))==after


async def test_waiting_native_history_runs_twice_per_locked_check_then_same_slot(native_wait,monkeypatch):
    r=native_wait
    counts=[];original=waiting.commercial._historical_commercial_cohort
    async def observed(*a,**kw):
        value=await original(*a,**kw);counts.append(value);return value
    monkeypatch.setattr(waiting.commercial,'_historical_commercial_cohort',observed)
    before=await snapshot(r)
    result=await service.prepare_native_review_resume(r.session,r.settings,project_id=r.project.id,chapter_id=r.chapter.id)
    leaf=result['chapter_receipts'][0]
    assert leaf['waiting_rearm']['status']=='rearmed'
    assert leaf['waiting_rearm']['prerequisite']['history_replays']==2
    assert len(counts)==6  # alias proof + two locked rearm inspections, each actual loop2
    current=r.chapter.metadata_json['chapter_critic_cycles'][r.cid]
    assert current['state']=='prepared' and len(current['attempts'])==1
    assert current['attempts'][0]['operation_namespace']==r.cycle['attempts'][0]['operation_namespace']
    assert current['attempts'][0]['infrastructure_history']==r.cycle['attempts'][0]['infrastructure_history']
    after=await snapshot(r)
    for name,rows in before.items():
        if name!='chapters':assert after[name]==rows,name
    assert (await preflight.validate_retained_review_before_processing(r.session,r.settings,r.chapter))['reuse_kind']=='resume_pending_review'


async def test_waiting_rollback_restores_policy_history_and_ledger(native_wait):
    r=native_wait;before=await snapshot(r)
    await service.prepare_native_review_resume(r.session,r.settings,project_id=r.project.id)
    await r.session.rollback()
    assert await snapshot(r)==before


@pytest.mark.parametrize('fault',['stop','active_owner','foreign_owner','changed_source','unknown',
    'unsupported_policy','draft_binding','physical_prompt','missing_transport'])
async def test_completed_native_foreign_or_semantic_change_never_aliases(completed,fault):
    r=completed
    meta=deepcopy(r.chapter.metadata_json);cycle=meta['chapter_critic_cycles'][r.cid]
    if fault=='stop':
        control=await r.session.get(db.BookProductionControlModel,r.project.id);control.desired_state='pause'
    elif fault=='active_owner':r.owner.status='running'
    elif fault=='foreign_owner':r.owner.project_id=uuid4()
    elif fault=='changed_source':r.chapter.chapter_goal='A different actual approved objective'
    elif fault=='unknown':r.report.verdict='unknown'
    elif fault=='unsupported_policy':cycle['policy_source_hash']='b'*64
    elif fault=='draft_binding':cycle['source_draft_id']=str(uuid4())
    elif fault=='physical_prompt':
        path=Path(r.settings.artifact_store.local_dir)/r.report.llm_run_id.hex
        run=await r.session.get(db.LlmRunModel,r.report.llm_run_id)
        path=Path(r.settings.artifact_store.local_dir)/run.request_payload_ref
        payload=json.loads(path.read_text());payload['messages']['system']['text']+=' CHANGED'
        payload['messages']['system']['original_chars']=len(payload['messages']['system']['text'])
        path.write_text(json.dumps(payload))
    else:
        row=await r.session.scalar(select(db.ExecutionOperationModel).where(db.ExecutionOperationModel.status=='transport_result'))
        await r.session.delete(row)
    r.chapter.metadata_json=meta
    await r.session.commit();before=await snapshot(r)
    with pytest.raises(ValueError):
        await service.prepare_native_review_resume(r.session,r.settings,project_id=r.project.id)
    assert await snapshot(r)==before


@pytest.mark.parametrize('fault',['stop','active_owner','foreign_owner','semantic','namespace','history','owner_state'])
async def test_waiting_wrong_control_or_source_does_not_rearm(native_wait,fault):
    r=native_wait
    meta=deepcopy(r.chapter.metadata_json);cycle=meta['chapter_critic_cycles'][r.cid]
    if fault=='stop':(await r.session.get(db.BookProductionControlModel,r.project.id)).desired_state='stop'
    elif fault=='active_owner':r.owner.status='running'
    elif fault=='foreign_owner':r.owner.scope_id=uuid4()
    elif fault=='semantic':r.chapter.title='Different current premise title'
    elif fault=='namespace':cycle['attempts'][0]['operation_namespace']='invented-credit'
    elif fault=='history':cycle['attempts'][0]['infrastructure_history'][0]['waiting_receipt']['credit_released']=True
    else:r.owner.status='failed'
    r.chapter.metadata_json=meta
    await r.session.commit();before=await snapshot(r)
    with pytest.raises(ValueError):await service.prepare_native_review_resume(r.session,r.settings,project_id=r.project.id)
    assert await snapshot(r)==before


async def test_unknown_current_source_pin_fails_without_adopting_source_flags(monkeypatch):
    actual=Path.read_bytes
    path=archive.ROOT/'src/bestseller/services/feedback.py'
    def changed(p):
        raw=actual(p)
        return raw+b'\n# unreviewed change\n' if p==path else raw
    monkeypatch.setattr(Path,'read_bytes',changed)
    with pytest.raises(ValueError,match='unlisted_runtime_delta'):archive.verified_transition()


async def test_archive_byte_tamper_and_unknown_original_are_blocked(monkeypatch):
    actual=Path.read_bytes
    target=archive.HERE/'bytes/src/bestseller/services/pipelines.py.source'
    monkeypatch.setattr(Path,'read_bytes',lambda p:actual(p)+b' ' if p==target else actual(p))
    with pytest.raises(ValueError,match='original_source_changed'):archive.verified_transition()
    with pytest.raises(ValueError,match='unknown_original_source'):archive.original_bytes('../arbitrary-source.py')


async def test_internally_replayed_history_must_be_stable_under_lock(native_wait,monkeypatch):
    r=native_wait;before=await snapshot(r)
    real=waiting._normal_commercial_prerequisite;seen=[]
    async def changes(*a,**kw):
        result=await real(*a,**kw);seen.append(result)
        if len(seen)==3:result={**result,'prepared_effective_input_hash':'a'*64}
        return result
    monkeypatch.setattr(waiting,'_normal_commercial_prerequisite',changes)
    with pytest.raises(ValueError,match='source_or_prerequisite_changed'):
        await service.prepare_native_review_resume(r.session,r.settings,project_id=r.project.id)
    assert len(seen)==3 and all(x['history_replays']==2 for x in seen)
    assert await snapshot(r)==before


async def test_alias_rejects_mutated_old_raw_and_flags(completed):
    r=completed
    await service.prepare_native_review_resume(r.session,r.settings,project_id=r.project.id)
    cycle=deepcopy(r.chapter.metadata_json['chapter_critic_cycles'][r.cid])
    cycle['native_runtime_policy_alias']['new_credit']=True
    plain=deepcopy(cycle['native_runtime_policy_alias']);plain.pop('receipt_sha256')
    cycle['native_runtime_policy_alias']['receipt_sha256']=service._native_digest(plain)
    with pytest.raises(ValueError,match='alias_binding'):
        service.validate_native_policy_alias(cycle,project_id=r.project.id,chapter_id=r.chapter.id,
            draft_id=r.draft.id,body_sha256=r.cycle['source_body_hash'])
    cycle=deepcopy(r.chapter.metadata_json['chapter_critic_cycles'][r.cid])
    cycle['attempts'][0]['review_result']['scores']['overall']=1.0
    with pytest.raises(ValueError,match='alias_valid_result_changed'):
        service.validate_native_policy_alias(cycle,project_id=r.project.id,chapter_id=r.chapter.id,
            draft_id=r.draft.id,body_sha256=r.cycle['source_body_hash'])


class EntryBoundary(BaseException):
    pass


@pytest.mark.parametrize('entry',['project','chapter'])
async def test_real_normal_entry_runs_native_admission_before_new_workflow(native_wait,monkeypatch,entry):
    r=native_wait;before=await snapshot(r)
    async def boundary(*a,**kw):
        current=r.chapter.metadata_json['chapter_critic_cycles'][r.cid]
        assert current['state']=='prepared' and len(current['attempts'])==1
        raise EntryBoundary('stop immediately after actual native source admission')
    if entry=='project':
        monkeypatch.setattr(pipelines,'_ensure_project_invariants',boundary)
        call=pipelines.run_project_pipeline.__wrapped__(r.session,r.settings,r.project.slug,
            materialize_narrative_graph=False,materialize_narrative_tree=False,export_markdown=False)
    else:
        from bestseller.services import outline_source_entry
        monkeypatch.setattr(outline_source_entry,'current_outline_artifact',boundary)
        call=pipelines.run_chapter_pipeline.__wrapped__(r.session,r.settings,r.project.slug,r.chapter.chapter_number,
            chapter_first=True,export_markdown=False,supersede_pending_rewrites=False)
    with pytest.raises(EntryBoundary):await call
    after=await snapshot(r)
    for table,rows in before.items():
        if table!='chapters':assert after[table]==rows,table


async def test_prepared_waiting_retry_consumes_original_namespace_attempt_and_owner(native_wait,monkeypatch):
    r=native_wait
    await service.prepare_native_review_resume(r.session,r.settings,project_id=r.project.id)
    await r.session.commit()
    seen=[]
    async def dispatch_boundary(*a,**kwargs):
        seen.append((kwargs['workflow_run_id'],llm._semantic_operation_namespace.get()))
        raise EntryBoundary('existing logical slot reached without sending')
    monkeypatch.setattr(pipelines,'review_chapter_draft',dispatch_boundary)
    with pytest.raises(EntryBoundary):
        await pipelines._review_chapter_with_evidence_retry(r.session,r.settings,r.project.slug,r.chapter.chapter_number,
            workflow=r.owner,draft=r.draft)
    assert seen==[(r.owner.id,(f'chapter-critic:{r.cid}:1',0))]
    current=r.chapter.metadata_json['chapter_critic_cycles'][r.cid]
    assert len(current['attempts'])==1
    assert current['attempts'][0]['infrastructure_history']==r.cycle['attempts'][0]['infrastructure_history']


async def test_project_without_cycles_is_select_only_idempotent(cohort):
    r=cohort
    from sqlalchemy import event
    statements=[]
    def sql(conn,cursor,statement,params,context,many):
        st=getattr(getattr(context,'compiled',None),'statement',None)
        assert st is not None and st.is_select and getattr(st,'_for_update_arg',None) is None
        statements.append(statement)
    def no_flush(*a):raise AssertionError('no-cycle admission must not flush or write')
    event.listen(r.engine.sync_engine,'before_cursor_execute',sql)
    event.listen(r.session.sync_session,'before_flush',no_flush)
    try:
        first=await service.prepare_native_review_resume(r.session,r.settings,project_id=r.project.id)
        second=await service.prepare_native_review_resume(r.session,r.settings,project_id=r.project.id)
    finally:
        event.remove(r.engine.sync_engine,'before_cursor_execute',sql)
        event.remove(r.session.sync_session,'before_flush',no_flush)
    assert first==second and first['status']=='unchanged' and first['chapter_receipts']==[]
    assert first['dispatch_authorized'] is first['credit_released'] is False
    assert statements
