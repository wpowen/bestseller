"""All real roles stop oversized effective requests before claim or reservation."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from bestseller.services import llm
from bestseller.services.llm_capacity import measure_request_capacity, assert_effective_request_capacity, ModelRequestCapacityError
from bestseller.settings import load_settings
pytestmark=pytest.mark.unit


def test_tools_output_candidates_and_complete_messages_are_counted_without_mutation():
 payload={'messages':[{'role':'system','content':'Read the whole source.'},{'role':'user','content':'潮位原文'*30}],
          'tools':[{'type':'function','function':{'name':'submit','parameters':{'description':'Exact complete schema.'}}}],
          'tool_choice':'required','max_tokens':4096,'n_candidates':2}
 saved=deepcopy(payload);receipt=assert_effective_request_capacity(payload)
 assert receipt['output_reserve_tokens']==8192 and receipt['effective_max_output_tokens']==4096
 assert receipt['tool_tokens']>0 and receipt['message_count']==2 and receipt['safety_factor']>=2.2
 assert receipt['safety_factor']>receipt['observed_input_estimator_ratio_floor'] and payload==saved
 assert receipt['total_budget']==65536 and receipt['provider_usage_claimed'] is False


def test_large_ascii_enum_schema_cannot_hide_cjk_or_output_reserve():
 payload={'messages':[{'role':'user','content':'{'+('"safe":"全量角色来源",'*9000)+'}'}],
          'tools':[{'description':'0123456789abcdef'*4000}],'max_tokens':4000,'n_candidates':1}
 with pytest.raises(ModelRequestCapacityError) as caught:assert_effective_request_capacity(payload)
 assert caught.value.receipt['reserved_total_tokens']>65536 and caught.value.receipt['mixed_script_input_floor']>0
 assert caught.value.receipt['output_reserve_tokens']==4000


@pytest.mark.parametrize('role',['writer','editor','critic','planner'])
@pytest.mark.asyncio
async def test_all_roles_without_opt_in_flag_stop_before_claim(monkeypatch,role):
 settings=load_settings(env={}).model_copy(deep=True);settings.llm.mock=False
 monkeypatch.setattr(llm,'apply_runtime_llm_profile',lambda s:s)
 monkeypatch.setattr(llm,'_resolve_project_model_override',AsyncMock(return_value=None))
 claim=AsyncMock(side_effect=AssertionError('Capacity must stop before claim'));reserve=AsyncMock(side_effect=AssertionError('Capacity must stop before reserve'))
 provider=AsyncMock(side_effect=AssertionError('Capacity must stop before provider'))
 monkeypatch.setattr(llm.ExecutionLedger,'claim',claim);monkeypatch.setattr(llm.ExecutionLedger,'reserve',reserve);monkeypatch.setattr(llm,'_call_litellm',provider)
 request=llm.LLMCompletionRequest(logical_role=role,system_prompt='Source data only',user_prompt='ASCII source structure '*140000,fallback_response='not dispatched',prompt_template='chapter_first_writer',prompt_version='fixture',max_tokens_override=16384,metadata={})
 with pytest.raises(ModelRequestCapacityError) as stopped:await llm.complete_text(SimpleNamespace(bind=None),settings,request)
 assert stopped.value.receipt['reserved_total_tokens']>stopped.value.receipt['total_budget']
 claim.assert_not_awaited();reserve.assert_not_awaited();provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_final_retry_transport_rechecks_actual_role_output_before_reserve(monkeypatch):
 settings=load_settings(env={});role=settings.llm.critic.model_copy(update={'model':'openai/MiniMax-M3','api_base':'https://api.minimaxi.com/v1','max_tokens':16384})
 request=llm.LLMCompletionRequest(logical_role='critic',system_prompt='Source',user_prompt='full source '*17000,fallback_response='not used')
 reserve=AsyncMock(side_effect=AssertionError('No physical reservation'));provider=AsyncMock(side_effect=AssertionError('No physical send'))
 call=SimpleNamespace(ledger=SimpleNamespace(scope_id='scope:fixture',reserve=reserve),claim=None,attempts=[])
 token=llm._execution_call_var.set(call)
 monkeypatch.setattr(llm,'_execution_boundary',AsyncMock())
 monkeypatch.setattr(llm,'_install_physical_transport_guard',lambda:None)
 monkeypatch.setattr(llm,'_call_litellm',provider)
 try:
  with pytest.raises(ModelRequestCapacityError):await llm._paid_transport_attempt(request,role)
 finally:llm._execution_call_var.reset(token)
 reserve.assert_not_awaited();provider.assert_not_awaited()


@pytest.mark.parametrize('output',[0,-1,True,'4000'])
def test_output_is_exact_positive_effective_integer(output):
 with pytest.raises(ValueError):measure_request_capacity([{'role':'user','content':'s'}],tools=None,output_tokens=output)


def test_component_counting_is_monotone_across_legacy_one_third_threshold():
 from bestseller.services.prompt_compiler import estimate_legacy_prompt_tokens
 low='汉'*99+'x'*201
 high='汉'*99+'x'*198
 assert len(high)<len(low) and estimate_legacy_prompt_tokens(high)>estimate_legacy_prompt_tokens(low)
 def measured(value):return measure_request_capacity([{'role':'system','content':'Actual source scope'},{'role':'user','content':value}],tools=[{'description':'schema'}],output_tokens=16384)
 assert measured(high)['reserved_total_tokens']<=measured(low)['reserved_total_tokens']
 for count in range(170,231):
  assert measured('汉'*99+'x'*count)['reserved_total_tokens']<=measured('汉'*99+'x'*(count+1))['reserved_total_tokens']
 assert measured(low)['safety_factor']==2.2 and measured(low)['total_budget']==65536
