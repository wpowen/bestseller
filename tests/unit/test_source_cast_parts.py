"""Real SQLite checkpoints with synthetic model fixtures; all network forbidden."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from sqlalchemy import select,func
from test_creation_configuration_slice import store
from bestseller.infra.db import models as m
from bestseller.services import source_cast_parts as parts,planner,llm,model_catalog
from bestseller.services.source_bound_planning_content import CONTENT_POLICY
from bestseller.services.source_bound_planning_submission import planning_submission_contract
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.model_context_capacity import bind_model_capacity
from bestseller.settings import load_settings

pytestmark=pytest.mark.unit


def source():
 return {'protagonist':{'name':'甲','goal':'保留原始记录'},'antagonist':{},'supporting_cast':[{'name':'乙'},{'name':'丙'}],'antagonist_forces':[],'conflict_map':[]}


def document(projection):
 return {'protocol':CONTENT_POLICY,'kind':'cast_spec','approved_sources':{'canonical_snapshot':{'id':'frozen-source','content_hash':'a'*64,'content':{'premise':'完整原始来源必须保留'}}},'projection':deepcopy(projection),'current_output_contract':{'kind':'cast_spec','mandatory_exact_dotted_fields':['protagonist.name','protagonist.goal'],'native_submission':planning_submission_contract('cast_spec')}}


def good(request):
 doc=json.JSONDecoder().raw_decode(request.user_prompt)[0]
 if request.prompt_template=='planner_cast_person_part':
  person=doc['current_part']['source_person'];return {'person':{'name':person['name'],'goal':person.get('goal') or '核对已知事实','gender':'male','pronoun_set_zh':'他','pronoun_set_en':'he/him','life_history':{'education':None},'social_network':{'family':[]}}}
 ids=[row['part_id'] for row in doc['already_generated_people']]
 return {'layout':{'protagonist':ids[0],'antagonist':None,'supporting_cast':ids[1:]},'antagonist_forces':[],'conflict_map':[]}


def completion(value,request,finish='stop'):
 return llm.LLMCompletionResult(content=json.dumps(value,ensure_ascii=False),provider='openai',model_name='openai/MiniMax-M3.1-Flash',input_tokens=10,output_tokens=20,finish_reason=finish,llm_run_id=uuid4(),metadata={'paid_call_origin':{'workflow_run_id':str(request.workflow_run_id)}})


def test_assembly_keeps_exact_people_types_and_rejects_names_duplicates_and_unknown_audit():
 projection=source();people=parts.roster(projection);persons={person['id']:{'name':person['name'],'goal':'保留原始记录'} for person in people};before=deepcopy((projection,persons))
 links={'layout':{'protagonist':'person:0','antagonist':'person:1','supporting_cast':['person:2']},'antagonist_forces':[],'conflict_map':[]}
 result=parts.assemble(people,persons,links)
 assert result['protagonist']==persons['person:0'] and result['antagonist']==persons['person:1'] and result['supporting_cast']==[persons['person:2']]
 assert (projection,persons)==before and '_meta' not in result
 for mutation in ('duplicate','omit','name','type','audit'):
  candidates=deepcopy(persons);graph=deepcopy(links)
  if mutation=='duplicate':graph['layout']['supporting_cast']=['person:1']
  if mutation=='omit':graph['layout']['supporting_cast']=[]
  if mutation=='name':candidates['person:0']['name']='凭空姓名'
  if mutation=='type':candidates['person:2']['life_history']={'education':['wrong type']}
  if mutation=='audit':graph['_meta']={'approved':True}
  with pytest.raises(ValueError):parts.assemble(people,candidates,graph)
 assert (projection,persons)==before


def test_source_packet_keeps_all_sources_and_rejects_wrong_kind():
 projection=source();doc=document(projection);before=deepcopy(doc)
 packet,contract=parts.source_packet(json.dumps(doc,ensure_ascii=False))
 assert packet['approved_sources']==doc['approved_sources'] and packet['projection']==projection and doc==before
 assert contract==doc['current_output_contract']
 doc['kind']='world_spec'
 with pytest.raises(ValueError,match='source_contract_mismatch'):parts.source_packet(json.dumps(doc))
 with pytest.raises(ValueError,match='source_suffix'):parts.source_packet(json.dumps(before)+'\nUNRECOGNIZED TASK')


def test_policy_is_only_complete_source_bound_formal_official_m31(monkeypatch):
 entry=SimpleNamespace(model='openai/MiniMax-M3.1-Flash',api_base='https://api.minimaxi.com/v1')
 monkeypatch.setattr(model_catalog,'resolve_project_model_entry',lambda meta:entry)
 p=SimpleNamespace(metadata_json={'production_profile':'complete'})
 assert parts.enabled(p)
 p.metadata_json['production_profile']='research';assert not parts.enabled(p)
 p.metadata_json['production_profile']='complete';entry.api_base='https://other.example/v1';assert not parts.enabled(p)
 entry.api_base='https://api.minimaxi.com/v1';entry.model='openai/MiniMax-M3';assert not parts.enabled(p)


def test_new_part_wire_is_documented_text_stream_medium_and_old_native_is_unchanged():
 settings=load_settings();role=settings.llm.planner.model_copy(update={'model':'openai/MiniMax-M3.1-Flash','reasoning_effort':'','stream':False,'thinking_type':'adaptive'})
 request=llm.LLMCompletionRequest(logical_role='planner',system_prompt='part',user_prompt='{}',fallback_response='{}',prompt_template='planner_cast_person_part',prompt_version=parts.POLICY,max_tokens_override=parts.OUTPUT_TOKENS)
 actual=llm._apply_conception_phase_settings(request,role)
 assert actual.stream is True and actual.reasoning_effort=='medium'
 with pytest.raises(ValueError,match='thinking_cannot_be_disabled'):
  llm._effective_input_payload(request,llm._apply_conception_phase_settings(request,role.model_copy(update={'thinking_type':'disabled'})))
 wire=llm._effective_input_payload(request,actual)
 assert wire['stream'] is True and wire['max_tokens']==16384 and not wire['tools']
 legacy=request.model_copy(update={'prompt_template':'planner_cast_spec','prompt_version':'v3-source-bound-kind-submit'})
 assert llm._apply_conception_phase_settings(legacy,role)==role
 explicit=role.model_copy(update={'reasoning_effort':'high'})
 assert llm._apply_conception_phase_settings(request,explicit).reasoning_effort=='high'
 with pytest.raises(ValueError,match='text_stream_required'):llm._apply_conception_phase_settings(request.model_copy(update={'tools':[{'type':'function'}]}),role)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',[None,'person_schema','global_layout'])
async def test_actual_part_generator_persists_each_success_and_repairs_only_failed_unit(store,tmp_path,monkeypatch,fault):
 async with store.sessions() as s:
  project=await s.get(m.ProjectModel,store.pid);workflow=m.WorkflowRunModel(project_id=project.id,workflow_type='generate_foundation_plan',status='running',metadata_json={});s.add(workflow);await s.commit();wid=workflow.id
  projection=source();requests=[];faulted=False
  async def complete(session,settings,request):
   nonlocal faulted
   requests.append(request.model_copy(deep=True));value=good(request)
   if not faulted and fault=='person_schema' and request.metadata['cast_part_id']=='person:1':
    value['person']['life_history']['education']=['wrong'];faulted=True
   if not faulted and fault=='global_layout' and request.metadata['cast_part_id']=='links':
    value['layout']['supporting_cast']=['person:1','person:1'];faulted=True
   return completion(value,request)
  monkeypatch.setattr(llm,'complete_text',complete)
  validator_calls=[];dispatch=AsyncMock()
  value=await parts.generate(s,store.settings,project=project,system='current source task',user=json.dumps(document(projection),ensure_ascii=False),projection=projection,workflow_run_id=wid,validate=lambda v:validator_calls.append(deepcopy(v)),before_dispatch=dispatch)
  assert len(requests)==4+(fault is not None)
  assert [q.metadata['cast_part_id'] for q in requests].count('person:0')==1
  assert [q.metadata['cast_part_id'] for q in requests].count('person:2')==1
  assert all('完整原始来源必须保留' in q.user_prompt and q.tools is None and q.prompt_template!='planner_cast_spec' for q in requests)
  if fault:
   bad_id='person:1' if fault=='person_schema' else 'links';fix=[q for q in requests if q.metadata['cast_part_id']==bad_id][1]
   assert 'schema_correction_only' in fix.user_prompt and 'rejected_part' in fix.user_prompt
  assert len(validator_calls)==1 and len(value['_meta']['generation_llm_run_ids'])==4
  meta=value['_meta'];assert meta['approved_complete'] is False and meta['composite_generation_receipt']['single_full_asset_provider_response_claimed'] is False
  assert 'generation_llm_run_id' not in meta
 async with store.sessions() as s:
  wf=await s.get(m.WorkflowRunModel,wid);assert len(wf.metadata_json['cast_part_checkpoints'])==4
  assert await s.scalar(select(func.count()).select_from(m.PlanningArtifactVersionModel))==0
 assert dispatch.await_count==len(requests)+1


@pytest.mark.asyncio
async def test_unknown_part_is_not_retried_and_known_prior_part_uses_same_identity_across_workflows(store,tmp_path,monkeypatch):
 projection=source();cache={};paid=[];all_requests=[]
 async def complete(session,settings,request):
  key=(request.prompt_template,request.prompt_version,request.system_prompt,request.user_prompt,request.max_tokens_override)
  all_requests.append((key,llm._semantic_operation_namespace.get()))
  if key not in cache:
   paid.append(request.metadata['cast_part_id'])
   cache[key]=ExecutionBudgetError('execution_result_unknown','fixture') if request.metadata['cast_part_id']=='person:1' else completion(good(request),request)
  if isinstance(cache[key],Exception):raise cache[key]
  return cache[key]
 monkeypatch.setattr(llm,'complete_text',complete)
 workflows=[]
 for repeat in range(2):
  async with store.sessions() as s:
   project=await s.get(m.ProjectModel,store.pid);wf=m.WorkflowRunModel(project_id=project.id,workflow_type='generate_foundation_plan',status='running',metadata_json={});s.add(wf);await s.commit();workflows.append(wf.id)
   with pytest.raises(ExecutionBudgetError,match='execution_result_unknown'):
    await parts.generate(s,store.settings,project=project,system='same source',user=json.dumps(document(projection),ensure_ascii=False),projection=projection,workflow_run_id=wf.id,validate=lambda _:None,before_dispatch=AsyncMock())
 assert paid==['person:0','person:1'] and len(all_requests)==4
 assert all_requests[0]==all_requests[2] and all_requests[1]==all_requests[3]
 async with store.sessions() as s:
  for wid in workflows:assert len((await s.get(m.WorkflowRunModel,wid)).metadata_json['cast_part_checkpoints'])==1
  assert await s.scalar(select(func.count()).select_from(m.PlanningArtifactVersionModel))==0


@pytest.mark.asyncio
async def test_composite_never_bypasses_existing_final_global_semantic_review(store,tmp_path,monkeypatch):
 async with store.sessions() as s:
  project=await s.get(m.ProjectModel,store.pid)
  candidate=parts.assemble(parts.roster(source()),{'person:0':{'name':'甲','goal':'保留记录'},'person:1':{'name':'乙'},'person:2':{'name':'丙'}},{'layout':{'protagonist':'person:0','antagonist':None,'supporting_cast':['person:1','person:2']},'antagonist_forces':[],'conflict_map':[]})
  candidate['_meta']={'generation_method':'assembled_real_model_parts','composite_generation_receipt':{'single_full_asset_provider_response_claimed':False,'quality_approval_granted':False},'approved_complete':False}
  monkeypatch.setattr(planner,'_source_bound_cast_enabled',lambda _:True)
  critic=AsyncMock(return_value={'decision':'revise','draft_eligible':False,'core_conflicts':['unresolved original source conflict'],'feedback':'Real final review must block this candidate.'})
  monkeypatch.setattr(planner,'_review_source_bound_expansion',critic)
  with pytest.raises(planner.PlannerFallbackError,match='semantic review blocked'):
   await planner._approve_final_source_bound_cast_artifact(s,store.settings,project=project,project_slug=project.slug,book_spec={},world_baseline={},cast_baseline=source(),world_derived={},cast_derived=candidate,workflow_run_id=uuid4(),llm_run_ids=[])
  assert critic.await_count==1 and critic.call_args.kwargs['cast_derived']==candidate
  assert await s.scalar(select(func.count()).select_from(m.PlanningArtifactVersionModel))==0


@pytest.mark.asyncio
@pytest.mark.parametrize('unknown',[False,True])
async def test_retained_bad_person_gets_only_model_field_patch_with_durable_composite_receipts(store,monkeypatch,unknown):
 from bestseller.services import source_cast_field_repair as fields
 cache={};physical=[];requests=[]
 async def complete(session,settings,request):
  identity=(request.prompt_template,request.prompt_version,request.system_prompt,request.user_prompt,request.max_tokens_override,llm._semantic_operation_namespace.get())
  requests.append(identity)
  if identity not in cache:
   physical.append(request.prompt_template)
   if request.prompt_template==fields.TEMPLATE:
    if unknown:cache[identity]=ExecutionBudgetError('execution_result_unknown','synthetic field request')
    else:
     document=json.JSONDecoder().raw_decode(request.user_prompt)[0]
     assert document['retained_part']['approved'] is False and '完整原始来源必须保留' in request.user_prompt
     assert set(document['part_contract']['required'])=={'$/person/ip_anchor/tag_memory','$/person/social_network/community/0'}
     cache[identity]=completion({'$/person/ip_anchor/tag_memory':'模型写出的记录标识','$/person/social_network/community/0':'模型写出的原有邻里关系'},request)
   else:
    value=good(request)
    if request.metadata['cast_part_id']=='person:0':
     value['person'].update(ip_anchor={'tag_memory':['wrong type']},social_network={'community':[{'name':'乙'}]})
    cache[identity]=completion(value,request)
  if isinstance(cache[identity],Exception):raise cache[identity]
  return cache[identity]
 monkeypatch.setattr(llm,'complete_text',complete)
 for repeat in range(2):
  async with store.sessions() as s:
   project=await s.get(m.ProjectModel,store.pid);wf=m.WorkflowRunModel(project_id=project.id,workflow_type='generate_foundation_plan',status='running',metadata_json={});s.add(wf);await s.commit();wid=wf.id
   kwargs=dict(project=project,system='same complete source',user=json.dumps(document(source()),ensure_ascii=False),projection=source(),workflow_run_id=wid,validate=lambda value:None,before_dispatch=AsyncMock())
   if unknown:
    with pytest.raises(ExecutionBudgetError,match='execution_result_unknown'):await parts.generate(s,store.settings,**kwargs)
    assert (await s.get(m.WorkflowRunModel,wid)).metadata_json.get('cast_part_checkpoints',{})=={}
   else:
    payload=await parts.generate(s,store.settings,**kwargs)
    assert payload['protagonist']['ip_anchor']['tag_memory']=='模型写出的记录标识'
    assert payload['protagonist']['social_network']['community']==['模型写出的原有邻里关系']
    receipts=payload['_meta']['composite_generation_receipt']['parts'];first=receipts[0]
    assert first['delivery_method']=='base_plus_model_field_corrections' and first['single_provider_payload_claimed'] is False
    correction=first['field_correction_receipt'];assert correction['quality_approval_granted'] is False
    assert len(payload['_meta']['generation_llm_run_ids'])==5 and set(first['llm_run_ids'])=={correction['base_llm_run_id'],correction['correction_llm_run_id']}
    assert len((await s.get(m.WorkflowRunModel,wid)).metadata_json['cast_part_checkpoints'])==4
   assert await s.scalar(select(func.count()).select_from(m.PlanningArtifactVersionModel))==0
 assert physical.count(fields.TEMPLATE)==1
 assert physical.count('planner_cast_person_part')==(2 if unknown else 4)
 assert len(physical)==(3 if unknown else 6)
 middle=len(requests)//2;assert requests[:middle]==requests[middle:]


def test_field_correction_wire_is_explicit_stream_and_does_not_change_v1_person_input():
 from bestseller.services import source_cast_field_repair as fields
 settings=load_settings();role=settings.llm.planner.model_copy(update={'model':'openai/MiniMax-M3.1-Flash','stream':False,'reasoning_effort':'','thinking_type':'adaptive'})
 request=llm.LLMCompletionRequest(logical_role='planner',system_prompt='source',user_prompt='{}',fallback_response='{}',prompt_template=fields.TEMPLATE,prompt_version=fields.POLICY,max_tokens_override=fields.OUTPUT_TOKENS)
 actual=llm._apply_conception_phase_settings(request,role)
 assert actual.stream and actual.reasoning_effort=='medium'
 wire=llm._effective_input_payload(request,actual);assert wire['stream'] and wire['max_tokens']==8192 and not wire['tools']
 with pytest.raises(ValueError,match='fields_text_stream_required'):llm._apply_conception_phase_settings(request.model_copy(update={'tools':[{'type':'function'}]}),role)
 original=request.model_copy(update={'prompt_template':'planner_cast_person_part','prompt_version':parts.POLICY,'max_tokens_override':parts.OUTPUT_TOKENS})
 preserved=llm._effective_input_payload(original,llm._apply_conception_phase_settings(original,role))
 assert preserved['stream'] and preserved['max_tokens']==16384
