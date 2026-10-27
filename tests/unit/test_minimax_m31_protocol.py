"""Offline wire, SDK and durable ledger proof. No external provider is contacted."""
from copy import deepcopy
from types import SimpleNamespace
import json
import pytest
import httpx
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from bestseller.settings import LLMRoleSettings,load_settings
from bestseller.services import llm,minimax_m31_protocol as p
from bestseller.infra.db.models import ExecutionOperationModel
from gateway_ledger_support import gateway_ledger as gateway_ledger,LedgerBoundSession

pytestmark=pytest.mark.unit


def role(**updates):
 return LLMRoleSettings(model='openai/MiniMax-M3.1-Flash',temperature=.2,max_tokens=6500,timeout_seconds=30,api_base='https://api.minimaxi.com/v1',**updates)


def request():
 return llm.LLMCompletionRequest(logical_role='critic',system_prompt='Only supplied fiction data.',user_prompt='完整正文。',fallback_response='not a verdict',max_tokens_override=6500,tools=[{'type':'function','function':{'name':'submit','parameters':{'type':'object','properties':{},'required':[]}}}],tool_choice={'type':'function','function':{'name':'submit'}})

@pytest.mark.parametrize('model',['openai/MiniMax-M3','openai/MiniMax-M3.1-Flash-preview','openai/other-MiniMax-M3.1-Flash'])
def test_exact_formal_model_only(model):
 assert not p.applies(model)

@pytest.mark.parametrize('thinking',['disabled','off','none'])
def test_disabled_reasoning_rejected_before_claim(thinking):
 with pytest.raises(ValueError,match='thinking_cannot_be_disabled'):
  llm._effective_input_payload(request(),role(thinking_type=thinking))

@pytest.mark.parametrize('effort',['none','disabled','invalid'])
def test_invalid_effort_rejected_before_claim(effort):
 with pytest.raises(ValueError,match='reasoning_effort_invalid'):
  llm._effective_input_payload(request(),role(reasoning_effort=effort))


def test_effective_identity_changes_and_old_model_payload_stays_legacy():
 req=request();new=llm._effective_input_payload(req,role());oldrole=role().model_copy(update={'model':'openai/MiniMax-M3'})
 old=llm._effective_input_payload(req,oldrole)
 assert old['thinking_type']=='disabled' and 'provider_protocol' not in old
 assert new['thinking_type'] is None and new['reasoning_effort']=='max'
 assert new['output_parameter']=='max_completion_tokens' and new['max_tokens']==6500
 assert llm._effective_input_hash(req,role())!=llm._effective_input_hash(req,oldrole)
 assert new['stream'] is False and new['n_candidates']==1
 from bestseller.services.llm_capacity import assert_effective_request_capacity
 assert assert_effective_request_capacity(new)['total_budget']==1000000
 assert assert_effective_request_capacity(old)['total_budget']==65536


def test_project_override_clears_old_thinking_without_changing_auth_destination():
 old=role(thinking_type='disabled',reasoning_effort='none')
 entry=SimpleNamespace(model='openai/MiniMax-M3.1-Flash',api_base=old.api_base,api_key_env='DOMESTIC_KEY',api_key_header=None)
 selected=llm._apply_model_override(old,entry)
 assert selected.thinking_type is None and selected.reasoning_effort is None
 assert selected.api_base=='https://api.minimaxi.com/v1' and selected.api_key_env=='DOMESTIC_KEY'
 assert old.thinking_type=='disabled'


def test_full_optional_reasoning_and_tool_fields_survive_without_rewriting():
 tools=[{'id':'call1','type':'function','function':{'name':'submit','arguments':'{ "ok": true }'}}]
 raw={'role':'assistant','content':None,'reasoning_content':'保留原始可选思考字段','tool_calls':tools,'provider_extra':{'x':1}}
 before=deepcopy(raw)
 assert p.assistant_message(raw,content='',tool_calls=tools)==before and raw==before
 raw.pop('reasoning_content');assert p.assistant_message(raw,content='',tool_calls=tools)==raw
 raw['function_call']={'name':'legacy'}
 with pytest.raises(ValueError,match='legacy_function_call'):p.assistant_message(raw,content='',tool_calls=tools)


