"""Real SQLite paid-journal proofs, fixture transport/source boundary; no model."""
from copy import deepcopy
import json
from uuid import UUID
import pytest
from test_planning_draft_admission import harness as harness  # noqa:F401
from bestseller.infra.db.models import LlmRunModel,ExecutionBudgetModel
from bestseller.services import planning_draft_admission as a,llm,volume_source_admission_input as source_input
from bestseller.services.budget import bind_execution_scope
from bestseller.services.planning_admission_submission import planning_admission_tool

pytestmark=pytest.mark.unit
SCOPE='audit:admission-fixture'

def candidate():
 return {'volumes':[{'volume_number':1,'volume_goal':'Deliver the harbor logbook.',
  'information_flow_scene_cards':[{'chapter':8,'location':'harbor office','actual_action':'Mira touches the tool and sends an ordinary inventory.'}],
  'unknown_authored_clause':{'before':'The tool cannot fire without touch.'}}]}

def grade(c, violation=None):
 targets=a.projection_audit_targets(c)
 return {'draft_eligible':violation is None,'hard_violations':[] if violation is None else [violation],
  'editorial_notes':['Original speculative review remains in history.'],
  'projection_checks':[{**t,'status':'conflict' if violation and violation['path']==t['path'] else 'consistent',
   'explanation':'Complete projection checked against current sources.'} for t in targets]}

@pytest.fixture
async def volume(harness,monkeypatch):
 h=harness;h.candidate=candidate();h.raw=json.dumps(grade(h.candidate));h.requests=[]
 async def inputs(session,**kw):
  assert kw['settings'] is h.settings
  for name in ('book_spec','world_spec','cast_spec'):
   assert kw[name]==getattr(h,name)
  supplied={'schema':a.VOLUME_SCHEMA,'hard_admission_policy':a.VOLUME_HARD_ADMISSION_POLICY,
   'project_id':str(h.project.id),'workflow_run_id':str(h.workflow.id),
   'candidate_payload':deepcopy(kw['candidate_payload']),
   'projection_audit_targets':a.projection_audit_targets(kw['candidate_payload']),
   'temporal_positions':a.temporal_positions(kw['candidate_payload']),
   'foundation':{'source_component_receipt_hash':'fixture-read-boundary'},
   'approved_book_spec':deepcopy(h.book_spec),'approved_world_spec':deepcopy(h.world_spec),
   'approved_cast_spec':deepcopy(h.cast_spec),'approved_creation_contracts':{},
   'untrusted_commercial_report':deepcopy(kw['commercial_report']),
   'untrusted_legacy_design_context':{'must_count':'Every greeting creates a witness.'}}
  supplied['source_authority']=a._source_authority(supplied)
  return supplied
 async def transport(req,role):
  h.requests.append(req);assert role.model=='openai/MiniMax-M3'
  assert req.prompt_version==a.VOLUME_SCHEMA and req.max_tokens_override==5000
  calls=[{'id':'fixture-volume','type':'function','function':{'name':'submit_planning_admission','arguments':h.raw}}]
  return '',10,5,'tool_calls',calls,{'role':'assistant','content':None,'tool_calls':deepcopy(calls)}
 kw=h.kwargs()
 for name in ('book_spec','world_spec','cast_spec'):setattr(h,name,kw[name])
 h.volume_kwargs=lambda:dict(project=h.project,workflow_run_id=h.workflow.id,candidate_payload=deepcopy(h.candidate),
  book_spec=deepcopy(h.book_spec),world_spec=deepcopy(h.world_spec),cast_spec=deepcopy(h.cast_spec))
 monkeypatch.setattr(source_input,'build_volume_admission_input',inputs)
 monkeypatch.setattr(llm,'_call_litellm',transport)
 return h

