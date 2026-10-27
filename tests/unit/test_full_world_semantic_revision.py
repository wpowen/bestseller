from copy import deepcopy
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from uuid import uuid4
import json
import pytest
from sqlalchemy.schema import CreateTable
from test_creation_configuration_slice import store
from test_source_bound_planning_submission import completion as native
from test_source_bound_world_revision import receipt
from test_planner_services import build_project,lock_design_snapshot
from bestseller.infra.db import models as m
from bestseller.services import world_semantic_revision as revision,planner as p
from bestseller.services.source_bound_planning_submission import parse_planning_submission
from bestseller.services.source_bound_planning_content import CONTENT_POLICY,decode_source_bound_planning_document
from bestseller.services.budget import ExecutionBudgetError
pytestmark=pytest.mark.unit

async def paid(store):
 async with store.engine.begin() as c:
  for model in (m.ExecutionBudgetModel,m.ExecutionOperationModel,m.ExecutionAttemptModel,m.LlmRunModel):
   await c.exec_driver_sql(str(CreateTable(model.__table__).compile(dialect=c.dialect)).replace('::jsonb',''))
 c=native('world_spec',raw=True);candidate,_,_=parse_planning_submission(c,kind='world_spec');now=datetime.now(timezone.utc);rid=uuid4();wid=uuid4()
 raw={**c.__dict__,'provider':'openai','model_name':'openai/MiniMax-M3.1-Flash','finish_reason':'tool_calls','fallback_used':False,'input_tokens':30,'output_tokens':20}
 candidate['_meta']={'source_content_policy':CONTENT_POLICY,'source_compiler':'source-bound-model-completion.v3','generation_llm_run_id':str(rid)}
 async with store.sessions() as s:
  project=await s.get(m.ProjectModel,store.pid);project.metadata_json={**project.metadata_json,'execution_scope_id':'world-scope'}
  s.add(m.WorkflowRunModel(id=wid,project_id=store.pid,workflow_type='foundation_plan',status='failed',metadata_json={'execution_scope_id':'world-scope','planning_submissions':{str(rid):{'status':'verified'}}}))
  s.add(m.ExecutionBudgetModel(scope_id='world-scope',max_calls=3,max_tokens=3000,calls_used=1,tokens_charged=50,deadline_at=now+timedelta(hours=1),status='active',metadata_json={}))
  s.add(m.ExecutionOperationModel(key='actual-world',scope_id='world-scope',input_hash='world-hash',status='completed',owner='fixture',lease_until=now,result_json=raw))
  s.add(m.ExecutionAttemptModel(id='paid-world',operation_key='actual-world',scope_id='world-scope',input_hash='world-hash',model_name=raw['model_name'],status='completed',reserved_tokens=1000,actual_tokens=50,created_at=now))
  s.add(m.LlmRunModel(id=rid,workflow_run_id=wid,project_id=store.pid,logical_role='planner',provider='openai',model_name=raw['model_name'],prompt_template='planner_world_spec',prompt_hash='world-hash',input_tokens=30,output_tokens=20,finish_reason='tool_calls',metadata_json={'execution_receipt':{'operation_key':'actual-world'}}))
  await s.commit()
 return candidate,rid

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',[None,'wrong_project','wrong_role','wrong_template','wrong_scope','input_changed','unknown_operation','fallback','provider','altered_candidate','unknown_attempt','usage_missing','extra_unknown_attempt','raw_mismatch','forged_meta','run_usage_missing'])
async def test_only_actual_settled_exact_world_response_can_enter_full_repair(store,fault):
 candidate,rid=await paid(store)
 async with store.sessions() as s:
  run=await s.get(m.LlmRunModel,rid);op=await s.get(m.ExecutionOperationModel,'actual-world');a=await s.get(m.ExecutionAttemptModel,'paid-world')
  if fault=='wrong_project':run.project_id=uuid4()
  if fault=='wrong_role':run.logical_role='critic'
  if fault=='wrong_template':run.prompt_template='planner_book_spec'
  if fault=='wrong_scope':op.scope_id='another-scope'
  if fault=='input_changed':op.input_hash='another'
  if fault=='unknown_operation':op.status='unknown'
  if fault=='fallback':op.result_json={**op.result_json,'fallback_used':True}
  if fault=='provider':op.result_json={**op.result_json,'provider':'mock'}
  if fault=='altered_candidate':candidate['world_name']='forged new text'
  if fault=='unknown_attempt':a.status='unknown';a.actual_tokens=None
  if fault=='usage_missing':a.actual_tokens=None
  if fault=='run_usage_missing':run.input_tokens=None;op.result_json={**op.result_json,'input_tokens':None}
  if fault=='extra_unknown_attempt':s.add(m.ExecutionAttemptModel(id='never-replay',operation_key=op.key,scope_id='world-scope',input_hash=op.input_hash,model_name=run.model_name,status='unknown',reserved_tokens=1000,actual_tokens=None,created_at=datetime.now(timezone.utc)))
  if fault=='raw_mismatch':
   r=deepcopy(op.result_json);r['raw_message']['tool_calls'][0]['function']['arguments']+=' ';op.result_json=r
  if fault=='forged_meta':candidate['_meta']['generation_llm_run_id']=str(uuid4())
  await s.commit()
 async with store.sessions() as s:
  project=await s.get(m.ProjectModel,store.pid)
  assert await revision.completed_candidate(s,project,candidate) is (fault is None)
  assert (await s.get(m.ExecutionAttemptModel,'paid-world')).reserved_tokens==1000

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['success','rejected_again','unchanged','unknown','canonical_changed','stale_final_grade','unverified_initial','outside_world','unsettled_initial','legacy_baseline_hash'])
async def test_full_world_repair_is_one_new_version_with_new_subject_review(monkeypatch,mode):
 project=build_project();lock_design_snapshot(project,protagonist_name='Mira',reader_promise='approved action',core_story_engine='approved action')
 book={'unique_hook':'approved action'};baseline={'rules':[{'description':'canonical rule'}],'locations':[]}
 candidate={'world_name':'workshop','rules':[{'description':'contradictory proposal'}],'history_key_events':[{'event':'unsupported date'}],
     '_meta':{'source_content_policy':CONTENT_POLICY,'source_compiler':'source-bound-model-completion.v3','generation_llm_run_id':str(uuid4())}}
 before=deepcopy(candidate);source=deepcopy(project.metadata_json['book_design_snapshot']);reviews=[];generations=[];runs=[];initial={}
 async def review(*args,**kw):
  reviews.append(deepcopy(kw['world_derived']));r=receipt(project,book,baseline,kw['world_derived'],'pass' if len(reviews)==2 and mode!='rejected_again' else 'revise')
  r['changed_fields']=['world.rules','world.history_key_events']
  if len(reviews)==1:
   if mode=='unverified_initial':r['evidence_verified']=False
   if mode=='outside_world':r['changed_fields']=['cast.protagonist']
   if mode=='legacy_baseline_hash':r['canonical_baseline_hash']=p._approved_payload_hash({'book_spec':book,'world_spec':baseline,'cast_spec':{},'authority_snapshot':p._source_bound_design_snapshot(project)})
   initial.update(deepcopy(r))
  elif mode=='stale_final_grade':r['candidate_hash']=initial['candidate_hash'];r['candidate_hash_echo']=initial['candidate_hash']
  return r
 async def completed(*args):return mode!='unsettled_initial'
 async def generate(session,settings,**kw):
  generations.append(deepcopy(kw));assert kw['kind']=='world_spec' and kw['book_spec']==book and kw['projection']==baseline
  ctx=kw['revision_context'];assert ctx['schema']==revision.SCHEMA and ctx['rejected_world_spec']==p._without_planning_meta(candidate)
  assert ctx['llm_run_id']==initial['llm_run_id'] and ctx['raw_response_hash']==initial['raw_response_hash']
  if mode=='unknown':raise ExecutionBudgetError('execution_result_unknown','world-scope')
  revised=deepcopy(candidate)
  if mode!='unchanged':revised['rules']=[{'description':'canonical rule'}];revised['history_key_events']=[{'event':'date unspecified'}]
  if mode=='canonical_changed':project.metadata_json['book_design_snapshot']['reader_promise']='different source'
  return revised,uuid4()
 monkeypatch.setattr(p,'_review_source_bound_expansion',review);monkeypatch.setattr(revision,'completed_candidate',completed)
 monkeypatch.setattr(p,'_complete_source_bound_planning_content',generate)
 async def invoke():return await p._approve_source_bound_world_expansion(None,SimpleNamespace(),project=project,book_spec=book,world_baseline=baseline,world_derived=candidate,workflow_run_id=uuid4(),llm_run_ids=runs)
 if mode=='success':
  result=await invoke();trail=result['_meta']['source_bound_expansion_review_receipt']['world_revision']
  assert trail['schema']==revision.SCHEMA and trail['grade_inherited'] is False and trail['initial_review']['decision']=='revise' and trail['final_review']['decision']=='pass'
 elif mode=='unknown':
  with pytest.raises(ExecutionBudgetError,match='execution_result_unknown'):await invoke()
 else:
  with pytest.raises(p.PlannerFallbackError):await invoke()
 assert candidate==before
 if mode!='canonical_changed':assert project.metadata_json['book_design_snapshot']==source
 assert len(generations)==(0 if mode in ('unverified_initial','outside_world','unsettled_initial','legacy_baseline_hash') else 1)
 assert len(reviews)<=2
 if mode in ('rejected_again','stale_final_grade','unchanged','unknown','canonical_changed'):
  assert project.metadata_json['source_bound_expansion_review']['world_revision']['initial_review']['decision']=='revise'


def test_full_revision_prompt_is_lossless_and_preserves_uncertainty_and_current_native_contract():
 from test_planning_dispatch_transport import _plain
 from bestseller.services.planning_dispatch_transport import prepare_planning_dispatch,_document
 system,base,tools=_plain('world_spec')
 context={'schema':revision.SCHEMA,'rejected_world_spec':{'rules':[{'literal':'未确定日期'}]},'feedback':'correct the sequence, do not invent dates','candidate_hash':'same reviewed candidate'}
 new=revision.repair_document(base,context)
 wire,receipt=prepare_planning_dispatch(system,new,tools=tools,output_tokens=12288)
 doc,suffix=_document(wire);logical=decode_source_bound_planning_document(doc)
 assert logical.pop('verified_world_revision')==context and suffix=='' and receipt['roundtrip_verified']
 instruction=logical['current_output_contract'].pop('semantic_revision_instruction')
 assert 'Keep unspecified dates or counts explicitly uncertain' in instruction and 'new independent semantic review' in instruction
 assert logical==decode_source_bound_planning_document(json.loads(base))
 with pytest.raises(ValueError,match='context_invalid'):revision.repair_document(base,{'schema':'forged'})
