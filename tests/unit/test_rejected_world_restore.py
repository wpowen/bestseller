"""Real SQLite/native-parser restoration; fake suppliers are offline-only fixtures."""
from copy import deepcopy
from datetime import datetime,timezone
from uuid import uuid4,UUID
from types import SimpleNamespace
import hashlib,json
import pytest
from sqlalchemy import select,func
from test_creation_configuration_slice import store
from test_full_world_semantic_revision import paid
from test_source_bound_world_revision import receipt
from test_planner_services import lock_design_snapshot
from test_source_bound_planning_submission import completion as native
from bestseller.infra.db import models as m
from bestseller.services import planner as p,rejected_world_restore as restore
from bestseller.services.conception_snapshots import snapshot_hash
pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

async def fixture(store):
 candidate,sourceid=await paid(store);book={'unique_hook':'approved action'}
 baseline=deepcopy(p._without_planning_meta(candidate));baseline['rules']=[{'rule_id':'R1','description':'approved fact','constraint':'retain source'}]
 now=datetime.now(timezone.utc);active=uuid4();sid=uuid4()
 async with store.sessions() as s:
  project=await s.get(m.ProjectModel,store.pid)
  lock_design_snapshot(project,protagonist_name='Mira',reader_promise='approved action',core_story_engine='approved action')
  content={'premise':'approved action','world_spec':deepcopy(baseline)}
  s.add(m.PlanningArtifactVersionModel(id=sid,project_id=store.pid,artifact_type='conception_snapshot',version_no=1,status='canonical',schema_version='1.0',content=content,idempotency_key='canonical-world-test'))
  project.metadata_json={**project.metadata_json,'execution_scope_id':'world-scope','conception_approved':True,
      'creation_canonical_snapshot_id':str(sid),'creation_canonical_snapshot_hash':snapshot_hash(content),'llm_model_id':'selected'}
  project._source_bound_planning_canonical={'id':str(sid),'content_hash':snapshot_hash(content),'content':deepcopy(content)}
  report=receipt(project,book,baseline,candidate);report['changed_fields']=['world.rules','world.history_key_events']
  raw={k:deepcopy(report[k]) for k in ('decision','core_preserved','core_conflicts','quality_findings','authority_snapshot_hash','candidate_hash','source_evidence','candidate_evidence','feedback') if k in report}
  text=json.dumps(raw,ensure_ascii=False);digest=hashlib.sha256(text.encode()).hexdigest();qid=UUID(report['llm_run_id'])
  report['raw_response_hash']=digest;report['review_attempts'][0]['raw_response_hash']=digest
  project.metadata_json={**project.metadata_json,'source_bound_expansion_review':report}
  source=await s.get(m.LlmRunModel,sourceid)
  s.add(m.LlmRunModel(id=qid,project_id=store.pid,workflow_run_id=source.workflow_run_id,logical_role='critic',provider='openai',model_name='openai/MiniMax-M3.1-Flash',prompt_template='planner_source_bound_expansion_semantic_review',prompt_hash='review-hash',input_tokens=20,output_tokens=10,finish_reason='stop',metadata_json={'execution_receipt':{'operation_key':'real-rejection'}}))
  s.add(m.ExecutionOperationModel(key='real-rejection',scope_id='world-scope',input_hash='review-hash',status='completed',owner='fixture',lease_until=now,result_json={'provider':'openai','model_name':'openai/MiniMax-M3.1-Flash','content':text,'input_tokens':20,'output_tokens':10,'finish_reason':'stop','fallback_used':False}))
  s.add(m.ExecutionAttemptModel(id='review-paid',operation_key='real-rejection',scope_id='world-scope',input_hash='review-hash',model_name='openai/MiniMax-M3.1-Flash',status='completed',reserved_tokens=1000,actual_tokens=30,created_at=now))
  s.add(m.WorkflowRunModel(id=active,project_id=store.pid,workflow_type='foundation_plan',status='running',metadata_json={'execution_scope_id':'world-scope'}))
  await s.commit()
 return SimpleNamespace(candidate=candidate,book=book,baseline=baseline,report=report,sourceid=sourceid,qid=qid,active=active,sid=sid,content=content)

async def bound(s,store,x):
 project=await s.get(m.ProjectModel,store.pid)
 project._source_bound_planning_canonical={'id':str(x.sid),'content_hash':snapshot_hash(x.content),'content':deepcopy(x.content)}
 return project

