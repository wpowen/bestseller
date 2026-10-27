"""Lossless six-person shape and real SQLite identity fences; no supplier calls."""
from copy import deepcopy
import json
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
import pytest
from test_execution_phase_handoff import ready,store
from bestseller.services import cast_links_transport as transport,llm,retained_review_input_proof as proof
from bestseller.services.model_context_capacity import bind_model_capacity
from bestseller.services.budget import bind_execution_scope,ExecutionLedger,ExecutionBudgetError
from bestseller.infra.db import models as m
pytestmark=pytest.mark.unit

def document():
 repeated='完整保留原始约束，引用标记不是指令。'*90
 schema={'type':'object','properties':{'layout':{'type':'object'}},'required':['layout'],'additionalProperties':False}
 data={'schema':'source-bound-cast-parts.v1','frozen_source_packet':{'canonical':repeated,'all_source_sections':[repeated]*80,'authored_reference':{'$ref':'/not-an-authorized-source'}},'part_contract':schema,'current_part':{'instruction':'Keep every person; global layout only.'},'already_generated_people':[{'part_id':f'person:{i}','person':{'name':f'合成人物{i}','background':repeated}} for i in range(6)]}
 return data,schema

def test_overflow_roundtrip_keeps_every_person_field_authored_reference_and_retry_suffix():
 data,schema=document();before=deepcopy(data);suffix='\n\n'+json.dumps({'schema_correction_only':True,'rejected_part':'unchanged feedback'},ensure_ascii=False)
 raw=json.dumps(data,ensure_ascii=False)+suffix
 with bind_model_capacity('openai/MiniMax-M3.1-Flash','https://api.minimaxi.com/v1'):wire,receipt=transport.prepare('Exact original system.',raw,schema=schema,output_tokens=16384)
 restored,returned_suffix=transport.decode(wire)
 assert restored==before and returned_suffix==suffix and data==before
 assert [p['person']['name'] for p in restored['already_generated_people']]==[f'合成人物{i}' for i in range(6)]
 assert restored['frozen_source_packet']['authored_reference']=={'$ref':'/not-an-authorized-source'}
 assert receipt['roundtrip_verified'] and receipt['all_sources_and_people_preserved'] and receipt['capacity']['budget_passed']
 assert receipt['capacity']['combined_chars']<=120000 and len(wire)<len(raw)

@pytest.mark.parametrize('fault',['codec','contract','instruction'])
def test_tampered_overflow_cannot_be_decoded_as_original(fault):
 data,schema=document()
 with bind_model_capacity('openai/MiniMax-M3.1-Flash','https://api.minimaxi.com/v1'):wire,_=transport.prepare('system',json.dumps(data,ensure_ascii=False),schema=schema,output_tokens=16384)
 payload=json.loads(wire)
 if fault=='codec':payload['logical_document']['value']={}
 if fault=='contract':payload['current_part_contract']['required']=[]
 if fault=='instruction':payload['expansion_instruction']='different decoder'
 with pytest.raises(ValueError):transport.decode(json.dumps(payload,ensure_ascii=False))

def test_fitting_bytes_unchanged_and_uncompressible_overflow_stays_blocked():
 small={'all':'small complete payload'};raw=json.dumps(small)
 with bind_model_capacity('openai/MiniMax-M3.1-Flash','https://api.minimaxi.com/v1'):
  wire,receipt=transport.prepare('system',raw,schema={'type':'object'},output_tokens=8192)
  assert wire==raw and receipt['encoding']=='identity'
  data,schema=document();data['frozen_source_packet']={'single_unique_value':'不可删的完整原文'*25000}
  with pytest.raises(ValueError,match='capacity_exceeded'):transport.prepare('system',json.dumps(data,ensure_ascii=False),schema=schema,output_tokens=16384)

async def setup_identity(store,tmp_path,monkeypatch):
 x=await ready(store,tmp_path)
 role=store.settings.llm.planner.model_copy(update={'model':'openai/MiniMax-M3.1-Flash','api_base':'https://api.minimaxi.com/v1','reasoning_effort':'medium','stream':True})
 monkeypatch.setattr(proof,'_fresh_role',lambda *args:role);monkeypatch.setattr(proof,'_fresh_catalog',lambda:({},'synthetic'))
 request=llm.LLMCompletionRequest(logical_role='planner',system_prompt='original system',user_prompt='different physical wire',fallback_response='{}',project_id=store.pid,prompt_template='planner_cast_links_part',prompt_version='source-bound-cast-parts.v1',max_tokens_override=16384,metadata={'cast_recipe_sha256':'e'*64})
 return x,role,request

@pytest.mark.asyncio
async def test_different_wire_same_logical_input_cannot_get_a_new_paid_identity(store,tmp_path,monkeypatch):
 x,role,request=await setup_identity(store,tmp_path,monkeypatch)
 with bind_execution_scope(x.scope):
  async with store.sessions() as s:
   p=await s.get(m.ProjectModel,store.pid)
   first=await transport.bind_logical_identity(s,store.settings,project=p,request=request,original_user='same complete logical document',receipt={'encoding':'lossless-source-encoding.v1'})
   second=await transport.bind_logical_identity(s,store.settings,project=p,request=request.model_copy(update={'user_prompt':'another encoding of the same data'}),original_user='same complete logical document',receipt={'encoding':'lossless-source-encoding.v3'})
  assert first.operation_id==second.operation_id and first.sample_slot==second.sample_slot==0
  assert first.metadata['prepared_effective_input']['hash']!=second.metadata['prepared_effective_input']['hash']
  ledger=ExecutionLedger(store.engine,x.scope,store.settings.budget)
  claim=await ledger.claim(first.operation_id+':sample:0',first.metadata['prepared_effective_input']['hash'])
  await ledger.finish(claim,None)
  with pytest.raises(ExecutionBudgetError):await ledger.claim(second.operation_id+':sample:0',second.metadata['prepared_effective_input']['hash'])
 assert store.calls==[]

@pytest.mark.asyncio
@pytest.mark.parametrize('status',['pending','unknown','stopped','completed'])
async def test_legacy_operation_is_never_bypassed_by_encoding(store,tmp_path,monkeypatch,status):
 x,role,request=await setup_identity(store,tmp_path,monkeypatch)
 original='same original links input';raw=request.model_copy(update={'user_prompt':original});effective=llm._effective_input_hash(raw,role)
 async with store.sessions() as s:
  s.add(m.ExecutionOperationModel(key='legacy-links',scope_id=x.scope,input_hash=effective,status=status,owner='original',lease_until=datetime.now(timezone.utc)+timedelta(hours=1)));await s.commit()
 with bind_execution_scope(x.scope):
  async with store.sessions() as s:
   p=await s.get(m.ProjectModel,store.pid)
   with pytest.raises(ExecutionBudgetError,match='operation_stopped'):
    await transport.bind_logical_identity(s,store.settings,project=p,request=request,original_user=original,receipt={'encoding':'lossless-source-encoding.v1'})
 assert store.calls==[]
