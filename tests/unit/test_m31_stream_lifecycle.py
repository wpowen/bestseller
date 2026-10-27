"""Application gateway + actual SDK + durable ledger over offline SSE only."""
import asyncio
import json
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import httpx
from openai import AsyncOpenAI
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from bestseller.infra.db.models import ExecutionAttemptModel, ExecutionOperationModel, ExecutionBudgetModel
from bestseller.services import llm, production_strategy, stream_deadlines
from bestseller.services.budget import ExecutionBudgetError, current_execution_scope
from bestseller.services.progress_context import set_cancellation_checker
from bestseller.settings import load_settings, LLMRoleSettings
from gateway_ledger_support import gateway_ledger
from test_minimax_m31_streaming import packet

pytestmark=pytest.mark.unit

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['complete','disconnect','empty','cancel','expiry'])
async def test_stream_lifetime_application_sdk_ledger(gateway_ledger,monkeypatch,mode):
    seen=[]; clock=[0.0]; cancelled=[False]; policies=[]
    original_bound=stream_deadlines.bounded_stream
    def virtual_bound(response,**kwargs):
        policies.append((kwargs['idle_seconds'],kwargs['total_seconds']))
        return original_bound(response,clock=lambda:clock[0],**kwargs)
    monkeypatch.setattr(stream_deadlines,'bounded_stream',virtual_bound)
    async def check_cancel():
        if cancelled[0]:raise asyncio.CancelledError()
    class Wire(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(5):
                clock[0]+=80
                yield packet({'reasoning_content':'synthetic meaningful thought'})
                if mode=='cancel':cancelled[0]=True
                if mode=='expiry':
                    async with AsyncSession(gateway_ledger) as session:
                        await session.execute(update(ExecutionBudgetModel).where(
                            ExecutionBudgetModel.scope_id==current_execution_scope()).values(
                                deadline_at=datetime.now(timezone.utc)-timedelta(seconds=1)))
                        await session.commit()
                if mode=='disconnect':raise httpx.ReadTimeout('offline interrupted stream')
            if mode!='empty':yield packet({'content':'complete offline book evidence'})
            yield packet(finish='stop')
            yield packet(usage={'prompt_tokens':100,'completion_tokens':30,'total_tokens':130})
            yield b'data: [DONE]\n\n'
    async def wire(request):
        seen.append(json.loads(request.content))
        assert seen[-1]['stream'] and seen[-1]['stream_options']['include_usage']
        return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Wire())
    async with httpx.AsyncClient(transport=httpx.MockTransport(wire)) as http:
        sdk=AsyncOpenAI(api_key='offline-fixture',base_url='https://api.minimaxi.com/v1',http_client=http,max_retries=0)
        async def acompletion(**kwargs):
            assert kwargs['num_retries']==kwargs['max_retries']==0
            allowed={k:v for k,v in kwargs.items() if k not in {'model','timeout','num_retries','max_retries','drop_params','api_base','api_key','base_url','allowed_openai_params'}}
            return await sdk.chat.completions.create(model='MiniMax-M3.1-Flash',**allowed)
        monkeypatch.setattr(llm,'_get_litellm',lambda:SimpleNamespace(acompletion=acompletion))
        monkeypatch.setattr(llm,'_ensure_shared_litellm_http_client',lambda:None)
        settings=load_settings(env={});settings.llm.mock=False
        settings.llm.planner.timeout_seconds=900
        settings.llm.critic=LLMRoleSettings(model='openai/MiniMax-M3.1-Flash',temperature=1,max_tokens=6500,timeout_seconds=900,api_base='https://api.minimaxi.com/v1')
        settings.llm.retry.max_attempts=3;settings.llm.retry.rate_limit_fallback_enabled=False
        req=llm.LLMCompletionRequest(logical_role='critic',system_prompt='offline synthetic source',user_prompt='review',fallback_response='never approve',prompt_template='conception_review')
        set_cancellation_checker(check_cancel)
        try:
          async with AsyncSession(gateway_ledger,expire_on_commit=False) as session:
           with (pytest.raises(ExecutionBudgetError) if mode!='complete' else nullcontext()):
            with production_strategy.protect_downstream('complete',4),llm.bind_conception_model(None,call_timeout_seconds=300),llm.bind_execution_operation('offline-long-live-stream'):
             if mode=='complete':
                 result=await llm.complete_text(session,settings,req)
                 assert result.content=='complete offline book evidence'
                 assert (result.input_tokens,result.output_tokens)==(100,30)
                 cached=await llm.complete_text(session,settings,req)
                 assert cached.content==result.content
             else:
                 with pytest.raises((ExecutionBudgetError,asyncio.CancelledError)):
                     await llm.complete_text(session,settings,req)
                 cancelled[0]=False
                 with pytest.raises(ExecutionBudgetError):
                     await llm.complete_text(session,settings,req)
        finally:set_cancellation_checker(None)
    assert len(seen)==1
    assert policies and policies[0][0]==305 and 890<policies[0][1]<=905
    async with AsyncSession(gateway_ledger) as session:
        attempts=list(await session.scalars(select(ExecutionAttemptModel)))
        assert len(attempts)==1
        if mode=='complete':
            assert clock[0]==400  # valid progress beyond the old 305s ceiling
            assert attempts[0].status=='completed' and attempts[0].actual_tokens==130
            receipt=await session.scalar(select(ExecutionOperationModel).where(ExecutionOperationModel.status=='transport_result'))
            assert receipt.result_json['result']['input_tokens']==100
        else:
            assert attempts[0].status=='unknown' and attempts[0].actual_tokens is None
            partial=await session.scalar(select(ExecutionOperationModel).where(ExecutionOperationModel.status=='unusable_provider_return'))
            assert partial.result_json['adoption_authorized'] is False
            assert partial.result_json['credit_released'] is False
            assert partial.result_json['response']['stream_complete'] is False

@pytest.mark.asyncio
async def test_idle_empty_chunks_and_hanging_iterator_retain_partial():
    async def empty_heartbeats():
        yield {'choices':[{'delta':{'reasoning_content':'one real fragment'}}]}
        while True:
            await asyncio.sleep(.002)
            yield {'choices':[{'delta':{}}]}
    with pytest.raises(TimeoutError,match='idle') as err:
        await llm._collect_minimax_streaming_content(empty_heartbeats(),idle_timeout_seconds=.02,total_timeout_seconds=.3)
    assert err.value.partial_stream_response['choices'][0]['message']['reasoning_content']=='one real fragment'