@pytest.mark.asyncio
@pytest.mark.parametrize("actual_litellm",[False,True])
async def test_actual_sdk_wire_and_full_gateway_settlement_keep_reasoning(gateway_ledger,monkeypatch,actual_litellm):
 seen=[]
 retained_effort=[];persist=llm._persist_prompt_payloads
 def capture_persist(**kwargs):
  retained_effort.append(kwargs['effective_reasoning_effort']);return persist(**kwargs)
 monkeypatch.setattr(llm,'_persist_prompt_payloads',capture_persist)
 response={'id':'fixture-only','object':'chat.completion','created':1,'model':'MiniMax-M3.1-Flash','choices':[{'index':0,'finish_reason':'tool_calls','message':{'role':'assistant','content':None,'reasoning_content':'offline fixture reasoning','tool_calls':[{'id':'call1','type':'function','function':{'name':'submit','arguments':'{ "ok": true }'}}]}}],'usage':{'prompt_tokens':100,'completion_tokens':30,'total_tokens':130}}
 async def wire(req):
  assert req.url.host=='api.minimaxi.com'
  body=json.loads(req.content);seen.append(body)
  assert body['max_completion_tokens']==6500 and 'max_tokens' not in body
  assert body['reasoning_effort']=='max' and 'thinking' not in body
  assert body.get('stream',False) is False and body['tool_choice']['function']['name']=='submit'
  return httpx.Response(200,json=response)
 async with httpx.AsyncClient(transport=httpx.MockTransport(wire)) as http:
  sdk=AsyncOpenAI(api_key='offline-fixture-not-a-secret',base_url='https://api.minimaxi.com/v1',http_client=http,max_retries=0)
  async def acompletion(**kwargs):
   assert 'max_tokens' not in kwargs and kwargs['num_retries']==kwargs['max_retries']==0
   assert kwargs.get('base_url',kwargs.get('api_base'))=='https://api.minimaxi.com/v1'
   allowed={k:v for k,v in kwargs.items() if k not in {'model','timeout','num_retries','max_retries','drop_params','api_base','api_key','base_url','allowed_openai_params'}}
   return await sdk.chat.completions.create(model='MiniMax-M3.1-Flash',**allowed)
  if actual_litellm:
   monkeypatch.setenv('LITELLM_LOCAL_MODEL_COST_MAP','True')
   import litellm
   monkeypatch.setattr(litellm,'telemetry',False)
   monkeypatch.setattr(litellm,'aclient_session',http)
   monkeypatch.setattr(llm,'_get_litellm',lambda:litellm)
   monkeypatch.setattr(llm,'get_runtime_env_value',lambda key:'offline-fixture-not-a-secret' if key=='TEST_MINIMAX_M31_KEY' else None)
  else:
   monkeypatch.setattr(llm,'_get_litellm',lambda:SimpleNamespace(acompletion=acompletion))
  monkeypatch.setattr(llm,'_ensure_shared_litellm_http_client',lambda:None)
  settings=load_settings(env={});settings.llm.mock=False;settings.llm.critic=role(api_key_env="TEST_MINIMAX_M31_KEY");settings.llm.retry.max_attempts=1
  settings.llm.retry.rate_limit_max_attempts=1;settings.llm.retry.rate_limit_fallback_enabled=False
  result=await llm.complete_text(LedgerBoundSession(),settings,request())
 assert len(seen)==1 and result.finish_reason=='tool_calls'
 assert retained_effort==['max']
 assert result.raw_message['reasoning_content']=='offline fixture reasoning'
 assert result.raw_message['tool_calls'][0]['function']['arguments']=='{ "ok": true }'
 req=request().model_copy(update={'messages_override':[result.raw_message,{'role':'tool','tool_call_id':'call1','content':'ok'}]})
 assert llm._build_messages(req,'openai')[0]==result.raw_message
 async with AsyncSession(gateway_ledger) as session:
  # Inspect the actual completed root, ignoring transport child rows.
  roots=list(await session.scalars(select(ExecutionOperationModel).where(ExecutionOperationModel.status=='completed')))
  completed=next(r for r in roots if isinstance(r.result_json,dict) and 'raw_message' in r.result_json)
  assert completed.result_json['raw_message']==result.raw_message
