"""Real complete_text/SQLite boundary, simulated provider failures only."""
import hashlib
import json
import pytest
import httpx
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from test_volume_source_admission import volume as volume,grade  # noqa:F401
from test_planning_draft_admission import harness as harness  # noqa:F401
from bestseller.services import planning_draft_admission as a,llm
from bestseller.services.budget import ExecutionLedger,bind_execution_scope,ExecutionBudgetError
from bestseller.services.single_dispatch_authorization import authorization_id,register_grant,no_retry_settings
from bestseller.infra.db.models import ExecutionBudgetModel, ProjectModel
from test_volume_source_admission import SCOPE

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

@pytest.fixture(autouse=True)
async def project_schema(volume):
 # Extend this file's volume fixture without changing the shared harness.
 table=ProjectModel.__table__.to_metadata(MetaData())
 for column in table.columns:
  if isinstance(column.type,JSONB):
   column.type=JSON()
   column.server_default=None
 async with volume.session_factory.begin() as session:
  connection=await session.connection()
  await connection.run_sync(table.create)

@pytest.mark.parametrize('outcome',['bad_format','semantic_reject','429','timeout','sdk_resend'])
async def test_every_failure_stops_after_one_physical_attempt(volume,monkeypatch,outcome):
 h=volume;calls=[];wire=[];original=a.complete_text;gid=authorization_id('one-human-answer','old-exhausted')
 settings=no_retry_settings(h.settings);h.settings=settings
 if outcome=='semantic_reject':
  h.candidate['volumes'][0]['information_flow_scene_cards'][0]['actual_action']='Mira fires without touching the tool.'
  h.raw=json.dumps(grade(h.candidate,{'code':'ABILITY_RULE_CONFLICT','path':'volumes[0].information_flow_scene_cards[0]',
    'evidence':'Mira fires without touching the tool.','source_requirement':'Touching the tool is required',
    'source_refs':['/approved_world_spec/requirement']}))
 if outcome=='semantic_reject':
  raw=json.loads(h.raw)
  for check in raw['projection_checks']:
   if check['status']=='conflict':check['related_violation_paths']=[check['path']]
  h.raw=json.dumps(raw)
 async def gateway(session,st,req):
  req.metadata={**req.metadata,'single_dispatch_authorization_id':gid}
  digest=llm._effective_input_hash(req,st.llm.critic);key=hashlib.sha256((SCOPE+'\n'+req.operation_id).encode()).hexdigest()
  b={'scope_id':SCOPE,'operation_key':key,'input_hash':digest,'model':st.llm.critic.model,'endpoint':st.llm.critic.api_base,
   'workflow_id':str(h.workflow.id),'user_message_id':'one-human-answer','prior_workflow_id':'old-exhausted',
   'prior_journal_sha256':'unchanged','template':req.prompt_template,'version_prefix':'volume-source-admission.',
   'authority_evidence':{'limit':1}}
  await register_grant(ExecutionLedger(session.bind,SCOPE,st.budget),grant_id=gid,binding=b)
  return await original(session,st,req)
 async def transport(req,role):
  calls.append(req)
  if outcome=='429':
   error=RuntimeError('rate limit');error.status_code=429;raise error
  if outcome=='timeout':
   await llm._guard_provider_http_send(httpx.Request('POST','https://api.minimaxi.com/v1/chat/completions',content=b'fixture'))
   raise TimeoutError('unknown remote outcome')
  if outcome=='sdk_resend':
   async def http_handler(request):wire.append(request);return httpx.Response(200,json={'fixture':True})
   async with httpx.AsyncClient(transport=httpx.MockTransport(http_handler)) as client:
    await client.post('https://api.minimaxi.com/v1/chat/completions',json={'fixture':True})
    await client.post('https://api.minimaxi.com/v1/chat/completions',json={'fixture':True})
   pytest.fail('SDK resend guard did not fire')
  raw='{}' if outcome=='bad_format' else h.raw
  tools=[{'id':'native-fixture','type':'function','function':{'name':'submit_planning_admission','arguments':raw}}]
  return '',10,5,'tool_calls',tools,{'role':'assistant','content':None,'tool_calls':tools}
 monkeypatch.setattr(a,'complete_text',gateway);monkeypatch.setattr(llm,'_call_litellm',transport)
 llm._llm_breaker.record_success()
 async with h.session_factory() as session:
  with bind_execution_scope(SCOPE):
   if outcome=='semantic_reject':
    receipt=await a.assess_planning_draft_admission(h.wrap(session),settings,**h.volume_kwargs(),commercial_report=h.commercial)
    assert receipt['draft_eligible'] is False
   else:
    with pytest.raises((a.PlanningDraftAdmissionProtocolError,ExecutionBudgetError)):
     await a.assess_planning_draft_admission(h.wrap(session),settings,**h.volume_kwargs(),commercial_report=h.commercial)
 assert len(calls)==1
 if outcome=='sdk_resend':assert len(wire)==1
 async with h.session_factory() as session:
  row=await session.get(ExecutionBudgetModel,SCOPE)
  assert row.calls_used==1 and row.metadata_json['single_dispatch_grants'][gid]['status']=='spent'
