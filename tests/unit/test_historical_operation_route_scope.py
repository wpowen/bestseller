"""Actual SQLite settled ledger/transport/HTTP, with no fabricated LlmRun."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID,uuid4
from datetime import datetime,UTC,timedelta
import json,hashlib
from pathlib import Path
import pytest
from sqlalchemy import select,func,MetaData,JSON,ForeignKeyConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from gateway_ledger_support import gateway_ledger as gateway_ledger  # noqa:F401
from bestseller.infra.db import models as m
from bestseller.services import llm,retained_review_input_proof as retained,draft_continuity as dc,draft_continuity_adjudication as legacy
from bestseller.services.model_catalog import ModelCatalogEntry
from bestseller.services.prompt_source_policy import content_hash
from bestseller.settings import load_settings,LLMRoleSettings
from test_draft_continuity import BODY,SOURCES,response

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

@pytest.fixture
async def case(gateway_ledger,tmp_path,monkeypatch):
 engine=gateway_ledger;table=m.WorkflowRunModel.__table__.to_metadata(MetaData())
 for col in table.columns:
  if isinstance(col.type,JSONB):col.type=JSON();col.server_default=None
 for constraint in list(table.constraints):
  if isinstance(constraint,ForeignKeyConstraint):table.constraints.remove(constraint)
 async with engine.begin() as conn:await conn.run_sync(table.create)
 st=load_settings(env={});st.llm.mock=False;st.artifact_store.local_dir=tmp_path
 st.llm.critic=LLMRoleSettings(model='openai/MiniMax-M3',temperature=.2,max_tokens=6500,timeout_seconds=30,api_base='https://fixture.invalid/v1',stream=False)
 catalog={key:ModelCatalogEntry(id=key,display_name=key,vendor='fixture',model=model,api_base='https://fixture.invalid/v1') for key,model in [('old','openai/MiniMax-M3'),('new','openai/MiniMax-M3.1-Flash')]}
 monkeypatch.setattr(retained,'_fresh_catalog',lambda:(catalog,'fixture-catalog'))
 monkeypatch.setattr('bestseller.services.model_catalog._resolve_availability',lambda entry:(True,None))
 async with AsyncSession(engine,expire_on_commit=False) as s:
  p=await s.scalar(select(m.ProjectModel).where(m.ProjectModel.slug=='novel'));scope='settled-history-fixture';p.metadata_json={'execution_scope_id':scope,'llm_model_id':'new'}
  wid,cid,rid=uuid4(),uuid4(),uuid4();chapter=SimpleNamespace(id=cid,metadata_json={dc.POINTER:str(rid)})
  code=Path(dc.__file__).read_bytes();(tmp_path/'original-code.py').write_bytes(code);codehash=hashlib.sha256(code).hexdigest()
  logical={'schema':dc.POLICY,'body':BODY,'body_sha256':dc.body_hash(BODY),'sources':SOURCES,'source_sha256':content_hash(SOURCES),'axes':list(dc.AXES),'body_catalog':list(dc.build_critic_body_anchors(BODY)),'source_catalog':dc.source_catalog_index(SOURCES),'source_catalog_sha256':content_hash(dc.source_catalog(SOURCES)),'verifier_source_sha256':codehash}
  req=llm.LLMCompletionRequest(project_id=p.id,workflow_run_id=wid,logical_role='critic',prompt_template='draft_continuity_hard_safety',prompt_version='3.0',system_prompt=dc.SAFETY_SYSTEM_PROMPT,user_prompt=json.dumps(logical,ensure_ascii=False),tools=dc.safety_tools(BODY,SOURCES),tool_choice={'type':'function','function':{'name':dc.SUBMIT}},max_tokens_override=6500,fallback_response='unused')
  oldp=SimpleNamespace(id=p.id,metadata_json={'llm_model_id':'old'})
  role=retained._fresh_role(st,oldp,req,catalog);digest=llm._effective_input_hash(req,role)
  opkey=hashlib.sha256(b'original-settled-operation').hexdigest();aid=uuid4().hex
  raw=response();raw['unknown_issues']=['Original unresolved history remains Unknown'];raw['checked_body_ids'].append(raw['checked_body_ids'][0]);arguments=json.dumps(raw,ensure_ascii=False);calls=[{'id':'original-native','type':'function','function':{'name':dc.SUBMIT,'arguments':arguments}}]
  paid=llm.LLMCompletionResult(content='',provider=llm._provider_from_model(role.model),model_name=role.model,input_tokens=10,output_tokens=20,finish_reason='tool_calls',tool_calls=calls,raw_message={'role':'assistant','content':'','tool_calls':calls},metadata={'paid_call_origin':{'schema':'paid-call-origin.v1','status':'recorded','logical_role':'critic','step_run_id':None,'project_id':str(p.id),'workflow_run_id':str(wid),'prompt_hash':digest,'prompt_template':req.prompt_template,'prompt_version':'3.0'},'effective_input_receipt':{'schema':'effective-input.v1','hash':digest,'model':role.model},'execution_receipt':{'scope_id':scope,'operation_key':opkey,'attempts':[{'attempt_id':aid,'status':'completed'}]}})
  wire={'choices':[{'message':{'tool_calls':calls},'finish_reason':'tool_calls'}],'usage':{'prompt_tokens':10,'completion_tokens':20}}
  entity=json.dumps(wire,ensure_ascii=False).encode();(tmp_path/'original-http.json').write_bytes(entity)
  evidence={'request':req.model_dump(mode='json'),'workflow_id':str(wid),'operation_key':opkey,'http_body_ref':'original-http.json','http_body_sha256':hashlib.sha256(entity).hexdigest(),'evaluation_source_ref':'original-code.py','evaluation_source_sha256':codehash,'observed_llm_run_id':str(uuid4())}
  now=datetime.now(UTC)
  budget=m.ExecutionBudgetModel(scope_id=scope,max_calls=10,max_tokens=100000,calls_used=1,tokens_charged=30,deadline_at=now+timedelta(hours=1),status='active',metadata_json={})
  root=m.ExecutionOperationModel(key=opkey,scope_id=scope,input_hash=digest,status='completed',owner='original-owner',lease_until=now,result_json=paid.model_dump(mode='json'))
  attempt=m.ExecutionAttemptModel(id=aid,scope_id=scope,operation_key=opkey,model_name=role.model,input_hash=digest,reserved_tokens=6500,actual_tokens=30,status='completed',created_at=now)
  transport=m.ExecutionOperationModel(key=hashlib.sha256(f'transport-result.v1\n{scope}\n{aid}'.encode()).hexdigest(),scope_id=scope,input_hash=digest,status='transport_result',owner='original-owner',lease_until=now,result_json={'operation_key':opkey,'attempt_id':aid,'input_hash':digest,'result':paid.model_dump(mode='json')})
  reference=m.WorkflowRunModel(id=rid,project_id=p.id,workflow_type='draft_continuity_reference',scope_type='chapter',scope_id=cid,status='completed',metadata_json={'proof':{'schema':legacy.POLICY,'native_evidence':evidence}})
  s.add_all([p,budget,root,attempt,transport,reference]);await s.commit()
  yield s,st,p,chapter,SimpleNamespace(content_md=BODY),evidence,req,root,attempt,transport

async def test_missing_database_run_uses_exact_settled_history_only_within_scope(case):
 s,st,p,c,d,e,req,root,attempt,transport=case
 before=deepcopy(root.result_json);state=retained._HISTORICAL_ROUTE_WITNESSES.get()
 with pytest.raises(ValueError,match='adjudication_paid_operation'):
  await legacy.verify_paid_evidence(s,st,project=p,chapter=c,draft=d,sources=SOURCES,evidence=e)
 async with retained.historical_reference_route_scope(s,st,project=p,chapter=c):
  raw,proof=await legacy.verify_paid_evidence(s,st,project=p,chapter=c,draft=d,sources=SOURCES,evidence=e)
  assert raw['unknown_issues']==['Original unresolved history remains Unknown'] and proof['model']=='openai/MiniMax-M3' and proof['input_hash']==root.input_hash
  assert proof['original_database_run_missing'] is True and proof['original_row_reinserted'] is False
 assert retained._HISTORICAL_ROUTE_WITNESSES.get() is state
 assert root.result_json==before and await s.scalar(select(func.count()).select_from(m.LlmRunModel))==0
 catalog,_=retained._fresh_catalog();assert retained._fresh_role(st,p,req,catalog).model=='openai/MiniMax-M3.1-Flash'

@pytest.mark.parametrize('bad',['origin','transport','http','attempt','configuration','request'])
async def test_unsettled_conflicting_or_changed_history_never_binds_old_route(case,bad):
 s,st,p,c,d,e,req,root,attempt,transport=case
 if bad=='origin':
  data=deepcopy(root.result_json);data['metadata']['paid_call_origin']['workflow_run_id']=str(uuid4());root.result_json=data
 if bad=='transport':
  data=deepcopy(transport.result_json);data['result']['content']='different physical result';transport.result_json=data
 if bad=='http':(Path(st.artifact_store.local_dir)/e['http_body_ref']).write_text('changed')
 if bad=='attempt':attempt.status='unknown'
 if bad=='configuration':st.llm.critic.temperature=.731
 if bad=='request':
  row=await s.get(m.WorkflowRunModel,UUID(c.metadata_json[dc.POINTER]));data=deepcopy(row.metadata_json);data['proof']['native_evidence']['request']['user_prompt']+=' changed';row.metadata_json=data
 await s.commit();state=retained._HISTORICAL_ROUTE_WITNESSES.get()
 with pytest.raises(ValueError):
  async with retained.historical_reference_route_scope(s,st,project=p,chapter=c):
   await legacy.verify_paid_evidence(s,st,project=p,chapter=c,draft=d,sources=SOURCES,evidence=(await s.get(m.WorkflowRunModel,UUID(c.metadata_json[dc.POINTER]))).metadata_json['proof']['native_evidence'])
 assert retained._HISTORICAL_ROUTE_WITNESSES.get() is state
 assert await s.scalar(select(func.count()).select_from(m.LlmRunModel))==0