async def test_restore_is_audited_idempotent_rejection_and_never_promotes_or_changes_ledger(store):
 x=await fixture(store)
 async with store.sessions() as s:
  project=await bound(s,store,x);budget=await s.get(m.ExecutionBudgetModel,'world-scope');before=(budget.calls_used,budget.tokens_charged,budget.metadata_json)
  a=await restore.restore(s,project=project,book=x.book,baseline=x.baseline,workflow_run_id=x.active)
  b=await restore.restore(s,project=project,book=x.book,baseline=x.baseline,workflow_run_id=x.active)
  assert a==b and p._without_planning_meta(a)==p._without_planning_meta(x.candidate)
  assert a['_meta']['approved_complete'] is False and a['_meta']['planning_status']=='rejected_pending_revision'
  assert (await restore.verified_rejection(s,project,book=x.book,baseline=x.baseline,candidate=a,expected_review_id=str(x.qid)))['decision']=='revise'
  assert (budget.calls_used,budget.tokens_charged,budget.metadata_json)==before
  wf=await s.get(m.WorkflowRunModel,x.active);assert len(wf.metadata_json['world_repair_restorations'])==1
  record=next(iter(wf.metadata_json['world_repair_restorations'].values()))
  assert record['inherited_decision']=='revise' and record['approval_inherited'] is False and record['provider_calls']==0
  assert await s.scalar(select(func.count()).select_from(m.ExecutionAttemptModel))==2
  assert (await s.get(m.PlanningArtifactVersionModel,x.sid)).status=='canonical'

@pytest.mark.parametrize('fault',['producer_unknown','producer_unsettled','submission_unverified','review_unknown','review_unsettled','review_raw_changed','review_wrong_project','review_echo_changed','canonical_changed','book_changed','baseline_changed','ambiguous','forged_marker'])
async def test_unknown_unsettled_mismatched_or_ambiguous_history_stops_without_paid_replacement(store,fault):
 x=await fixture(store)
 async with store.sessions() as s:
  project=await bound(s,store,x);source=await s.get(m.LlmRunModel,x.sourceid)
  if fault=='producer_unknown':(await s.get(m.ExecutionOperationModel,'actual-world')).status='unknown'
  if fault=='producer_unsettled':(await s.get(m.ExecutionAttemptModel,'paid-world')).actual_tokens=None
  if fault=='submission_unverified':
   owner=await s.get(m.WorkflowRunModel,source.workflow_run_id);owner.metadata_json={**owner.metadata_json,'planning_submissions':{}}
  if fault=='review_unknown':(await s.get(m.ExecutionOperationModel,'real-rejection')).status='unknown'
  if fault=='review_unsettled':(await s.get(m.ExecutionAttemptModel,'review-paid')).actual_tokens=None
  if fault=='review_raw_changed':
   op=await s.get(m.ExecutionOperationModel,'real-rejection');op.result_json={**op.result_json,'content':'{"decision":"pass"}'}
  if fault=='review_wrong_project':(await s.get(m.LlmRunModel,x.qid)).project_id=uuid4()
  if fault=='review_echo_changed':
   project.metadata_json={**project.metadata_json,'source_bound_expansion_review':{**x.report,'candidate_hash_echo':'forged'}}
  if fault=='canonical_changed':(await s.get(m.PlanningArtifactVersionModel,x.sid)).content={'different':'source'}
  if fault=='book_changed':x.book={'unique_hook':'different'}
  if fault=='baseline_changed':x.baseline={**x.baseline,'world_name':'different'}
  if fault=='ambiguous':
   # A second settled source with the identical body remains ambiguous.
   newid=uuid4();oldop=await s.get(m.ExecutionOperationModel,'actual-world');now=datetime.now(timezone.utc)
   s.add(m.ExecutionOperationModel(key='duplicate',scope_id='world-scope',input_hash='duplicate-hash',status='completed',owner='fixture',lease_until=now,result_json=deepcopy(oldop.result_json)))
   s.add(m.ExecutionAttemptModel(id='duplicate-attempt',operation_key='duplicate',scope_id='world-scope',input_hash='duplicate-hash',model_name=source.model_name,status='completed',reserved_tokens=1000,actual_tokens=50,created_at=now))
   s.add(m.LlmRunModel(id=newid,project_id=store.pid,workflow_run_id=source.workflow_run_id,logical_role='planner',provider='openai',model_name=source.model_name,prompt_template='planner_world_spec',prompt_hash='duplicate-hash',input_tokens=30,output_tokens=20,finish_reason='tool_calls',metadata_json={'execution_receipt':{'operation_key':'duplicate'}}))
   owner=await s.get(m.WorkflowRunModel,source.workflow_run_id);owner.metadata_json={**owner.metadata_json,'planning_submissions':{**owner.metadata_json['planning_submissions'],str(newid):{'status':'verified'}}}
  await s.commit()
 async with store.sessions() as s:
  project=await bound(s,store,x)
  if fault=='forged_marker':
   candidate=deepcopy(x.candidate);candidate['_meta']['restored_rejection_review_id']=str(uuid4())
   with pytest.raises(p.PlannerFallbackError,match='binding invalid'):
    await p._approve_source_bound_world_expansion(s,store.settings,project=project,book_spec=x.book,world_baseline=x.baseline,world_derived=candidate,workflow_run_id=x.active)
  else:
   with pytest.raises(p.PlannerFallbackError,match='evidence missing, mismatched or ambiguous'):
    await restore.restore(s,project=project,book=x.book,baseline=x.baseline,workflow_run_id=x.active)
  assert (await s.get(m.ExecutionBudgetModel,'world-scope')).calls_used==1
  assert (await s.get(m.ExecutionAttemptModel,'paid-world')).reserved_tokens==1000

