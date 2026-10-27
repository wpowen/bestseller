"""Actual model/route ceiling, no supplier and no financial regrant."""
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace
import pytest
from bestseller.services import model_context_capacity as c
from bestseller.services.finite_book_budget import current_capacity,bind_run
from bestseller.services.llm_capacity import assert_effective_request_capacity,ModelRequestCapacityError
pytestmark=pytest.mark.unit

def request(model='openai/MiniMax-M3.1-Flash',endpoint='https://api.minimaxi.com/v1',chars=160000):
 return {'model':model,'endpoint_hash':sha256(endpoint.encode()).hexdigest(),'messages':[{'role':'user','content':'中'*chars}],
         'max_tokens':12288,'n_candidates':1,'tools':None}

@pytest.mark.parametrize('model,endpoint,expected',[
 ('openai/MiniMax-M3.1-Flash','https://api.minimaxi.com/v1',1000000),
 ('MiniMax-M3.1-Flash','https://api.minimax.cn/v1',1000000),
 ('openai/MiniMax-M3.1-Flash','https://api.minimax.io/v1/',1000000),
 ('openai/MiniMax-M3.1-Flash','https://api.minimaxi.com.evil.example/v1',65536),
 ('openai/MiniMax-M3.1-Flash','http://api.minimaxi.com/v1',65536),
 ('openai/MiniMax-M3.1-Flash','https://api.minimaxi.com/other',65536),
 ('openai/MiniMax-M3.1-Flash','https://api.minimaxi.com/v1?other=1',65536),
 ('other-model','https://api.minimaxi.com/v1',65536),
 ('openai/MiniMax-M3','https://api.minimaxi.com/v1',65536),
])
def test_known_model_and_exact_official_route_only(model,endpoint,expected):
 assert c.documented_capacity(model,endpoint=endpoint)==expected
 assert c.payload_capacity(request(model,endpoint))==expected


def test_native_schema_output_and_entire_input_still_count_and_model_limit_is_enforced():
 payload=request(chars=50000);before=deepcopy(payload)
 receipt=assert_effective_request_capacity(payload)
 assert receipt['total_budget']==1000000 and receipt['budget_passed'] and receipt['reserved_total_tokens']>65536
 assert receipt['truncated'] is False and payload==before
 with pytest.raises(ModelRequestCapacityError):assert_effective_request_capacity(request(chars=700000))
 # A selected planning context cannot grant another actual provider that ceiling.
 with c.bind_model_capacity('MiniMax-M3.1-Flash','https://api.minimaxi.com/v1'):
  assert current_capacity()==1000000
  with pytest.raises(ModelRequestCapacityError):assert_effective_request_capacity(request('other-model',chars=50000))
 assert current_capacity()==65536


def test_explicit_finite_binding_remains_smaller_and_is_never_rewritten():
 with bind_run('offline-run',project_id='offline-project',stage='plan',capacity_limit=65536):
  with c.bind_model_capacity('MiniMax-M3.1-Flash','https://api.minimaxi.com/v1'):
   assert current_capacity()==65536
   with pytest.raises(ModelRequestCapacityError):assert_effective_request_capacity(request(chars=50000))
 with bind_run('offline-run',project_id='offline-project',stage='plan',capacity_limit=900000):
  assert current_capacity()==900000
  assert assert_effective_request_capacity(request(chars=50000))['total_budget']==900000
 assert current_capacity()==65536

@pytest.mark.asyncio
async def test_actual_project_selection_is_bound_only_for_duration_of_planning(monkeypatch):
 from bestseller.services import model_catalog
 entry=SimpleNamespace(model='openai/MiniMax-M3.1-Flash',api_base='https://api.minimaxi.com/v1')
 monkeypatch.setattr(model_catalog,'resolve_project_model_entry',lambda meta:entry if meta.get('llm_model_id')=='selected' else None)
 @c.planning_model_capacity
 async def plan(session,settings,*,project):return current_capacity()
 assert await plan(None,None,project=SimpleNamespace(metadata_json={'llm_model_id':'selected'}))==1000000
 assert await plan(None,None,project=SimpleNamespace(metadata_json={}))==65536
 assert current_capacity()==65536
