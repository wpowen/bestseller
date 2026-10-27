"""Only explicit immutable historical source evidence may pin its old route."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4
import pytest
from bestseller.services import llm,retained_review_input_proof as proof
from bestseller.services.model_catalog import ModelCatalogEntry
from bestseller.settings import load_settings

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

@pytest.fixture(autouse=True)
def clear_routes():
 token=proof._HISTORICAL_ROUTE_WITNESSES.set(None)
 yield
 proof._HISTORICAL_ROUTE_WITNESSES.reset(token)

def fixture(monkeypatch):
 monkeypatch.setattr('bestseller.services.model_catalog._resolve_availability',lambda e:(True,None))
 st=load_settings(env={});st.llm.mock=False
 # Use the actual effective runtime profile, not a guessed old role.
 pid=uuid4();project=SimpleNamespace(id=pid,metadata_json={'llm_model_id':'new'})
 catalog={key:ModelCatalogEntry(id=key,display_name=key,vendor='MiniMax',model=model,api_base='https://api.minimaxi.com/v1') for key,model in [('old','openai/MiniMax-M3'),('new','openai/MiniMax-M3.1-Flash')]}
 request=llm.LLMCompletionRequest(logical_role='critic',project_id=pid,workflow_run_id=uuid4(),system_prompt='历史来源审查。',user_prompt='完整旧正文与所有原始来源。',fallback_response='unused',prompt_template='draft_continuity_hard_safety',prompt_version='3.0',max_tokens_override=6500)
 oldproject=SimpleNamespace(id=pid,metadata_json={'llm_model_id':'old'})
 role=proof._fresh_role(st,oldproject,request,catalog);digest=llm._effective_input_hash(request,role)
 run=SimpleNamespace(id=uuid4(),project_id=pid,model_name=role.model,prompt_hash=digest,metadata_json={'effective_input_receipt':{'schema':'effective-input.v1','model':role.model,'hash':digest}})
 return st,project,catalog,request,run

async def test_exact_old_review_keeps_old_model_and_new_request_uses_new_model(monkeypatch):
 st,project,catalog,request,run=fixture(monkeypatch)
 assert proof._fresh_role(st,project,request,catalog).model=='openai/MiniMax-M3.1-Flash'
 proof._remember_historical_route(request,run)
 restored=proof._fresh_role(st,project,request,catalog)
 assert restored.model==run.model_name and llm._effective_input_hash(request,restored)==run.prompt_hash
 # New workflow/explicit new selection cannot inherit the historical route.
 for changes in [{'workflow_run_id':uuid4()},{'user_prompt':'来源已发生变更。'},{'model_catalog_key':'new'}]:
  fresh=request.model_copy(update=changes)
  assert proof._fresh_role(st,project,fresh,catalog).model=='openai/MiniMax-M3.1-Flash'
  assert llm._effective_input_hash(fresh,proof._fresh_role(st,project,fresh,catalog))!=run.prompt_hash

@pytest.mark.parametrize('bad',['hash','model','schema','commercial','new_source_protocol'])
async def test_bad_or_nonhistorical_evidence_does_not_pin_route(monkeypatch,bad):
 st,project,catalog,request,run=fixture(monkeypatch)
 if bad in {'hash','model','schema'}:run.metadata_json['effective_input_receipt'][bad]='invalid'
 if bad=='commercial':request=request.model_copy(update={'prompt_template':'chapter_review'})
 if bad=='new_source_protocol':request=request.model_copy(update={'prompt_version':'6.0-applicable-positional'})
 proof._remember_historical_route(request,run)
 assert proof._fresh_role(st,project,request,catalog).model=='openai/MiniMax-M3.1-Flash'

async def test_real_config_drift_rejects_instead_of_forgiving_the_old_hash(monkeypatch):
 st,project,catalog,request,run=fixture(monkeypatch);proof._remember_historical_route(request,run)
 real=proof.apply_runtime_llm_profile
 def changed(settings):
  result=real(settings).model_copy(deep=True);result.llm.critic.temperature=.731;return result
 monkeypatch.setattr(proof,'apply_runtime_llm_profile',changed)
 with pytest.raises(ValueError,match='historical_route_unprovable'):proof._fresh_role(st,project,request,catalog)

async def test_child_task_cannot_inherit_actionable_old_route(monkeypatch):
 st,project,catalog,request,run=fixture(monkeypatch);proof._remember_historical_route(request,run)
 async def child():return proof._fresh_role(st,project,request,catalog).model
 assert await asyncio.create_task(child())=='openai/MiniMax-M3.1-Flash'
 assert proof._fresh_role(st,project,request,catalog).model==run.model_name

@pytest.mark.parametrize('version',['1.2-source-sections','1.3-dependency-scope'])
async def test_foundation_delta_old_physical_route_is_retained_only_for_exact_protocol(monkeypatch,version):
 st,project,catalog,request,run=fixture(monkeypatch)
 request=request.model_copy(update={'prompt_template':'foundation_creative_delta_review','prompt_version':version})
 oldproject=SimpleNamespace(id=project.id,metadata_json={'llm_model_id':'old'})
 oldrole=proof._fresh_role(st,oldproject,request,catalog)
 run.prompt_hash=llm._effective_input_hash(request,oldrole)
 run.metadata_json={'effective_input_receipt':{'schema':'effective-input.v1','hash':run.prompt_hash,'model':run.model_name}}
 proof._remember_historical_route(request,run)
 assert proof._fresh_role(st,project,request,catalog).model==run.model_name
 assert llm._effective_input_hash(request,proof._fresh_role(st,project,request,catalog))==run.prompt_hash
 for change in ({'prompt_version':'new-protocol'},{'user_prompt':'new source'},{'workflow_run_id':uuid4()}):
  new=request.model_copy(update=change)
  assert proof._fresh_role(st,project,new,catalog).model=='openai/MiniMax-M3.1-Flash'

async def test_original_volume_hard_admission_retains_paid_route_but_new_input_does_not(monkeypatch):
 st,project,catalog,request,run=fixture(monkeypatch)
 request=request.model_copy(update={'prompt_template':'planning_draft_admission','prompt_version':'volume-source-admission.v3'})
 oldproject=SimpleNamespace(id=project.id,metadata_json={'llm_model_id':'old'})
 run.prompt_hash=llm._effective_input_hash(request,proof._fresh_role(st,oldproject,request,catalog))
 run.metadata_json={'effective_input_receipt':{'schema':'effective-input.v1','hash':run.prompt_hash,'model':run.model_name}}
 proof._remember_historical_route(request,run)
 assert proof._fresh_role(st,project,request,catalog).model==run.model_name
 new=request.model_copy(update={'user_prompt':'a new source candidate'})
 assert proof._fresh_role(st,project,new,catalog).model=='openai/MiniMax-M3.1-Flash'

@pytest.mark.parametrize('version',['3.0','4.0-completion-supplement'])
async def test_rebuilt_expected_input_keeps_exact_history_but_omitted_business_metadata_is_not_input(monkeypatch,version):
 st,project,catalog,request,run=fixture(monkeypatch)
 request=request.model_copy(update={'prompt_version':version,'operation_id':'original-operation','metadata':{'grant_id':'original-non-input'}})
 oldp=SimpleNamespace(id=project.id,metadata_json={'llm_model_id':'old'})
 run.prompt_hash=llm._effective_input_hash(request,proof._fresh_role(st,oldp,request,catalog))
 run.metadata_json={'effective_input_receipt':{'schema':'effective-input.v1','hash':run.prompt_hash,'model':run.model_name}}
 proof._remember_historical_route(request,run)
 expected=request.model_copy(update={'operation_id':None,'metadata':{},'fallback_response':'never dispatched proof'})
 assert proof._fresh_role(st,project,expected,catalog).model==run.model_name
 assert llm._effective_input_hash(expected,proof._fresh_role(st,project,expected,catalog))==run.prompt_hash
 changed=expected.model_copy(update={'user_prompt':expected.user_prompt+' source drift'})
 assert proof._fresh_role(st,project,changed,catalog).model=='openai/MiniMax-M3.1-Flash'
