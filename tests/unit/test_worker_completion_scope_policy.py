from copy import deepcopy
from datetime import datetime,UTC,timedelta
from types import SimpleNamespace
import pytest
from bestseller.services.budget import execution_scope_block_code,ExecutionBudgetError
from bestseller.services.retry_ledger import assert_execution_scope_open

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

@pytest.mark.parametrize('status,policy_status,expected',[
 ('active','active',None),('exhausted','active',None),
 ('unknown','active','execution_result_unknown'),('cancelled','active','execution_cancelled'),
 ('active','paused','execution_authorization_paused'),('active','completed','execution_authorization_closed'),
 ('active',None,'execution_deadline_exhausted'),('exhausted',None,'execution_deadline_exhausted'),
])
async def test_worker_and_gateway_use_the_same_authoritative_scope_policy(status,policy_status,expected):
    policy={'mode':'completion_bound','authorization_ref':'explicit-user-authorization','objective':'continue chapters',
        'models':['openai/MiniMax-M3'],'endpoint_hosts':['api.minimaxi.com'],'status':policy_status}
    row=SimpleNamespace(status=status,deadline_at=datetime.now(UTC)-timedelta(days=2),
        calls_used=266,tokens_charged=3166886,max_calls=139,max_tokens=1128666,
        metadata_json={'provider_authorization':policy} if policy_status else {})
    before=deepcopy(row.__dict__)
    class Session:
        async def get(self,*args):return row
    assert execution_scope_block_code(row)==expected
    if expected:
        with pytest.raises(ExecutionBudgetError,match=expected):await assert_execution_scope_open(Session(),'original-scope')
    else:await assert_execution_scope_open(Session(),'original-scope')
    assert row.__dict__==before
