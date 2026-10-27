"""Web-only owner composition. No source patch, dispatch, model or production IO."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID
import pytest
from bestseller.services import web_pipeline_owner as module
from bestseller.services.progress_context import _CANCELLATION_CHECKER,TaskCancelledError,check_cancellation
from bestseller.worker import tasks

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]


class Redis:
    def __init__(self,store=None):self.store={} if store is None else store;self.closed=False;self.fail=None;self.events=[]
    async def set(self,key,value,nx=False,ex=None):
        if self.fail=='set':raise ConnectionError('synthetic')
        if nx and key in self.store:return False
        self.store[key]=value;return True
    async def eval(self,script,numkeys,key,owner,*args):
        self.events.append('owner_check')
        if self.fail=='eval':raise ConnectionError('synthetic')
        if self.store.get(key)!=owner:return 0
        if "'DEL'" in script:self.store.pop(key,None)
        return 1
    async def aclose(self,**kw):self.closed=True;assert kw=={'close_connection_pool':True}


@pytest.fixture
def rig(monkeypatch):
    client=Redis();factory=Mock(return_value=client)
    monkeypatch.setattr(module.Redis,'from_url',factory)
    settings=SimpleNamespace(redis=SimpleNamespace(url='redis://isolated-test:12345/0',socket_timeout_seconds=1,
        socket_connect_timeout_seconds=1,pool_max_connections=2))
    return settings,client,factory


async def test_composes_prior_checker_and_restores_token_on_normal_exit(rig):
    settings,client,factory=rig;calls=[]
    async def prior():calls.append('operator')
    binding=_CANCELLATION_CHECKER.set(prior)
    try:
        async with module.web_pipeline_owner(settings,'web-test'):
            token=client.store[tasks._pipeline_live_key('web-test')]
            assert UUID(token).version==4
            client.events.clear();await check_cancellation()
            assert calls==['operator'] and client.events==['owner_check']
        assert _CANCELLATION_CHECKER.get() is prior
        assert client.closed and not client.store
        factory.assert_called_once()
    finally:_CANCELLATION_CHECKER.reset(binding)


@pytest.mark.parametrize('state',['held','unknown','client_creation'])
async def test_unknown_or_other_owner_never_enters_body(rig,state):
    settings,client,factory=rig
    if state=='held':client.store[tasks._pipeline_live_key('web-test')]='successor'
    elif state=='unknown':client.fail='set'
    else:factory.side_effect=ConnectionError('synthetic')
    with pytest.raises(module.WebPipelineOwnerUnavailable):
        async with module.web_pipeline_owner(settings,'web-test'):
            raise AssertionError('unowned Web body entered')
    if state=='held':assert client.store[tasks._pipeline_live_key('web-test')]=='successor'
    if state!='client_creation':assert client.closed


@pytest.mark.parametrize('fault',['replacement','unknown'])
async def test_actual_paid_boundary_keeps_prior_check_and_refuses_lost_owner(rig,fault):
    from bestseller.services import llm
    settings,client,_=rig;calls=[]
    async def prior():calls.append('operator')
    binding=_CANCELLATION_CHECKER.set(prior)
    try:
        with pytest.raises(TaskCancelledError):
            async with module.web_pipeline_owner(settings,'web-test'):
                if fault=='replacement':client.store[tasks._pipeline_live_key('web-test')]='successor'
                else:client.fail='eval'
                await llm._execution_boundary()  # Native no-send boundary, no provider call.
                raise AssertionError('old Web owner passed paid admission')
        assert calls==['operator'] and _CANCELLATION_CHECKER.get() is prior and client.closed
        if fault=='replacement':assert client.store[tasks._pipeline_live_key('web-test')]=='successor'
    finally:_CANCELLATION_CHECKER.reset(binding)


async def test_original_operator_cancellation_is_not_replaced(rig):
    settings,client,_=rig;original=TaskCancelledError('actual operator cancellation')
    async def prior():raise original
    binding=_CANCELLATION_CHECKER.set(prior)
    try:
        with pytest.raises(TaskCancelledError) as caught:
            async with module.web_pipeline_owner(settings,'web-test'):await check_cancellation()
        assert caught.value is original
        assert _CANCELLATION_CHECKER.get() is prior and client.closed
    finally:_CANCELLATION_CHECKER.reset(binding)


async def test_body_failure_resets_original_checker_and_releases_only_self(rig):
    settings,client,_=rig;prior=_CANCELLATION_CHECKER.get()
    with pytest.raises(ValueError,match='body failure'):
        async with module.web_pipeline_owner(settings,'web-test'):raise ValueError('body failure')
    assert _CANCELLATION_CHECKER.get() is prior and client.closed and not client.store


async def test_each_invocation_constructs_a_new_client_and_uuid(rig):
    settings,_,factory=rig;clients=[Redis(),Redis()];factory.side_effect=clients;tokens=[]
    for c in clients:
        async with module.web_pipeline_owner(settings,'web-test'):tokens.append(c.store[tasks._pipeline_live_key('web-test')])
    assert tokens[0]!=tokens[1] and all(c.closed for c in clients)


async def test_guard_background_renewal_cancels_old_web_executor(rig,monkeypatch):
    settings,client,_=rig;monkeypatch.setattr(tasks,'_PIPELINE_LIVE_RENEW_SECONDS',.001)
    stopped=asyncio.Event()
    async def body():
        try:
            async with module.web_pipeline_owner(settings,'web-test'):
                client.store[tasks._pipeline_live_key('web-test')]='successor'
                await asyncio.Event().wait()
        except TaskCancelledError:stopped.set()
    task=asyncio.create_task(body());await asyncio.wait_for(stopped.wait(),1);await task
    assert client.store[tasks._pipeline_live_key('web-test')]=='successor' and client.closed
