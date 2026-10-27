"""Synthetic SQLite ledger + actual complete_text + intercepted HTTPX only.

No production approval, provider connection, or real review opinion is used.
"""
import asyncio
from copy import deepcopy
import json
import httpx
import pytest
from sqlalchemy import select,func
from test_planning_draft_admission import harness as harness  # noqa:F401
from test_volume_source_admission import volume as volume,SCOPE  # noqa:F401
from bestseller.services import llm,outline_llm_judge as judge
from bestseller.services.bounded_outline_review import ReviewSendCap,review_candidate_with_send_cap
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.budget import ExecutionBudgetError,ExecutionLedger
from bestseller.services.single_dispatch_authorization import bind_grant,authorization_id
from bestseller.infra.db.models import ExecutionBudgetModel,ExecutionAttemptModel

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]


def primary(blocked=False):
 return {'pass':not blocked,'overall_score':0.90,'dimension_scores':{},'blocking_issues':[
  {'code':'SYNTHETIC_CONFLICT','severity':'critical','evidence':'Fixture only','required_fix':'Fixture only'}] if blocked else []}


@pytest.fixture
async def bounded(volume,monkeypatch,tmp_path):
 h=volume;h.wires=[];h.phases=[];h.blocked=False;h.failure=None;h.fail_stage='initial';h.requests=[]
 monkeypatch.setattr(judge,'resolve_commercial_judge_model_key',lambda settings:None)
 h.settings.artifact_store.local_dir=str(tmp_path/'retained_fixture_payloads')
 h.settings.llm.critic.rate_limit_fallback_model='openai/forbidden-fallback'
 h.plan={'target_id':str(h.workflow.id),'plan_sha256':'synthetic-plan','candidate_sha256':content_hash(h.candidate),'automatic_attempts_sha256':'old-two-attempts-unchanged'}
 async def authorize(_):pass  # Fixture approval; public missing-approval test below does not bypass it.
 h.cap=lambda:ReviewSendCap(project=h.project,workflow_id=h.workflow.id,scope=SCOPE,user_message_id='fixture-human',plan=h.plan,authorize=authorize)
 async def transport(req,role):
  phase='consistency' if 'consistency' in req.prompt_template else 'initial';h.phases.append(phase);h.requests.append(req)
  async def handler(request):
   h.wires.append((phase,request.url.host))
   return httpx.Response(429 if h.failure=='429' and phase==h.fail_stage else 200,json={'fixture':True})
  async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
   if h.failure=='before_send' and phase==h.fail_stage:raise RuntimeError('fixture local error')
   if h.failure=='sdk_parallel' and phase==h.fail_stage:
    await asyncio.gather(*(client.post('https://api.minimaxi.com/v1/chat/completions',json={'fixture':True}) for _ in range(2)))
   else:
    await client.post('https://api.minimaxi.com/v1/chat/completions',json={'fixture':True})
   if h.failure and phase==h.fail_stage:
    if h.failure=='429':
     error=RuntimeError('fixture 429');error.status_code=429;raise error
    if h.failure=='timeout':raise TimeoutError('fixture unknown after send')
    if h.failure=='generic':raise RuntimeError('fixture transport error')
    if h.failure=='sdk_retry':await client.post('https://api.minimaxi.com/v1/chat/completions',json={'fixture':True})
  if h.failure=='bad_json' and phase==h.fail_stage:raw='not valid JSON'
  elif h.failure=='duplicate_json' and phase==h.fail_stage:raw='{"pass":true,"pass":false}'
  elif h.failure=='oversized' and phase==h.fail_stage:raw=json.dumps({**primary(),'ignored':'x'*17000})
  elif phase=='initial':raw=json.dumps(primary(h.blocked))
  else:raw=json.dumps({'adjudication_complete':True,'decisions':[{'index':1,'decision':'CONFIRM','reason':'fixture','required_fix':'fixture'}]})
  return raw,10,5,'stop',None,None
 monkeypatch.setattr(llm,'_call_litellm',transport);llm._llm_breaker.record_success()
 async def run(cap=None):
  cap=cap or h.cap()
  async with h.session_factory() as s:
   result=await judge.judge_outline_commercial_readiness(h.wrap(s),h.settings,outline_payload=h.candidate,
    project_brief={'language':'zh-CN'},workflow_run_id=h.workflow.id,project_id=h.project.id,completion_dispatch=cap)
   if cap.failure:raise cap.failure
   await s.commit()
   return result
 h.run=run
 return h


async def test_no_blocker_one_physical_send_and_reentry_zero_new_sends(bounded):
 h=bounded;before=h.settings.model_dump(mode='json',warnings=False);result=await h.run();assert result.passed
 assert h.wires==[('initial','api.minimaxi.com')]
 await h.run();assert len(h.wires)==1
 assert h.settings.model_dump(mode='json',warnings=False)==before


