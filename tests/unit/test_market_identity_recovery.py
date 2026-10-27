"""Explicit audited technical recovery; isolated SQLite, no model dispatch."""
from copy import deepcopy
from uuid import uuid4
import hashlib,json
import pytest
from sqlalchemy import MetaData,JSON,ForeignKeyConstraint,select,func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import CreateTable
from test_independent_acceptance import case
from test_market_validation_resume_identity import market_source
from bestseller.infra.db import models as m
from bestseller.services import conception_continuation as recovery,independent_acceptance as independent
pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

async def recovery_case(case):
    scope,req,h,rid,result=await market_source(case,legacy=True)
    meta=MetaData()
    for model in (m.ProjectModel,m.ChapterModel,m.WorkflowRunModel):
        table=model.__table__.to_metadata(meta)
        for column in table.columns:
            if isinstance(column.type,JSONB):column.type=JSON();column.server_default=None
        for c in list(table.constraints):
            if isinstance(c,ForeignKeyConstraint):table.constraints.remove(c)
    async with case.engine.begin() as c:
        for table in meta.tables.values():
            await c.exec_driver_sql(str(CreateTable(table).compile(dialect=c.dialect)).replace("::jsonb",""))
    tid=scope.split(':')[1]
    async with case.factory() as s:
        b=await s.get(m.ExecutionBudgetModel,scope);b.metadata_json={**b.metadata_json,'provider_authorization':{
            'mode':'completion_bound','status':'active','authorization_ref':'explicit isolated technical recovery',
            'models':[independent.MODEL],'endpoint_hosts':['api.minimaxi.com'],'objective':'same book complete'}}
        p=m.ProjectModel(slug='same-recovery-book',title='original title',genre='现实',language='zh-CN',status='paused',target_chapters=4,target_word_count=8000,
            metadata_json={'execution_scope_id':scope,'execution_terminal':{'code':'execution_operation_identity_required','terminal':True},
                'production_paused':True,'requires_human_review':True,'production_pause_reason':'execution_operation_identity_required',
                'protected_authored':'preserve','conception_snapshot_id':'old-validated-audit','unknown_history':['never release']})
        s.add(p);await s.commit();pid=p.id
    return tid,scope,pid,{'slug':'same-recovery-book','_active_continuation_scope':scope}

async def test_explicit_recovery_records_exact_paid_source_without_modifying_unknown_or_budget(case):
    tid,scope,pid,payload=await recovery_case(case)
    async with case.factory() as s:
        b=independent.snapshot(await s.get(m.ExecutionBudgetModel,scope));unknown=await s.get(m.ExecutionAttemptModel,'new27');held=unknown.reserved_tokens
        receipt=await recovery.recover_completed_market_identity_stop(s,task_id=tid,scope=scope,payload=payload);await s.commit()
        p=await s.get(m.ProjectModel,pid);assert p.status=='planning'
        assert 'execution_terminal' not in p.metadata_json and p.metadata_json['production_paused'] is False
        assert p.metadata_json['protected_authored']=='preserve' and p.metadata_json['conception_snapshot_id']=='old-validated-audit'
        assert not p.metadata_json.get('conception_approved') and not p.metadata_json.get('creation_canonical_snapshot_id')
        assert p.metadata_json['unknown_history']==['never release']
        assert receipt['credit_released'] is receipt['unknown_reconciled'] is receipt['grade_changed'] is False
        assert receipt['provider_calls']==0 and receipt['source']['source_llm_run_id']
        assert (await s.get(m.ExecutionOperationModel,receipt['receipt_key'])).status=='identity_recovery'
        assert len(p.metadata_json['execution_programmatic_recovery_history'])==1
        assert independent.snapshot(await s.get(m.ExecutionBudgetModel,scope))==b
        assert unknown.status=='unknown' and unknown.actual_tokens is None and unknown.reserved_tokens==held
        with pytest.raises(ValueError,match='project_state_changed'):
            await recovery.recover_completed_market_identity_stop(s,task_id=tid,scope=scope,payload=payload)

@pytest.mark.parametrize('fault',['other_task','other_scope','other_pause','other_terminal','approved','chapter','writer','pending_send','source_unknown','closed_auth','legacy_key'])
async def test_recovery_fails_closed_on_any_changed_boundary(case,fault):
    tid,scope,pid,payload=await recovery_case(case)
    async with case.factory() as s:
        p=await s.get(m.ProjectModel,pid);meta=deepcopy(p.metadata_json)
        if fault=='other_task':tid='wrong'
        if fault=='other_scope':payload['_active_continuation_scope']='wrong'
        if fault=='other_pause':meta['production_pause_reason']='human requested stop'
        if fault=='other_terminal':meta['execution_terminal']={'code':'execution_result_unknown','terminal':True}
        if fault=='approved':meta['conception_approved']=True
        if fault=='chapter':s.add(m.ChapterModel(project_id=pid,chapter_number=1,chapter_goal='preserve existing chapter'))
        if fault=='writer':s.add(m.WorkflowRunModel(project_id=pid,workflow_type='planning',status='running',metadata_json={}))
        a=await s.get(m.ExecutionAttemptModel,'market-attempt')
        if fault=='pending_send':a.status='reserved';a.actual_tokens=None
        if fault=='source_unknown':a.status='unknown';a.actual_tokens=None
        if fault=='closed_auth':
            b=await s.get(m.ExecutionBudgetModel,scope);x=deepcopy(b.metadata_json);x['provider_authorization']['status']='paused';b.metadata_json=x
        if fault=='legacy_key':
            op=await s.get(m.ExecutionOperationModel,a.operation_key);op.input_hash='different identity'
        p.metadata_json=meta;await s.commit();before=deepcopy(meta)
        with pytest.raises((ValueError,recovery.ExecutionBudgetError)):
            await recovery.recover_completed_market_identity_stop(s,task_id=tid,scope=scope,payload=payload)
        await s.rollback();p=await s.get(m.ProjectModel,pid)
        assert p.status=='paused' and p.metadata_json==before
        assert await s.scalar(select(func.count()).select_from(m.ExecutionOperationModel).where(m.ExecutionOperationModel.status=='identity_recovery'))==0
