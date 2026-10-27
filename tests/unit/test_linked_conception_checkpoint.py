"""Same-book historical reads remain bound, immutable and never grant approval."""
from copy import deepcopy
from uuid import uuid4
import pytest
from sqlalchemy import select
from test_creation_configuration_slice import store as store
from test_conception_checkpoints import _state
from bestseller.infra.db.models import ProjectModel,WorkflowRunModel,ChapterModel
from bestseller.services.conception_checkpoints import (build_conception_checkpoint,build_replay_context,
    load_conception_checkpoint,load_replay_context,save_conception_checkpoint,save_replay_context)
pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

async def setup(store):
    checkpoint=build_conception_checkpoint(_state(),input_hash='same-input')
    replay=build_replay_context({'preserved':'rejected proposal'},input_hash='same-input',genre_key='realistic',chapter_count=4)
    async with store.sessions() as s:
        p=await s.get(ProjectModel,store.pid);p.metadata_json={**p.metadata_json,'creation_intent_contract_hash':'same-intent'}
        r=WorkflowRunModel(project_id=p.id,workflow_type='conception_initial',status='completed',metadata_json={
            'creation_intent_hash':'same-intent','conception_checkpoint':checkpoint,'conception_replay_context':replay})
        s.add(r);await s.commit();return r.id,checkpoint,replay

async def test_linked_frozen_checkpoint_and_replay_are_readable_but_never_mutable_or_approved(store):
    rid,cp,replay=await setup(store)
    async with store.sessions() as s:
        kw=dict(workflow_run_id=rid,input_hash='same-input',expected_project_id=store.pid,expected_intent_hash='same-intent')
        loaded=await load_conception_checkpoint(s,**kw);assert loaded==cp
        assert await load_replay_context(s,**kw)==replay
        loaded['state']['title']='caller mutation'
        with pytest.raises(ValueError,match='project-less'):
            await save_conception_checkpoint(s,workflow_run_id=rid,checkpoint=cp)
        with pytest.raises(ValueError,match='project-less'):
            await save_replay_context(s,workflow_run_id=rid,record=replay)
        assert (await s.get(WorkflowRunModel,rid)).metadata_json['conception_checkpoint']==cp
        assert not (await s.get(ProjectModel,store.pid)).metadata_json.get('conception_approved')

@pytest.mark.parametrize('fault',['missing_binding','wrong_project','wrong_intent','changed_intent','approved','chapter','active_writer','wrong_workflow','running_source','tampered'])
async def test_linked_checkpoint_recovery_rejects_unsafe_state(store,fault):
    rid,cp,replay=await setup(store)
    kw=dict(workflow_run_id=rid,input_hash='same-input',expected_project_id=store.pid,expected_intent_hash='same-intent')
    if fault=='missing_binding':kw.pop('expected_project_id')
    if fault=='wrong_project':kw['expected_project_id']=uuid4()
    if fault=='wrong_intent':kw['expected_intent_hash']='different'
    async with store.sessions() as s:
        p=await s.get(ProjectModel,store.pid);r=await s.get(WorkflowRunModel,rid)
        if fault=='changed_intent':p.metadata_json={**p.metadata_json,'creation_intent_contract_hash':'changed'}
        if fault=='approved':p.metadata_json={**p.metadata_json,'conception_approved':True}
        if fault=='chapter':s.add(ChapterModel(project_id=p.id,chapter_number=1,chapter_goal='existing authored goal'))
        if fault=='active_writer':s.add(WorkflowRunModel(project_id=p.id,workflow_type='planning',status='running',metadata_json={}))
        if fault=='wrong_workflow':r.workflow_type='chapter_pipeline'
        if fault=='running_source':r.status='running'
        if fault=='tampered':
            meta=deepcopy(r.metadata_json);meta['conception_checkpoint']['state']['title']='tampered';r.metadata_json=meta
        await s.commit()
        with pytest.raises(ValueError,match='conception_linked_checkpoint|sha256|project-less'):
            await load_conception_checkpoint(s,**kw)
        if fault!='tampered':
            with pytest.raises(ValueError,match='conception_linked_checkpoint|project-less'):
                await load_replay_context(s,**kw)
        assert not (await s.get(ProjectModel,store.pid)).metadata_json.get('creation_canonical_snapshot_id')