@pytest.mark.asyncio
async def test_native_volume_eligibility_preserves_negative_and_real_paid_readback(volume):
 h=volume;negative=deepcopy(h.commercial)
 async with h.session_factory() as actual:
  with bind_execution_scope(SCOPE):
   r=await a.assess_planning_draft_admission(h.wrap(actual),h.settings,**h.volume_kwargs(),commercial_report=negative)
  await actual.commit()
 assert r['draft_eligible'] and r['commercial_report']==negative and negative['pass'] is False
 assert r['schema']==a.VOLUME_SCHEMA and r['hard_admission_policy']!=a.HARD_ADMISSION_POLICY
 assert len(h.requests)==1 and h.requests[0].metadata['planning_admission_input_receipt']['output_reserve_tokens']==5000
 async with h.session_factory() as actual:
  await a.verify_volume_source_admission(h.wrap(actual),settings=h.settings,**h.volume_kwargs(),receipt=r)
  with pytest.raises(a.PlanningDraftAdmissionError,match='not_chapter'):
   await a.verify_planning_draft_admission(h.wrap(actual),**h.volume_kwargs(),receipt=r)
  run=await actual.get(LlmRunModel,UUID(r['llm_run_id']));assert run.prompt_version==a.VOLUME_SCHEMA
  budget=await actual.get(ExecutionBudgetModel,SCOPE);assert budget.calls_used==1

@pytest.mark.asyncio
async def test_rollback_reuses_exact_volume_paid_journal(volume):
 h=volume
 for commit in (False,True):
  async with h.session_factory() as actual:
   with bind_execution_scope(SCOPE):
    r=await a.assess_planning_draft_admission(h.wrap(actual),h.settings,**h.volume_kwargs(),commercial_report=h.commercial)
   await actual.commit() if commit else await actual.rollback()
 assert r['draft_eligible'] and len(h.requests)==1

@pytest.mark.parametrize('bad',['candidate_quote','source_quote','advisory_ref'])
def test_volume_hard_finding_must_bind_actual_candidate_and_authority(bad):
 c=candidate();supplied={'approved_world_spec':{'rule':'Touching the tool is required.'},'approved_creation_contracts':{}}
 supplied['source_authority']=a._source_authority(supplied)
 v={'code':'ABILITY_RULE_CONFLICT','path':'volumes[0].information_flow_scene_cards[0]',
  'evidence':'Mira touches the tool','source_requirement':'Touching the tool is required.',
  'source_refs':['/approved_world_spec/rule']}
 if bad=='candidate_quote':v['evidence']='Mira reveals the forbidden seal.'
 if bad=='source_quote':v['source_requirement']='Every ordinary greeting creates a witness.'
 if bad=='advisory_ref':v['source_refs']=['/untrusted_legacy_design_context/must_count']
 with pytest.raises(a.PlanningDraftAdmissionError):a._grade(json.dumps(grade(c,v)),c,supplied)

def test_volume_actual_hard_violation_stays_rejected_and_full_unknown_fields_hashed():
 c=candidate();c['volumes'][0]['information_flow_scene_cards'][0]['actual_action']='Mira fires without touching the tool.'
 supplied={'approved_world_spec':{'rule':'Touching the tool is required.'}};supplied['source_authority']=a._source_authority(supplied)
 v={'code':'ABILITY_RULE_CONFLICT','path':'volumes[0].information_flow_scene_cards[0]',
  'evidence':'Mira fires without touching the tool.','source_requirement':'Touching the tool is required.',
  'source_refs':['/approved_world_spec/rule']}
 assert not a._grade(json.dumps(grade(c,v)),c,supplied).draft_eligible
 before=a.projection_audit_targets(c);c['volumes'][0]['unknown_authored_clause']['before']='Altered actual rule'
 assert a.projection_audit_targets(c)[0]!=before[0]

def test_native_target_kind_is_explicit_and_does_not_accept_fake_chapters():
 targets=a.projection_audit_targets(candidate())
 with pytest.raises(ValueError):planning_admission_tool(a._Grade.model_json_schema(),targets)
 tool=planning_admission_tool(a._Grade.model_json_schema(),targets,projection_kind='volumes')
 assert tool['function']['name']=='submit_planning_admission'
 assert a._candidate_path_exists(candidate(),'volumes[0].information_flow_scene_cards[0].actual_action')
 assert not a._candidate_path_exists(candidate(),'chapters[0]')
 with pytest.raises(a.PlanningDraftAdmissionError):a.projection_audit_targets({'volumes':[],'chapters':[]})
