"""Installed field-repair contract tests; synthetic model fixtures, no network or DB."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from bestseller.services import llm,planner
from bestseller.services.source_cast_parts import person_schema
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services import source_cast_field_repair as fields

def source():
 return {'person':{'name':'甲','goal':'核对原始记录','ip_anchor':{'tag_memory':['旧账本','原始记录']},'social_network':{'community':[{'name':'乙','bond':'邻居'}]},'background':'受保护的原文'}}
def schema():return person_schema({'name':'甲','source_slot':'protagonist'})
def patch():return {'$/person/ip_anchor/tag_memory':'原始记录与旧账本','$/person/social_network/community/0':'乙是邻居；关系来源不变'}

def test_exact_failed_leaves_only_preserve_original_and_all_other_fields():
 value=source();before=deepcopy(value);contract=fields.correction_contract(value,schema())
 assert set(contract['required'])==set(patch())
 result=fields.apply_corrections(value,schema(),contract,patch())
 assert value==before and result['person']['name']=='甲' and result['person']['background']=='受保护的原文'
 assert result['person']['ip_anchor']['tag_memory']==patch()['$/person/ip_anchor/tag_memory']
 assert result['person']['social_network']['community']==[patch()['$/person/social_network/community/0']]

@pytest.mark.parametrize('bad',['extra','omit','wrong_type'])
def test_patch_cannot_modify_unfailed_or_omit_fields_or_coerce_types(bad):
 value=source();before=deepcopy(value);contract=fields.correction_contract(value,schema());correction=patch()
 if bad=='extra':correction['$/person/background']='擅改原文'
 if bad=='omit':correction.pop('$/person/ip_anchor/tag_memory')
 if bad=='wrong_type':correction['$/person/ip_anchor/tag_memory']=['仍然是错误类型']
 with pytest.raises(ValueError):fields.apply_corrections(value,schema(),contract,correction)
 assert value==before

def test_unpatchable_missing_required_or_clean_data_does_not_dispatch():
 value=source();value['person'].pop('name')
 with pytest.raises(ValueError,match='not_patchable'):fields.correction_contract(value,schema())
 clean=fields.apply_corrections(source(),schema(),fields.correction_contract(source(),schema()),patch())
 with pytest.raises(ValueError,match='no_failed_fields'):fields.correction_contract(clean,schema())

def test_real_rejected_paid_result_has_only_four_typed_targets():
 row=json.loads((Path(__file__).parents[1]/'fixtures'/'cast_person0_typed_rejection.json').read_text());value=json.loads(row['result_json']['content'])
 contract=fields.correction_contract(value,person_schema({'name':'周夏','source_slot':'protagonist'}))
 assert set(contract['required'])=={'$/person/ip_anchor/tag_memory',*{f'$/person/social_network/community/{i}' for i in range(3)}}
 assert row['status']=='completed' and row['finish_reason']=='stop'

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['valid','invalid_then_valid','unknown'])
async def test_field_model_receipts_and_unknown_no_retry(monkeypatch,mode):
 value=source();before=deepcopy(value);requests=[];base=llm.LLMCompletionResult(content=json.dumps(value,ensure_ascii=False),provider='openai',model_name='openai/MiniMax-M3.1-Flash',finish_reason='stop',input_tokens=10,output_tokens=20,llm_run_id=uuid4())
 async def complete(session,settings,request):
  requests.append(request)
  if mode=='unknown':raise ExecutionBudgetError('execution_result_unknown','synthetic')
  correction=patch()
  if mode=='invalid_then_valid' and len(requests)==1:correction['$/person/ip_anchor/tag_memory']=['bad']
  return llm.LLMCompletionResult(content=json.dumps(correction,ensure_ascii=False),provider='openai',model_name='openai/MiniMax-M3.1-Flash',finish_reason='stop',input_tokens=10,output_tokens=20,llm_run_id=uuid4())
 monkeypatch.setattr(llm,'complete_text',complete);monkeypatch.setattr(planner,'_heartbeat_planner_workflow',AsyncMock())
 session=SimpleNamespace(commit=AsyncMock());project=SimpleNamespace(id=uuid4(),slug='synthetic-source',metadata_json={})
 args=dict(project=project,part_system='只返回当前字段修订，不放行质量',original_document={'frozen_source_packet':{'source':'完整原文'},'part_contract':schema()},schema=schema(),value=value,base_completion=base,recipe_hash='a'*64,part_id='person:0',workflow_run_id=uuid4(),before_dispatch=AsyncMock())
 if mode=='unknown':
  with pytest.raises(ExecutionBudgetError,match='unknown'):await fields.repair(session,SimpleNamespace(),**args)
  assert len(requests)==1 and session.commit.await_count==0
 else:
  result,receipt=await fields.repair(session,SimpleNamespace(),**args)
  assert len(requests)==(2 if mode=='invalid_then_valid' else 1)
  assert receipt['quality_approval_granted'] is False and receipt['base_llm_run_id']==str(base.llm_run_id)
  assert receipt['correction_llm_run_id']!=str(base.llm_run_id) and receipt['corrected_payload_sha256']==fields._sha(result)
  assert all(r.prompt_template==fields.TEMPLATE and r.tools is None and '完整原文' in r.user_prompt for r in requests)
 assert value==before


def test_nested_and_recursive_schema_references_keep_every_reachable_constraint():
 schema={'type':'object','properties':{'target':{'$ref':'#/$defs/A'}},'required':['target'],'additionalProperties':False,'$defs':{'A':{'type':'object','properties':{'next':{'anyOf':[{'type':'null'},{'$ref':'#/$defs/B'}]}}},'B':{'type':'object','properties':{'back':{'$ref':'#/$defs/A'}}},'Unrelated':{'type':'array','maxItems':9}}}
 before=deepcopy(schema);result=fields.referenced_definitions_only(schema)
 assert set(result['$defs'])=={'A','B'} and result['$defs']['A']==schema['$defs']['A'] and result['$defs']['B']==schema['$defs']['B']
 assert {k:v for k,v in result.items() if k!='$defs'}=={k:v for k,v in schema.items() if k!='$defs'} and schema==before

@pytest.mark.parametrize('reference',['https://outside.invalid/schema','#/$defs/Missing','#/properties/outside'])
def test_missing_or_unrecognized_reference_rejects(reference):
 with pytest.raises(ValueError):fields.referenced_definitions_only({'properties':{'x':{'$ref':reference}},'$defs':{}})

def test_scalar_array_leaf_loses_no_validation_rules_or_story_data():
 schema={'type':'object','properties':{'$/person/social_network/enemies':{'type':'array','items':{'type':'string','minLength':1}}},'required':['$/person/social_network/enemies'],'additionalProperties':False,'$defs':{'Unused':{'type':'object'}}}
 result=fields.referenced_definitions_only(schema);assert result['$defs']=={} and result['properties']==schema['properties'] and result['required']==schema['required'] and result['additionalProperties'] is False