async def test_full_normal_call_path_restores_rejection_then_submits_new_native_revision_and_new_review(store,monkeypatch):
 from bestseller.services import model_catalog,llm
 from bestseller.services.planning_dispatch_transport import _document,prepare_planning_dispatch
 from bestseller.services.source_bound_planning_content import decode_source_bound_planning_document
 from bestseller.services.source_bound_planning_submission import parse_planning_submission
 from bestseller.services.finite_book_budget import current_capacity
 x=await fixture(store);sent=[];reviews=[]
 monkeypatch.setattr(model_catalog,'resolve_project_model_entry',lambda m:SimpleNamespace(model='openai/MiniMax-M3.1-Flash',api_base='https://api.minimaxi.com/v1'))
 async def producer(session,settings,**kw):
  assert current_capacity()==1000000
  logical=decode_source_bound_planning_document(json.loads(kw['user_prompt']))
  assert logical['verified_world_revision']['llm_run_id']==str(x.qid)
  assert logical['verified_world_revision']['candidate_hash']==x.report['candidate_hash']
  assert 'Keep unspecified dates or counts explicitly uncertain' in logical['current_output_contract']['semantic_revision_instruction']
  user=kw['user_prompt'].rstrip()+p._STRUCTURED_FUNCTION_SUFFIX
  wire,capacity=prepare_planning_dispatch(kw['system_prompt'],user,tools=[kw['output_tool']],output_tokens=12288)
  assert capacity['capacity']['budget_passed'] and capacity['capacity']['total_budget']==1000000
  sent.append((wire,kw['prompt_version']))
  revised=deepcopy(p._without_planning_meta(x.candidate));revised['world_name']='new traceable derived proposal'
  c=native('world_spec',inner=json.dumps(revised,ensure_ascii=False),raw=True)
  parsed,_=kw['output_parser'](c)
  rid=uuid4();key='new-native-world';now=datetime.now(timezone.utc);raw={**c.__dict__,'provider':'openai','model_name':'openai/MiniMax-M3.1-Flash','finish_reason':'tool_calls','fallback_used':False,'input_tokens':35,'output_tokens':25}
  session.add(m.ExecutionOperationModel(key=key,scope_id='world-scope',input_hash='new-real-effective-hash',status='completed',owner='offline-only-fixture',lease_until=now,result_json=raw))
  session.add(m.ExecutionAttemptModel(id='new-world-attempt',operation_key=key,scope_id='world-scope',input_hash='new-real-effective-hash',model_name=raw['model_name'],status='completed',reserved_tokens=1000,actual_tokens=60,created_at=now))
  session.add(m.LlmRunModel(id=rid,project_id=store.pid,workflow_run_id=x.active,logical_role='planner',provider='openai',model_name=raw['model_name'],prompt_template='planner_world_spec',prompt_hash='new-real-effective-hash',input_tokens=35,output_tokens=25,finish_reason='tool_calls',metadata_json={'execution_receipt':{'operation_key':key}}))
  await session.flush()
  return parsed,rid
 async def judge(session,settings,**kw):
  reviews.append(kw);assert p._without_planning_meta(kw['world_derived'])!=p._without_planning_meta(x.candidate)
  assert kw['book_spec']==x.book and kw['world_baseline']==x.baseline
  return receipt(kw['project'],x.book,x.baseline,kw['world_derived'],'pass')
 monkeypatch.setattr(p,'_generate_structured_artifact',producer)
 monkeypatch.setattr(p,'_review_source_bound_expansion',judge)
 async with store.sessions() as s:
  project=await bound(s,store,x)
  restored,run=await p._complete_source_bound_planning_content(s,store.settings,project=project,premise='approved action',kind='world_spec',projection=x.baseline,workflow_run_id=x.active,book_spec=x.book)
  assert run is None and sent==[] and reviews==[]
  result=await p._approve_source_bound_world_expansion(s,store.settings,project=project,book_spec=x.book,world_baseline=x.baseline,world_derived=restored,workflow_run_id=x.active,llm_run_ids=[])
  trail=result['_meta']['source_bound_expansion_review_receipt']['world_revision']
  assert trail['initial_review']['decision']=='revise' and trail['initial_review']['llm_run_id']==str(x.qid)
  assert trail['final_review']['decision']=='pass' and trail['final_review']['llm_run_id']!=str(x.qid)
  assert len(sent)==1 and len(reviews)==1
  assert (await s.get(m.PlanningArtifactVersionModel,x.sid)).status=='canonical'
  assert (await s.get(m.ExecutionOperationModel,'real-rejection')).result_json['content']!= '{"decision":"pass"}'