async def test_only_real_initial_blockers_enable_one_secondary_max_two(bounded):
 h=bounded;h.blocked=True;result=await h.run();assert not result.passed
 assert [p for p,_ in h.wires]==['initial','consistency']
 await h.run();assert len(h.wires)==2
 async with h.session_factory() as s:
  b=await s.get(ExecutionBudgetModel,SCOPE)
  grants=b.metadata_json['single_dispatch_grants'];assert len(grants)==2
  assert all(g['status']=='spent' for g in grants.values())
  assert await s.scalar(select(func.count()).select_from(ExecutionAttemptModel))==2


@pytest.mark.parametrize('stage',['initial','consistency'])
@pytest.mark.parametrize('failure',['429','timeout','generic','before_send','sdk_retry','bad_json','duplicate_json','oversized'])
async def test_failure_never_creates_protocol_retry_fallback_or_recovery_send(bounded,stage,failure):
 h=bounded;h.blocked=True;h.fail_stage=stage;h.failure=failure
 with pytest.raises((ValueError,RuntimeError,ExecutionBudgetError)):await h.run()
 count=len(h.wires)
 assert count <= (1 if stage=='initial' else 2)
 assert h.phases.count(stage)==1
 # New process/cap, same durable grant; no new entitlement even for not-sent.
 with pytest.raises((ValueError,RuntimeError,ExecutionBudgetError)):await h.run()
 assert len(h.wires)==count


async def test_second_sdk_send_is_blocked_before_http_handler(bounded):
 h=bounded;h.failure='sdk_retry'
 with pytest.raises(ExecutionBudgetError):await h.run()
 assert len(h.wires)==1


async def test_changed_input_cannot_rebind_same_stage_grant(bounded):
 h=bounded;await h.run();h.candidate['volumes'][0]['other_fixture_field']='different'
 with pytest.raises(ExecutionBudgetError,match='binding_changed'):await h.run()
 assert len(h.wires)==1


async def test_concurrent_reentry_cannot_send_twice(bounded,monkeypatch):
 h=bounded;entered=asyncio.Event();release=asyncio.Event();original=llm._call_litellm
 async def held(req,role):
  entered.set();await release.wait();return await original(req,role)
 monkeypatch.setattr(llm,'_call_litellm',held)
 left=asyncio.create_task(h.run());await entered.wait()
 try:
  with pytest.raises(ExecutionBudgetError):await h.run()
 finally:release.set()
 await left
 assert len(h.wires)==1


async def test_prior_spent_and_legacy_attempts_not_reset(bounded):
 h=bounded;l=ExecutionLedger(h.session_factory.kw['bind'],SCOPE,h.settings.budget)
 claim=await l.claim('synthetic-old-operation','old-input')
 oldbinding={'scope_id':SCOPE,'operation_key':claim.key,'input_hash':'old-input','model':'old-model',
  'endpoint':'https://example.invalid/v1','workflow_id':'old-wf','user_message_id':'old-message',
  'prior_workflow_id':'old-prior','prior_journal_sha256':'two-legacy-attempts','template':'old-template',
  'version_prefix':'old-version','authority_evidence':{'fixture':True}}
 gid=authorization_id('old-message','old-prior')
 async with l.transaction() as s:
  row=await l._lock(s);meta=bind_grant(row.metadata_json,grant_id=gid,binding=oldbinding)
  meta['single_dispatch_grants'][gid].update(status='spent',used_attempt_id='historical-only')
  row.metadata_json=meta;old=deepcopy(meta['single_dispatch_grants'][gid])
 before=deepcopy(h.plan);await h.run()
 async with h.session_factory() as s:assert (await s.get(ExecutionBudgetModel,SCOPE)).metadata_json['single_dispatch_grants'][gid]==old
 assert h.plan==before


async def test_public_runner_without_approval_cannot_register_grant_or_send(bounded):
 h=bounded;plan={**h.plan,'project_id':str(h.project.id),'scope':SCOPE,'policy':{},'parent_sha256':'old','foundation':{}}
 plan['plan_sha256']=content_hash({k:v for k,v in plan.items() if k!='plan_sha256'})
 async with h.session_factory() as s:
  with pytest.raises(ValueError,match='approval_missing'):
   await review_candidate_with_send_cap(h.wrap(s),h.settings,project=h.project,workflow_id=h.workflow.id,
     scope=SCOPE,plan=plan,approval_id=None,user_message_id='fixture-human')
  assert await s.get(ExecutionBudgetModel,SCOPE) is None
 assert not h.wires


async def test_parallel_sdk_http_sends_share_one_physical_slot(bounded):
 h=bounded;h.failure='sdk_parallel'
 with pytest.raises(ExecutionBudgetError):await h.run()
 assert len(h.wires)==1


