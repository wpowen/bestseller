"""Real SQLite handoff: same reviewed version, no provider and no manual canon flags."""
from copy import deepcopy
from datetime import datetime,timezone
from uuid import uuid4,UUID
import json
import pytest
from sqlalchemy import select
from sqlalchemy.schema import CreateTable
from test_creation_configuration_slice import store as store
from test_conception_approval_bridge import _snapshot
from bestseller.infra.db import models as m
from bestseller.services.conception import _project_final_canonical_body,_final_packaging_audit_identity,_render_seed_fidelity_story_assets,_CANONICAL_PACKAGING_BODY_BUDGETS
from bestseller.services.conception_snapshots import create_conception_snapshot_artifact,snapshot_hash
from bestseller.services.reviewed_conception_selection import select_reviewed_conception_snapshot,assert_snapshot_quality
pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

async def setup(store,tmp_path):
    async with store.engine.begin() as c:
        for model in (m.ExecutionBudgetModel,m.ExecutionOperationModel,m.ExecutionAttemptModel,m.LlmRunModel):
            await c.exec_driver_sql(str(CreateTable(model.__table__).compile(dialect=c.dialect)).replace('::jsonb',''))
    settings=store.settings.model_copy(deep=True);settings.artifact_store.local_dir=tmp_path/'artifacts';settings.artifact_store.local_dir.mkdir()
    content=deepcopy(_snapshot()['content']);content.update(premise='林秋是一名接线员。她追查明日来电。',title='信号之外',synopsis='女儿与父亲当夜见面并完整告别。',writing_profile={},world_model={})
    body=_project_final_canonical_body(premise=content['premise'],writing_profile={},story_spine=content['story_spine'],
        story_architecture=content['concept_contract'].get('story_architecture',{}),world_model={})
    rid=uuid4();report={'passed':True,'hard_conflicts':[],
        'audit_identity':_final_packaging_audit_identity(title=content['title'],synopsis=content['synopsis']),
        'normalization_version':'final-packaging-semantic.v2','canonical_body_sha256':snapshot_hash(body),'review_llm_run_ids':[str(rid)]}
    content['conception_artifacts']={'story_appeal':{'final_packaging_promotion':{'draft_eligible':True,'seed_fidelity':report}}}
    intent={'schema_version':'creation-intent.v1','concept_seed':'same original creator intent'};content['creation_intent_hash']=snapshot_hash(intent)
    template='conception_final_packaging_seed_fidelity_judge_candidate_v2_fixture'
    request={'messages':{'source':'system_user','system':{'text':'judge','truncated':False},
        'user':{'text':f"最终书名：{content['title']}\n最终上架简介：{content['synopsis']}\n\n{_render_seed_fidelity_story_assets(body,budgets=_CANONICAL_PACKAGING_BODY_BUDGETS)}",'truncated':False}}}
    (settings.artifact_store.local_dir/'request.json').write_text(json.dumps(request,ensure_ascii=False))
    now=datetime.now(timezone.utc)
    async with store.sessions() as s:
        project=await s.get(m.ProjectModel,store.pid);project.target_chapters=12
        old=await create_conception_snapshot_artifact(s,project_id=project.id,artifact_type='conception_snapshot',
            content={'title':'old wrong version','audit':'retained original'},status='validated',idempotency_key='old')
        source=await create_conception_snapshot_artifact(s,project_id=project.id,artifact_type='creation_intent',content=intent,status='validated',idempotency_key='intent')
        snap=await create_conception_snapshot_artifact(s,project_id=project.id,artifact_type='conception_snapshot',content=content,status='validated',idempotency_key='corrected-reviewed')
        project.metadata_json={**project.metadata_json,'conception_snapshot_id':str(snap.id),'creation_intent_snapshot_id':str(source.id),'creation_intent_contract_hash':snapshot_hash(intent)}
        s.add(m.ExecutionOperationModel(key='actual-paid-fixture',scope_id='fixture',input_hash='fixture-input',status='completed',owner='fixture',lease_until=now,
            result_json={'content':json.dumps({'passed':True,'hard_conflicts':[]}), 'model_name':'openai/MiniMax-M3.1-Flash','input_tokens':10,'output_tokens':20,'finish_reason':'stop'}))
        s.add(m.ExecutionAttemptModel(id='fixture-attempt',operation_key='actual-paid-fixture',scope_id='fixture',input_hash='fixture-input',model_name='openai/MiniMax-M3.1-Flash',status='completed',reserved_tokens=1000,actual_tokens=30,created_at=now))
        s.add(m.LlmRunModel(id=rid,project_id=project.id,logical_role='critic',provider='openai',model_name='openai/MiniMax-M3.1-Flash',prompt_template=template,input_tokens=10,output_tokens=20,finish_reason='stop',request_payload_ref='request.json',metadata_json={'execution_receipt':{'operation_key':'actual-paid-fixture'}}))
        await s.commit()
        return settings,snap.id,deepcopy(content),old.id

