import json
from types import SimpleNamespace
import pytest
from bestseller.services import hook_pull_judge as judge, llm
from bestseller.services.budget import ExecutionBudgetError
from bestseller.settings import LLMRoleSettings
pytestmark=pytest.mark.unit


@pytest.mark.asyncio
async def test_real_request_construction_reserves_thinking_and_versions_both_channels(monkeypatch):
    captured=[]
    async def complete(session,settings,request):
        captured.append(request)
        content=({'goal_quote':'要重建街坊的信任','craving':'完成承诺','score':7,'reason':'具体行动'}
                 if request.prompt_template=='hook_pull_judge' else {'hits':[]})
        return SimpleNamespace(content=json.dumps(content,ensure_ascii=False))
    monkeypatch.setattr(llm,'complete_text',complete)
    result=await judge.evaluate_hook_pull(None,None,title='修桥之后',hook='她要重建街坊的信任，决定亲手修好断桥。',genre='现实',channel='通用',samples=1)
    assert result is not None and result.score==7 and len(captured)==2
    role=LLMRoleSettings(model='openai/MiniMax-M3.1-Flash',max_tokens=32768,temperature=1,timeout_seconds=180,api_base='https://api.minimaxi.com/v1')
    for request in captured:
        assert request.prompt_version=='v2' and request.max_tokens_override==4096
        effective=llm._effective_input_payload(request,role)
        assert effective['max_tokens']==4096 and effective['reasoning_effort']=='low'
        old=request.model_copy(update={'max_tokens_override':300,'prompt_version':'v1'})
        assert llm._effective_input_hash(old,role)!=llm._effective_input_hash(request,role)


@pytest.mark.asyncio
async def test_unknown_budget_error_stops_all_remaining_samples_immediately(monkeypatch):
    calls=[]
    async def blocked(*args):
        calls.append(1);raise ExecutionBudgetError('execution_result_unknown','fixture')
    monkeypatch.setattr(llm,'complete_text',blocked)
    with pytest.raises(ExecutionBudgetError,match='execution_result_unknown'):
        await judge.evaluate_hook_pull(None,None,title='合成试题',hook='主角要找到答案',genre='现实',samples=3)
    assert len(calls)==1


def test_generic_goals_need_verbatim_evidence_without_forced_power_or_reversal():
    _,prompt=judge.build_hook_pull_messages(title='合成题目',hook='她要兑现约定',genre='现实',channel='通用')
    assert '关系修复' in prompt and '履行承诺' in prompt
    assert '上面五类之内' not in prompt
    assert 'goal_quote' in prompt and '总分不得超过 4' in prompt
    assert judge.parse_hook_pull_verdict('{"goal_quote":"虚构逆袭","craving":"升官","score":9}',source_text='她要兑现约定') is None