@pytest.mark.parametrize('route',['dropped_id','changed_version','changed_template','changed_workflow'])
async def test_bound_case_cannot_escape_by_dropping_id_or_rerouting(bounded,route):
 from uuid import uuid4
 from bestseller.services.budget import bind_execution_scope
 h=bounded;await h.run();req=h.requests[0].model_copy(deep=True)
 req.metadata.pop('single_dispatch_authorization_id');req.operation_id='synthetic-bypass'
 if route=='changed_version':req.prompt_version='other-version'
 if route=='changed_template':req.prompt_template='other-template'
 if route=='changed_workflow':req.workflow_run_id=uuid4()
 async with h.session_factory() as s:
  with bind_execution_scope(SCOPE):
   with pytest.raises(ExecutionBudgetError):await llm.complete_text(h.wrap(s),h.settings,req)
 assert len(h.wires)==1


async def test_unrelated_next_candidate_is_not_locked_by_spent_review_grants(bounded):
 from uuid import uuid4
 from bestseller.services.budget import bind_execution_scope
 h=bounded;h.blocked=True;await h.run();req=h.requests[0].model_copy(deep=True)
 req.metadata.pop('single_dispatch_authorization_id');req.operation_id='new-normal-business'
 req.workflow_run_id=uuid4();req.user_prompt+='\nA different next chapter and independently authorized normal business.'
 async with h.session_factory() as s:
  with bind_execution_scope(SCOPE):await llm.complete_text(h.wrap(s),h.settings,req)
 assert len(h.wires)==3
 async with h.session_factory() as s:
  grants=(await s.get(ExecutionBudgetModel,SCOPE)).metadata_json['single_dispatch_grants']
  assert len(grants)==2 and all(g['status']=='spent' for g in grants.values())


async def test_public_entry_cannot_attribute_review_to_another_workflow(bounded):
 from uuid import uuid4
 h=bounded;plan={**h.plan,'project_id':str(h.project.id),'scope':SCOPE}
 plan['plan_sha256']=content_hash({k:v for k,v in plan.items() if k!='plan_sha256'})
 async with h.session_factory() as s:
  with pytest.raises(ValueError,match='workflow_target_changed'):
   await review_candidate_with_send_cap(h.wrap(s),h.settings,project=h.project,workflow_id=uuid4(),
    scope=SCOPE,plan=plan,approval_id=None,user_message_id='fixture-human')
 assert not h.wires


async def test_concurrent_secondary_uses_only_one_second_send(bounded,monkeypatch):
 h=bounded;h.blocked=True;entered=asyncio.Event();release=asyncio.Event();original=llm._call_litellm
 async def held(req,role):
  if 'consistency' in req.prompt_template:entered.set();await release.wait()
  return await original(req,role)
 monkeypatch.setattr(llm,'_call_litellm',held)
 left=asyncio.create_task(h.run());await entered.wait()
 try:
  with pytest.raises(ExecutionBudgetError):await h.run()
 finally:release.set()
 await left
 assert [x[0] for x in h.wires]==['initial','consistency']


async def test_fallback_constructor_is_never_entered_even_after_429(bounded,monkeypatch):
 h=bounded;h.failure='429'
 def forbidden(*args):pytest.fail('fallback route construction entered')
 monkeypatch.setattr(llm,'_build_rate_limit_fallback_settings',forbidden)
 with pytest.raises(ExecutionBudgetError):await h.run()
 assert len(h.wires)==1


async def test_unknown_initial_cannot_start_secondary_on_same_cap(bounded):
 h=bounded;h.failure='timeout';cap=h.cap()
 with pytest.raises(ExecutionBudgetError):await h.run(cap)
 req=h.requests[0].model_copy(deep=True);req.prompt_template='outline_commercial_judge_consistency_adjudication'
 async with h.session_factory() as s:
  with pytest.raises(ExecutionBudgetError):await cap(h.wrap(s),h.settings,req)
 assert len(h.wires)==1


async def test_capacity_failure_precedes_grant_registration_and_send(bounded):
 h=bounded
 from bestseller.services.llm import LLMCompletionRequest
 from bestseller.services.source_bound_planning_content import SourceBoundPlanningCapacityError
 req=LLMCompletionRequest(logical_role='critic',system_prompt='fixture',user_prompt='界'*120001,
  fallback_response='{}',prompt_template='outline_commercial_judge',prompt_version='v2-source-authority',
  project_id=h.project.id,workflow_run_id=h.workflow.id,max_tokens_override=8192)
 async with h.session_factory() as s:
  with pytest.raises(SourceBoundPlanningCapacityError):await h.cap()(h.wrap(s),h.settings,req)
  assert await s.get(ExecutionBudgetModel,SCOPE) is None
 assert not h.wires