async def reviewed_artifact(store,x):
 async with store.sessions() as s:
  project=await bound(s,store,x)
  candidate=deepcopy(p._without_planning_meta(x.candidate));candidate['world_name']='independently corrected proposal'
  c=native('world_spec',inner=json.dumps(candidate,ensure_ascii=False),raw=True)
  now=datetime.now(timezone.utc);rid=uuid4()
  raw={**c.__dict__,'provider':'openai','model_name':'openai/MiniMax-M3.1-Flash','finish_reason':'tool_calls','fallback_used':False,'input_tokens':35,'output_tokens':25}
  s.add(m.ExecutionOperationModel(key='approved-generation',scope_id='world-scope',input_hash='approved-hash',status='completed',owner='fixture',lease_until=now,result_json=raw))
  s.add(m.ExecutionAttemptModel(id='approved-generation-paid',operation_key='approved-generation',scope_id='world-scope',input_hash='approved-hash',model_name=raw['model_name'],status='completed',reserved_tokens=1000,actual_tokens=60,created_at=now))
  s.add(m.LlmRunModel(id=rid,project_id=store.pid,workflow_run_id=x.active,logical_role='planner',provider='openai',model_name=raw['model_name'],prompt_template='planner_world_spec',prompt_hash='approved-hash',input_tokens=35,output_tokens=25,finish_reason='tool_calls',metadata_json={'execution_receipt':{'operation_key':'approved-generation'}}))
  owner=await s.get(m.WorkflowRunModel,x.active);owner.metadata_json={**owner.metadata_json,'planning_submissions':{str(rid):{'status':'verified'}}}
  candidate['_meta']={**x.candidate['_meta'],'generation_llm_run_id':str(rid)}
  final=receipt(project,x.book,x.baseline,candidate,'pass');final['changed_fields']=['world.rules','world.history_key_events'];final['feedback']=''
  qid=UUID(final['llm_run_id']);raw_report={k:deepcopy(final[k]) for k in ('decision','core_preserved','core_conflicts','authority_snapshot_hash','candidate_hash','source_evidence','candidate_evidence','feedback')}
  text=json.dumps(raw_report,ensure_ascii=False);digest=hashlib.sha256(text.encode()).hexdigest()
  final['raw_response_hash']=digest;final['review_attempts'][0]['raw_response_hash']=digest
  final['world_revision']={'schema':'full-world-semantic-revision.v1','initial_review':deepcopy(x.report),'generation_receipt':{'llm_run_id':str(rid)},'grade_inherited':False}
  s.add(m.ExecutionOperationModel(key='approved-review',scope_id='world-scope',input_hash='approved-review-hash',status='completed',owner='fixture',lease_until=now,result_json={'provider':'openai','model_name':raw['model_name'],'finish_reason':'stop','fallback_used':False,'input_tokens':20,'output_tokens':10,'content':text}))
  s.add(m.ExecutionAttemptModel(id='approved-review-paid',operation_key='approved-review',scope_id='world-scope',input_hash='approved-review-hash',model_name=raw['model_name'],status='completed',reserved_tokens=1000,actual_tokens=30,created_at=now))
  s.add(m.LlmRunModel(id=qid,project_id=store.pid,workflow_run_id=x.active,logical_role='critic',provider='openai',model_name=raw['model_name'],prompt_template='planner_source_bound_expansion_semantic_review',prompt_hash='approved-review-hash',input_tokens=20,output_tokens=10,finish_reason='stop',metadata_json={'execution_receipt':{'operation_key':'approved-review'}}))
  content=p._mark_source_bound_expansion_reviewed(candidate,final)
  a=m.PlanningArtifactVersionModel(project_id=store.pid,artifact_type='world_spec',version_no=1,status='approved',schema_version='1.0',content=content,idempotency_key='genuinely-reviewed-world')
  s.add(a)
  # A later unrelated Cast rejection must not obscure the World's own receipt.
  project.metadata_json={**project.metadata_json,'source_bound_expansion_review':{'decision':'revise','changed_fields':['cast.protagonist'],'feedback':'different stage'}}
  await s.commit()
  return a.id

