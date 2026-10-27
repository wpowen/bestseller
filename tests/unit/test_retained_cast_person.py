"""Actual SQLite and retained physical payloads. Synthetic content; no model network."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import hashlib,json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from sqlalchemy import select,func
from test_failed_stream_recovery import interrupted,store
from test_source_cast_parts import source,document,completion,good
from bestseller.infra.db import models as m
from bestseller.services import llm,planner,source_cast_parts as parts,retained_cast_person as retained
from bestseller.services import retained_review_input_proof as proof,source_cast_field_repair as fields
from bestseller.services.budget import bind_execution_scope,ExecutionBudgetError
from bestseller.services.source_bound_planning_submission import planning_submission_tool
pytestmark=[pytest.mark.unit,pytest.mark.asyncio]


def native(value,request):
 calls=[{'id':'synthetic-native-submit','type':'function','function':{'name':'submit_cast_spec','arguments':json.dumps(value,ensure_ascii=False)}}]
 return llm.LLMCompletionResult(content='',tool_calls=calls,raw_message={'role':'assistant','content':None,'tool_calls':deepcopy(calls)},provider='openai',model_name='openai/MiniMax-M3.1-Flash',finish_reason='tool_calls',input_tokens=3,output_tokens=4)


async def rig(store,tmp_path,monkeypatch):
 x,manager=await interrupted(store,tmp_path,monkeypatch)
 packet,contract=parts.source_packet(json.dumps(document(source()),ensure_ascii=False))
 person=parts.roster(source())[2];schema=parts.person_schema(person)
 doc={'schema':parts.POLICY,'source_packet_sha256':parts._sha(packet),'frozen_source_packet':packet,
  'reference_full_cast_contract':contract,'part_contract':schema,
  'current_part':{'required_name':person['name']},'already_generated_people':[]}
 role=store.settings.llm.planner.model_copy(update={'model':'openai/MiniMax-M3.1-Flash','api_base':'https://api.minimaxi.com/v1','reasoning_effort':'medium'})
 monkeypatch.setattr(proof,'_fresh_role',lambda *args:role);monkeypatch.setattr(proof,'_fresh_catalog',lambda:({},'synthetic-route'))
 current=llm.LLMCompletionRequest(logical_role='planner',system_prompt='Current source part task',user_prompt=parts._json(doc),fallback_response='{}',project_id=store.pid,prompt_template='planner_cast_person_part',prompt_version=parts.POLICY,max_tokens_override=16384)
 current_hash=llm._effective_input_hash(current,llm._apply_conception_phase_settings(current,role))
 async with store.sessions() as s:
  a=await s.get(m.ExecutionAttemptModel,'new-timeout-attempt');a.input_hash=current_hash
  op=await s.get(m.ExecutionOperationModel,'new-timeout');op.input_hash=current_hash
  from bestseller.services.failed_stream_recovery import key
  returned=await s.get(m.ExecutionOperationModel,key('provider-return.v1',x.scope,a.id));returned.input_hash=current_hash;returned.result_json={**returned.result_json,'input_hash':current_hash}
  await s.commit()
 evidence={'reconciliation_id':'synthetic-known-person-recovery','attempt_id':'new-timeout-attempt','expected_input_hash':current_hash,'decision':'quarantine_charged','evidence_kind':'dispatcher_exit','evidence_sha256':'c'*64,'actor':'offline authorized operator','remote_operation_closed':False,'local_dispatcher_stopped':True,'result':None}
 receipt=await manager.execution_reconciliation(x.task,evidence,settings=store.settings);assert receipt['ready_for_manual_resume']
 old_request=llm.LLMCompletionRequest(logical_role='planner',system_prompt='Native full Cast task',user_prompt=json.dumps(document(source()),ensure_ascii=False),fallback_response='{}',project_id=store.pid,workflow_run_id=x.interrupted_workflow_id,prompt_template='planner_cast_spec',prompt_version='v3-source-bound-kind-submit',max_tokens_override=65536,tools=[planning_submission_tool('cast_spec')],tool_choice='required')
 physical_hash=llm._effective_input_hash(old_request,role);run_id=uuid4();opkey=uuid4().hex;aid=uuid4().hex
 value={'protagonist':{'goal':'Missing name remains rejected'},'supporting_cast':[{'name':person['name'],'background':'Protected retained text','social_network':{'enemies':'not an array','superiors':'not an array'}}]}
 paid=native(value,old_request);paid.metadata={'paid_call_origin':llm._paid_call_origin(old_request,physical_hash),'effective_input_receipt':{'hash':physical_hash}}
 store.settings.artifact_store.local_dir=str(tmp_path/'physical');store.settings.artifact_store.retain_prompt_payloads=True
 with llm.bind_project_prompt_audit(store.pid,reason='offline physical native fixture'):
  reqref,resref,reason,replayable=llm._persist_prompt_payloads(settings=store.settings,request=old_request,llm_run_id=run_id,configured_model=role.model,effective_model=role.model,provider=paid.provider,effective_temperature=role.temperature,effective_max_tokens=65536,effective_timeout_seconds=role.timeout_seconds,effective_stream=False,effective_thinking_type=role.thinking_type,effective_reasoning_effort=role.reasoning_effort,content=paid.content,tool_call_count=1,tool_calls=paid.tool_calls,raw_message=paid.raw_message,finish_reason='tool_calls',fallback_used=False,fallback_source=None,input_tokens=3,output_tokens=4)
 assert replayable and reason is None
 async with store.sessions() as s:
  lease=datetime.now(timezone.utc)+timedelta(hours=1)
  s.add(m.LlmRunModel(id=run_id,project_id=store.pid,workflow_run_id=x.interrupted_workflow_id,logical_role='planner',provider=paid.provider,model_name=paid.model_name,prompt_template=old_request.prompt_template,prompt_version=old_request.prompt_version,prompt_hash=physical_hash,finish_reason='tool_calls',input_tokens=3,output_tokens=4,request_payload_ref=reqref,response_payload_ref=resref,metadata_json={'execution_receipt':{'scope_id':x.scope,'operation_key':opkey,'attempts':[{'attempt_id':aid,'status':'completed'}]},'effective_input_receipt':{'hash':physical_hash}}))
  s.add(m.ExecutionOperationModel(key=opkey,scope_id=x.scope,input_hash=physical_hash,status='completed',owner='original-native',lease_until=lease,result_json=paid.model_dump(mode='json')))
  s.add(m.ExecutionAttemptModel(id=aid,operation_key=opkey,scope_id=x.scope,input_hash=physical_hash,model_name=paid.model_name,status='completed',reserved_tokens=7,actual_tokens=7,created_at=datetime.now(timezone.utc)))
  wire_key=hashlib.sha256(('transport-result.v1\n'+x.scope+'\n'+aid).encode()).hexdigest()
  s.add(m.ExecutionOperationModel(key=wire_key,scope_id=x.scope,input_hash=physical_hash,status='transport_result',owner='original-native',lease_until=lease,result_json={'operation_key':opkey,'attempt_id':aid,'input_hash':physical_hash,'result':paid.model_dump(mode='json')}))
  owner=m.WorkflowRunModel(project_id=store.pid,workflow_type='generate_foundation_plan',status='running',metadata_json={'execution_scope_id':x.scope});s.add(owner);await s.flush();wid=owner.id
  b=await s.get(m.ExecutionBudgetModel,x.scope);b.calls_used+=1;b.tokens_charged+=7
  await s.commit()
 return SimpleNamespace(x=x,doc=doc,schema=schema,current=current,wid=wid,run=run_id,opkey=opkey,aid=aid,wire_key=wire_key,value=value,role=role)


async def test_real_retained_person_is_unapproved_exact_source_material_and_only_two_fields_change(store,tmp_path,monkeypatch):
 r=await rig(store,tmp_path,monkeypatch)
 async with store.sessions() as s, _scope(r.x.scope):
  project=await s.get(m.ProjectModel,store.pid)
  value,base,origin=await retained.restore(s,store.settings,project=project,request=r.current,document=r.doc,schema=r.schema,workflow_run_id=r.wid)
  before=deepcopy(value);assert origin['whole_submission_status']=='rejected_schema' and origin['container_pointer']=='$/supporting_cast/0'
  assert base.content=='' and base.tool_calls and base.finish_reason=='tool_calls' and base.llm_run_id==r.run
  assert origin['projection_receipt']['isolated_predecessor']['operation_key']=='new-timeout'
  requests=[]
  async def correct(session,settings,request):
   requests.append(request);doc=json.loads(request.user_prompt)
   assert set(doc['part_contract']['required'])=={'$/person/social_network/enemies','$/person/social_network/superiors'}
   assert doc['retained_part']['raw_json_origin']=='lossless_json_pointer_projection_not_provider_message'
   audit=doc['retained_native_container']
   assert audit['container_sha256']==hashlib.sha256(origin['container_json'].encode()).hexdigest()
   assert audit['container_operation_key']==r.opkey
   assert audit['projected_person_sha256']==parts._sha(before)
   assert json.loads(doc['retained_part']['raw_json'])==before
   assert doc['frozen_source_packet']==r.doc['frozen_source_packet']
   assert audit['full_native_container_retained_in_audit'] is True and 'raw_asset_json' not in audit
   return completion({'$/person/social_network/enemies':[],'$/person/social_network/superiors':[]},request)
  monkeypatch.setattr(llm,'complete_text',correct);monkeypatch.setattr(planner,'_heartbeat_planner_workflow',AsyncMock())
  result,receipt=await fields.repair(s,store.settings,project=project,part_system='Current source part task',original_document=r.doc,schema=r.schema,value=value,base_completion=base,recipe_hash='e'*64,part_id='person:2',workflow_run_id=r.wid,before_dispatch=AsyncMock(),retained_origin=origin)
  assert len(requests)==1 and result['person']['background']==before['person']['background']
  assert result['person']['name']==before['person']['name'] and value==before
  assert not receipt['quality_approval_granted'] and not receipt['single_provider_payload_claimed']
  assert (await s.get(m.ExecutionOperationModel,'new-timeout')).status=='stopped'
  hold=await s.get(m.ExecutionAttemptModel,'new-timeout-attempt');assert (hold.status,hold.reserved_tokens,hold.actual_tokens)==('reconciled_charged',1065536,None)
  assert (await s.get(m.ExecutionBudgetModel,r.x.scope)).calls_used==3


from contextlib import asynccontextmanager
@asynccontextmanager
async def _scope(scope):
 with bind_execution_scope(scope):yield


@pytest.mark.parametrize('fault',['source','contract','paid_body','transport','usage','settlement','source_owner','quarantine','owner','exact_cache'])
async def test_retained_recovery_rejects_mismatch_and_never_dispatches_a_person(store,tmp_path,monkeypatch,fault):
 r=await rig(store,tmp_path,monkeypatch)
 async with store.sessions() as s, _scope(r.x.scope):
  project=await s.get(m.ProjectModel,store.pid)
  if fault=='source':r.doc['frozen_source_packet']['projection']['protagonist']['goal']='changed source'
  if fault=='contract':r.doc['reference_full_cast_contract']['kind']='different'
  if fault=='paid_body':
   op=await s.get(m.ExecutionOperationModel,r.opkey);op.result_json={**op.result_json,'content':'changed return'}
  if fault=='transport':await s.delete(await s.get(m.ExecutionOperationModel,r.wire_key))
  if fault=='usage':(await s.get(m.LlmRunModel,r.run)).input_tokens=99
  if fault=='settlement':(await s.get(m.ExecutionAttemptModel,r.aid)).actual_tokens=None
  if fault=='source_owner':
   run=await s.get(m.LlmRunModel,r.run)
   owner=await s.get(m.WorkflowRunModel,run.workflow_run_id);owner.metadata_json={'execution_scope_id':'foreign'}
  if fault=='quarantine':project.metadata_json={**project.metadata_json,'execution_reconciliation_history':[]}
  if fault=='owner':(await s.get(m.WorkflowRunModel,r.wid)).status='failed'
  if fault=='exact_cache':
   op=await s.get(m.ExecutionOperationModel,'new-timeout');op.status='completed';op.result_json={'fixture':'existing per-person cache wins'}
  await s.commit()
  if fault in ('quarantine','exact_cache'):
   assert await retained.restore(s,store.settings,project=project,request=r.current,document=r.doc,schema=r.schema,workflow_run_id=r.wid) is None
  else:
   with pytest.raises((ExecutionBudgetError,ValueError)):
    await retained.restore(s,store.settings,project=project,request=r.current,document=r.doc,schema=r.schema,workflow_run_id=r.wid)
 assert store.calls==[]


async def test_extraction_does_not_accept_protocol_conflicts_or_duplicate_names():
 request=SimpleNamespace()
 value={'protagonist':{'name':'甲'},'supporting_cast':[{'name':'乙'}]}
 paid=native(value,request);assert retained.extract(paid,name='乙')[0]['person']=={'name':'乙'}
 corrupted=paid.model_copy(deep=True);corrupted.raw_message['tool_calls']=[]
 with pytest.raises(ValueError):retained.extract(corrupted,name='乙')
 value['supporting_cast'].append({'name':'乙'})
 with pytest.raises(ValueError,match='ambiguous'):retained.extract(native(value,request),name='乙')


async def test_real_parts_pipeline_reuses_retained_person_without_a_full_person_send_and_keeps_final_review(store,monkeypatch):
 from bestseller.services import retained_cast_person as module
 calls=[];source_run=uuid4();fixed=[]
 async def restored(session,settings,*,project,request,document,schema,workflow_run_id):
  if request.metadata['cast_part_id']!='person:2':return None
  person={'name':'丙','gender':'male','pronoun_set_zh':'他','pronoun_set_en':'he/him','background':'Retained body','social_network':{'enemies':'bad'}}
  old=native({'protagonist':{'name':'甲'},'supporting_cast':[deepcopy(person)]},request);old.llm_run_id=source_run
  return {'person':deepcopy(person)},old,{'container_json':old.tool_calls[0]['function']['arguments'],'container_pointer':'$/supporting_cast/0','whole_submission_status':'rejected_schema','projection_receipt':{'source_operation_key':'synthetic-native-source','wire_errors':['schema_type:$/person/social_network/enemies:array']}}
 async def respond(session,settings,request):
  calls.append(request)
  if request.prompt_template==fields.TEMPLATE:
   fixed.append(request);return completion({'$/person/social_network/enemies':[]},request)
  assert request.metadata['cast_part_id']!='person:2'
  return completion(good(request),request)
 monkeypatch.setattr(module,'restore',restored);monkeypatch.setattr(llm,'complete_text',respond)
 async with store.sessions() as s:
  p=await s.get(m.ProjectModel,store.pid);wf=m.WorkflowRunModel(project_id=store.pid,workflow_type='generate_foundation_plan',status='running',metadata_json={});s.add(wf);await s.commit()
  result=await parts.generate(s,store.settings,project=p,system='Original source task',user=json.dumps(document(source()),ensure_ascii=False),projection=source(),workflow_run_id=wf.id,validate=lambda value:None,before_dispatch=AsyncMock())
  assert len(calls)==4 and len(fixed)==1 and result['supporting_cast'][1]['background']=='Retained body'
  receipts=result['_meta']['composite_generation_receipt']['parts'];target=next(x for x in receipts if x['part_id']=='person:2')
  assert target['new_whole_person_generation_calls']==0 and target['finish_reason']=='tool_calls'
  assert target['llm_run_ids'][0]==str(source_run) and not target['quality_approval_granted']
  assert not result['_meta']['approved_complete'] and await s.scalar(select(func.count()).select_from(m.PlanningArtifactVersionModel))==0
