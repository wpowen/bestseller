"""Request sizing and real native schema retry; no supplier or production DB."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from bestseller.services import model_catalog,planner
from bestseller.services.source_cast_output_allocation import allocation,next_after_length,prompt_suffix,POLICY
from bestseller.services.source_bound_planning_submission import parse_planning_submission,planning_submission_tool,planning_submission_contract,PlanningSubmissionError
from bestseller.services.word_targets import model_output_token_ceiling
from bestseller.services.model_context_capacity import bind_model_capacity
from bestseller.services.llm import LLMCompletionResult,_effective_request_max_tokens
from bestseller.services.budget import ExecutionBudgetError
from bestseller.infra.db.models import ProjectModel
from bestseller.settings import load_settings

pytestmark=pytest.mark.unit

def source(n=6):
 return {'protagonist':{'name':'甲','goal':'保留原始记录'},'supporting_cast':[{'name':f'伙伴{i}','role':'supporting'} for i in range(n-1)]}

@pytest.fixture
def selected(monkeypatch):
 entry=SimpleNamespace(model='openai/MiniMax-M3.1-Flash',api_base='https://api.minimaxi.com/v1')
 monkeypatch.setattr(model_catalog,'resolve_project_model_entry',lambda meta:entry)
 project=ProjectModel(id=uuid4(),slug='cast-allocation-test',title='test',genre='realistic',language='zh-CN',target_chapters=4,target_word_count=8000,metadata_json={})
 return project,entry

def plan(project,payload):return allocation(kind='cast_spec',project=project,projection=payload,prompt_version='v3-source-bound-kind-submit')

def test_full_source_sizing_retains_every_person_and_never_grants_approval(selected):
 project,_=selected;payload=source();before=deepcopy(payload);p=plan(project,payload)
 assert p['schema']==POLICY and p['roster_count']==6 and p['initial_output_tokens']>12288
 assert p['source_truncated'] is False and p['roster_reduced'] is False and p['quality_approval_granted'] is False
 assert payload==before and 'string-or-null' in prompt_suffix(p)
 assert plan(project,source(12))['initial_output_tokens']>p['initial_output_tokens']

@pytest.mark.parametrize('model,endpoint',[('openai/MiniMax-M3','https://api.minimaxi.com/v1'),('openai/MiniMax-M3.1-Flash','https://not-minimax.example/v1')])
def test_unknown_model_or_route_does_not_get_expanded_allowance(selected,model,endpoint):
 project,entry=selected;entry.model=model;entry.api_base=endpoint
 assert plan(project,source()) is None

def test_non_source_or_non_cast_stages_are_unchanged(selected):
 project,_=selected
 assert allocation(kind='world_spec',project=project,projection=source(),prompt_version='v3-source-bound-kind-submit') is None
 assert allocation(kind='cast_spec',project=project,projection=source(),prompt_version='1.0') is None
 assert model_output_token_ceiling('openai/MiniMax-M3.1-Flash')==524288
 assert model_output_token_ceiling('openai/MiniMax-M3')==32768
 assert model_output_token_ceiling('openai/MiniMax-M3.1-Flash-Preview')==32768

def test_length_growth_stops_at_real_model_ceiling(selected):
 p=plan(selected[0],source());current=p['initial_output_tokens']
 while current<p['model_output_ceiling']:
  following=next_after_length(current,p);assert following>current and following<=524288;current=following
 with pytest.raises(ValueError,match='capacity_exhausted'):next_after_length(current,p)

def physical(payload,finish='tool_calls'):
 calls=[{'id':'test-submit','type':'function','function':{'name':'submit_cast_spec','arguments':json.dumps(payload,ensure_ascii=False)}}]
 return LLMCompletionResult(content='',provider='test',model_name='test',llm_run_id=uuid4(),finish_reason=finish,tool_calls=calls,
  raw_message={'role':'assistant','content':'','tool_calls':deepcopy(calls)})

def test_native_nullable_diagnostic_names_deep_field_without_coercion():
 payload=source();payload['protagonist']['life_history']={'education':['职业学校']};returned=physical(payload);before=deepcopy(returned)
 with pytest.raises(PlanningSubmissionError) as e:parse_planning_submission(returned,kind='cast_spec')
 assert '$/protagonist/life_history/education:string' in str(e.value)
 assert returned==before and payload['protagonist']['life_history']['education']==['职业学校']
 payload['protagonist']['life_history']['education']='职业学校'
 assert parse_planning_submission(physical(payload),kind='cast_spec')[0]==payload

@pytest.mark.asyncio
@pytest.mark.parametrize('unknown',[False,True])
async def test_actual_generator_increases_length_cap_and_uses_exact_schema_diagnostic(selected,monkeypatch,unknown):
 project,_=selected;settings=load_settings();settings.pipeline.planning_artifact_reuse_enabled=False
 monkeypatch.setattr(planner,'_heartbeat_planner_workflow',AsyncMock())
 failures=[];monkeypatch.setattr(planner,'_persist_failing_planner_output',lambda **kwargs:failures.append(kwargs))
 payload=source();requests=[]
 async def complete(session,settings,request):
  requests.append(request.model_copy(deep=True))
  if unknown:raise ExecutionBudgetError('execution_result_unknown','test-scope')
  if len(requests)==1:return physical(payload,'length')
  if len(requests)==2:
   invalid=deepcopy(payload);invalid['protagonist']['life_history']={'education':['wrong wire type']};return physical(invalid)
  return physical(payload)
 monkeypatch.setattr(planner,'complete_text',complete)
 from bestseller.services.source_bound_planning_content import SOURCE_BOUND_PLANNING_PROTOCOL
 doc={'protocol':SOURCE_BOUND_PLANNING_PROTOCOL,'kind':'cast_spec','current_output_contract':{'kind':'cast_spec','expected_root':'object','native_submission':planning_submission_contract('cast_spec')},'approved_sources':{'literal':'source preserved'},'source_projection':deepcopy(payload)}
 before=deepcopy(doc);dispatch=AsyncMock()
 async def invoke():
  with bind_model_capacity('MiniMax-M3.1-Flash','https://api.minimaxi.com/v1'):
   return await planner._generate_structured_artifact(None,settings,project=project,logical_name='cast_spec',system_prompt='Submit the complete typed asset.',user_prompt=json.dumps(doc,ensure_ascii=False),fallback_payload=payload,workflow_run_id=uuid4(),
    validator=lambda value:None,abort_on_fallback=True,merge_fallback=False,prompt_version='v3-source-bound-kind-submit',attach_default_methodology=False,
    output_tool=planning_submission_tool('cast_spec'),output_parser=lambda result:parse_planning_submission(result,kind='cast_spec')[:2],before_dispatch=dispatch)
 if unknown:
  with pytest.raises(ExecutionBudgetError,match='execution_result_unknown'):await invoke()
  assert len(requests)==1 and dispatch.await_count==1;return
 value,_=await invoke()
 assert {k:v for k,v in value.items() if k!='_meta'}==payload and doc==before
 assert len(requests)==3 and dispatch.await_count==3
 assert requests[1].max_tokens_override>requests[0].max_tokens_override>12288
 assert requests[2].max_tokens_override==requests[1].max_tokens_override
 assert '$/protagonist/life_history/education:string' in requests[2].user_prompt
 assert all(POLICY in request.user_prompt and request.metadata['source_cast_output_allocation']['quality_approval_granted'] is False for request in requests)
 role=settings.llm.planner.model_copy(update={'model':'openai/MiniMax-M3.1-Flash','max_tokens':32768})
 assert _effective_request_max_tokens(role,requests[0])==requests[0].max_tokens_override


def test_missing_identity_and_array_types_all_reported_without_mutating_or_approving():
 payload=source();del payload['protagonist']['name']
 payload['protagonist']['social_network']={'family':'','enemies':''}
 payload['supporting_cast'][0]['family_imprint']={'family_secrets':''}
 before=deepcopy(payload);returned=physical(payload)
 with pytest.raises(PlanningSubmissionError) as error:
  parse_planning_submission(returned,kind='cast_spec')
 receipt=error.value.receipt;issues=receipt['wire_diagnostics']
 assert 'schema_required:$/protagonist:missing=name' in issues
 assert 'schema_type:$/protagonist/social_network/family:array' in issues
 assert 'schema_type:$/protagonist/social_network/enemies:array' in issues
 assert 'schema_type:$/supporting_cast/0/family_imprint/family_secrets:array' in issues
 assert receipt['status']=='rejected' and receipt['grade_reused'] is False
 assert json.loads(receipt['asset_json'])==before and payload==before
 assert 'schema_required:$/protagonist:missing=name' in str(error.value)


@pytest.mark.asyncio
async def test_actual_schema_repair_keeps_complete_rejected_candidate_and_all_typed_feedback(selected,monkeypatch):
 project,_=selected;settings=load_settings();settings.pipeline.planning_artifact_reuse_enabled=False
 monkeypatch.setattr(planner,'_heartbeat_planner_workflow',AsyncMock())
 monkeypatch.setattr(planner,'_persist_failing_planner_output',lambda **kwargs:None)
 valid=source();invalid=deepcopy(valid);del invalid['protagonist']['name']
 invalid['protagonist']['social_network']={'family':'','enemies':''}
 requests=[];bad=physical(invalid)
 async def complete(session,settings,request):
  requests.append(request.model_copy(deep=True))
  return bad if len(requests)==1 else physical(valid)
 monkeypatch.setattr(planner,'complete_text',complete)
 from bestseller.services.source_bound_planning_content import SOURCE_BOUND_PLANNING_PROTOCOL
 doc={'protocol':SOURCE_BOUND_PLANNING_PROTOCOL,'kind':'cast_spec','current_output_contract':{'kind':'cast_spec','expected_root':'object','native_submission':planning_submission_contract('cast_spec')},'approved_sources':{'literal':'ALL ORIGINAL FACTS'},'source_projection':deepcopy(valid)}
 with bind_model_capacity('MiniMax-M3.1-Flash','https://api.minimaxi.com/v1'):
  value,_=await planner._generate_structured_artifact(None,settings,project=project,logical_name='cast_spec',system_prompt='Submit exact schema.',user_prompt=json.dumps(doc),fallback_payload=valid,workflow_run_id=uuid4(),validator=lambda value:None,abort_on_fallback=True,merge_fallback=False,prompt_version='v3-source-bound-kind-submit',attach_default_methodology=False,output_tool=planning_submission_tool('cast_spec'),output_parser=lambda result:parse_planning_submission(result,kind='cast_spec')[:2])
 assert len(requests)==2
 prompt=requests[1].user_prompt
 assert 'ALL ORIGINAL FACTS' in prompt and 'schema_required:$/protagonist:missing=name' in prompt
 assert 'schema_type:$/protagonist/social_network/family:array' in prompt
 assert 'schema_type:$/protagonist/social_network/enemies:array' in prompt
 feedback=json.loads(prompt.split('[SOURCE-BOUND SUBMISSION CORRECTION]')[1].split('\n')[-1])
 rejected=feedback['rejected_candidate']
 assert json.loads(rejected['raw_asset_json'])==invalid
 assert rejected['source_llm_run_id']==str(bad.llm_run_id)
 assert rejected['authority']=='unreviewed_rejected_model_proposal'
 assert requests[0].tools==requests[1].tools and requests[0].max_tokens_override==requests[1].max_tokens_override
 assert {k:v for k,v in value.items() if k!='_meta'}==valid
