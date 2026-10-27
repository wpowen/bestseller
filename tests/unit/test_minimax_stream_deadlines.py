import asyncio
import pytest
from bestseller.services import llm
from bestseller.services.stream_deadlines import conception_stream_deadlines


def frame(content=None, thought=None, finish=None, usage=None):
    return {'id':'offline-deadline','choices':[] if usage else [{'delta':{'content':content,'reasoning_content':thought},'finish_reason':finish}], 'usage':usage}


def test_only_long_conception_roles_use_existing_role_lifetime():
    assert conception_stream_deadlines(900,300,7) == (305,898)
    assert conception_stream_deadlines(180,300,7) is None
    assert conception_stream_deadlines(900,None,7) is None
    assert conception_stream_deadlines(900,300,910) == (305,.001)


@pytest.mark.asyncio
async def test_active_reasoning_can_outlive_idle_cap_and_keeps_usage():
    async def response():
        for _ in range(8):
            await asyncio.sleep(.02)
            yield frame(thought='real reasoning delta')
        yield frame(content='complete synthetic answer',finish='stop')
        yield frame(usage={'prompt_tokens':10,'completion_tokens':20})
    result=await llm._collect_minimax_streaming_content(response(),idle_timeout_seconds=.08,total_timeout_seconds=1)
    assert result[:4] == ('complete synthetic answer',10,20,'stop')
    assert result[-1]['reasoning_content']=='real reasoning delta'*8


@pytest.mark.asyncio
@pytest.mark.parametrize('idle,total,label',[(.03,1,'idle'),(.2,.06,'total')])
async def test_idle_or_total_timeout_retains_unusable_partial(idle,total,label):
    async def response():
        yield frame(thought='retained partial')
        if label=='idle':
            await asyncio.sleep(1)
        else:
            while True:
                await asyncio.sleep(.01)
                yield frame(thought='still active')
    with pytest.raises(TimeoutError,match='minimax_stream_'+label+'_timeout') as raised:
        await llm._collect_minimax_streaming_content(response(),idle_timeout_seconds=idle,total_timeout_seconds=total)
    partial=raised.value.partial_stream_response
    assert partial['stream_complete'] is False
    assert partial['choices'][0]['finish_reason'] is None
    assert partial['choices'][0]['message']['content']==''
    assert partial['usage']['completion_tokens'] is None