async def test_next_stage_failure_can_reuse_exact_approved_revised_world_without_another_paid_call(store,monkeypatch):
 from bestseller.services import model_catalog
 x=await fixture(store);aid=await reviewed_artifact(store,x)
 monkeypatch.setattr(model_catalog,'resolve_project_model_entry',lambda m:SimpleNamespace(model='openai/MiniMax-M3.1-Flash',api_base='https://api.minimaxi.com/v1'))
 async def forbidden(*a,**kw):raise AssertionError('Reviewed World must be reused after a later Cast failure')
 monkeypatch.setattr(p,'_generate_structured_artifact',forbidden);monkeypatch.setattr(p,'_review_source_bound_expansion',forbidden)
 async with store.sessions() as s:
  project=await bound(s,store,x)
  candidate,run=await p._complete_source_bound_planning_content(s,store.settings,project=project,premise='approved action',kind='world_spec',projection=x.baseline,workflow_run_id=x.active,book_spec=x.book)
  assert run is None and candidate['_meta']['reviewed_world_reuse_artifact_id']==str(aid)
  approved=await p._approve_source_bound_world_expansion(s,store.settings,project=project,book_spec=x.book,world_baseline=x.baseline,world_derived=candidate,workflow_run_id=x.active)
  assert approved['_meta']['source_bound_expansion_draft_eligible'] is True
  assert approved['_meta']['source_bound_expansion_review_receipt']['world_revision']['initial_review']['decision']=='revise'
  assert json.loads((await s.get(m.ExecutionOperationModel,'real-rejection')).result_json['content'])['decision']=='revise'
  assert (await s.get(m.PlanningArtifactVersionModel,x.sid)).status=='canonical'

@pytest.mark.parametrize('fault',['not_approved','altered_body','unknown_generation','unknown_review','unsettled_review','old_rejection_grade','book_changed','canonical_changed'])
async def test_reviewed_artifact_reuse_requires_actual_approval_same_sources_and_settled_new_grade(store,fault):
 x=await fixture(store);aid=await reviewed_artifact(store,x)
 async with store.sessions() as s:
  a=await s.get(m.PlanningArtifactVersionModel,aid)
  if fault=='not_approved':a.status='validated'
  if fault=='altered_body':a.content={**a.content,'world_name':'changed after approval'}
  if fault=='unknown_generation':(await s.get(m.ExecutionOperationModel,'approved-generation')).status='unknown'
  if fault=='unknown_review':(await s.get(m.ExecutionOperationModel,'approved-review')).status='unknown'
  if fault=='unsettled_review':(await s.get(m.ExecutionAttemptModel,'approved-review-paid')).actual_tokens=None
  if fault=='old_rejection_grade':
   body=deepcopy(a.content);body['_meta']['source_bound_expansion_review_receipt']=deepcopy(x.report);a.content=body
  if fault=='book_changed':x.book={'unique_hook':'new incompatible book'}
  if fault=='canonical_changed':(await s.get(m.PlanningArtifactVersionModel,x.sid)).content={'different':'source'}
  await s.commit()
 async with store.sessions() as s:
  project=await bound(s,store,x)
  assert await restore.reuse_approved(s,project=project,book=x.book,baseline=x.baseline,workflow_run_id=x.active) is None
  assert (await s.get(m.ExecutionAttemptModel,'paid-world')).reserved_tokens==1000