async def test_actual_reviewed_version_selected_and_original_snapshot_preserved(store,tmp_path):
    settings,sid,content,oldid=await setup(store,tmp_path)
    async with store.sessions() as s:
        receipt=await select_reviewed_conception_snapshot(s,settings,project_id=store.pid,snapshot_id=sid,expected_hash=snapshot_hash(content),actor='fixture operator',reason='same book reviewed correction');await s.commit()
    async with store.sessions() as s:
        p=await s.get(m.ProjectModel,store.pid);snap=await s.get(m.PlanningArtifactVersionModel,sid)
        assert p.metadata_json['conception_approved'] is True and p.title==content['title']
        assert snap.status=='canonical' and p.metadata_json['creation_canonical_snapshot_hash']==snapshot_hash(snap.content)
        from bestseller.services.planner import _bind_source_bound_planning_canonical,_assert_bound_planning_canonical
        await _bind_source_bound_planning_canonical(s,p)
        await _assert_bound_planning_canonical(s,p)
        assert p._source_bound_planning_canonical['id']==str(snap.id)
        assert p.metadata_json['protected_authored']=='保留'
        assert (await s.get(m.PlanningArtifactVersionModel,oldid)).content=={'title':'old wrong version','audit':'retained original'}
        assert receipt==await select_reviewed_conception_snapshot(s,settings,project_id=store.pid,snapshot_id=sid,expected_hash=snapshot_hash(snap.content),actor='fixture operator',reason='same book reviewed correction')

@pytest.mark.parametrize('fault',['legacy_grade','stale_snapshot','changed_body','raw_rejection','unsettled','wrong_request','intent_mismatch'])
async def test_invalid_evidence_never_creates_canonical_selection(store,tmp_path,fault):
    settings,sid,content,oldid=await setup(store,tmp_path)
    async with store.sessions() as s:
        snap=await s.get(m.PlanningArtifactVersionModel,sid)
        if fault=='legacy_grade':
            c=deepcopy(snap.content);c['conception_artifacts']['story_appeal']['final_packaging_promotion']['seed_fidelity'].pop('normalization_version');snap.content=c
        if fault=='changed_body':
            c=deepcopy(snap.content);c['premise']='different canonical body';snap.content=c
        if fault=='raw_rejection':
            op=await s.get(m.ExecutionOperationModel,'actual-paid-fixture');op.result_json={**op.result_json,'content':json.dumps({'passed':False,'hard_conflicts':[{'classification':'hard_conflict','scope':'synopsis','reason':'直接改写女儿身份，不是对设定的有意省略。'}]})}
        if fault=='unsettled':
            a=await s.get(m.ExecutionAttemptModel,'fixture-attempt');a.status='unknown';a.actual_tokens=None
        if fault=='intent_mismatch':
            p=await s.get(m.ProjectModel,store.pid);p.metadata_json={**p.metadata_json,'creation_intent_contract_hash':'different'}
        await s.commit();expected=snapshot_hash(snap.content)
    if fault=='wrong_request':(settings.artifact_store.local_dir/'request.json').write_text(json.dumps({'messages':{'source':'system_user','user':{'text':'older other synopsis'}}}))
    if fault=='stale_snapshot':expected='stale'
    async with store.sessions() as s:
        with pytest.raises(ValueError,match='reviewed_selection_'):
            await select_reviewed_conception_snapshot(s,settings,project_id=store.pid,snapshot_id=sid,expected_hash=expected,actor='fixture',reason='must not grant')
        await s.rollback()
    async with store.sessions() as s:
        p=await s.get(m.ProjectModel,store.pid)
        assert not p.metadata_json.get('conception_approved')
        assert not p.metadata_json.get('creation_canonical_snapshot_id')
        assert (await s.get(m.PlanningArtifactVersionModel,sid)).status=='validated'
