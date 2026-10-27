"""Real SQLite readback: capacity-only recovery does not refund an old slot."""
from copy import deepcopy
from uuid import UUID,uuid4
import pytest
from sqlalchemy import MetaData,ForeignKeyConstraint,JSON
from sqlalchemy.dialects.postgresql import JSONB
from test_prefix_contract_repair import case  # noqa: F401
from test_chapter_outline_readiness import rig,sqlite_identity_store  # noqa: F401
from bestseller.infra.db import models as m
from bestseller.services import prefix_contract_repair as repair,prefix_zero_send_recovery as service,retained_review_input_proof as retained,llm
from bestseller.services.prompt_source_policy import content_hash
from bestseller.settings import load_settings
pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

@pytest.mark.parametrize('fault',[None,'no_authority','completion','wrong_error','spent','physical','operation','run'])
async def test_only_proved_zero_dispatch_can_reopen_next_existing_slot(case,monkeypatch,fault):  # noqa: F811
 settings=load_settings(env={});monkeypatch.setattr(retained,'_fresh_role',lambda *a:settings.llm.planner)
 # Keep this finite-capacity fixture independent of the selected live model's
 # expanding context window. Unknown fixture models use the conservative bound.
 settings.llm.planner.model='fixture/finite-capacity'
 async with case.factory() as s:
  for model in [m.ExecutionBudgetModel,m.ExecutionOperationModel,m.ExecutionAttemptModel]:
   table=model.__table__.to_metadata(MetaData())
   for c in table.c:
    if isinstance(c.type,JSONB):c.type=JSON()
   for constraint in list(table.constraints):
    if isinstance(constraint,ForeignKeyConstraint):table.constraints.remove(constraint)
   async with s.bind.begin() as connection:await connection.run_sync(table.create)
  row=await s.get(m.WorkflowRunModel,case.workflow.id)
  request=llm.LLMCompletionRequest(logical_role='planner',project_id=case.project.id,workflow_run_id=row.id,prompt_template=repair.TEMPLATE,system_prompt='Only patches.',user_prompt='retained archive '*60000,max_tokens_override=12000,fallback_response='{}')
  digest=llm._effective_input_hash(request,settings.llm.planner);origin=content_hash(case.candidate)
  record={'status':'unavailable','candidate_hash':origin,'fence':{'old':'exact'}}
  attempt={'status':'unavailable','consumed':True,'error':{'type':'ModelRequestCapacityError'},'request':request.model_dump(mode='json'),'parent_hash':origin,'source_fence':record['fence']}
  if fault=='completion':attempt['completion']={'finish_reason':'stop'}
  if fault=='wrong_error':attempt['error']={'type':'TimeoutError'}
  state=deepcopy(case.state);state[repair.STATE_KEY]={origin:deepcopy(record)};state['hard_patch_attempts']=[deepcopy(attempt)]
  if fault=='spent':state['hard_patch_attempts']*=2
  row.metadata_json=state;await s.commit()
  if fault in {'physical','operation','run'}:
   from datetime import datetime,UTC
   if fault=='physical':s.add(m.ExecutionAttemptModel(id='a',operation_key='k',scope_id=state['execution_scope_id'],model_name='fixture',input_hash=digest,reserved_tokens=1,actual_tokens=1,status='completed',created_at=datetime.now(UTC)))
   if fault=='operation':s.add(m.ExecutionOperationModel(key='k',scope_id=state['execution_scope_id'],input_hash=digest,status='unknown',owner='o',lease_until=datetime.now(UTC)))
   if fault=='run':s.add(m.LlmRunModel(id=uuid4(),project_id=case.project.id,workflow_run_id=row.id,logical_role='planner',provider='retained',model_name='fixture',prompt_template=repair.TEMPLATE,prompt_hash=digest,finish_reason='stop',metadata_json={}))
   await s.commit()
  original=deepcopy(state)
  kwargs=dict(project=case.project,workflow_id=row.id,actor='authorized-fixture-operator',authorization_ref='fixture explicit repair',explicit_user_approval=fault!='no_authority')
  if fault:
   with pytest.raises(ValueError):await service.recover_capacity_rejection(s,settings,**kwargs)
   await s.rollback();await s.refresh(row);assert row.metadata_json==original
  else:
   result=await service.recover_capacity_rejection(s,settings,**kwargs);await s.commit();await s.refresh(row)
   assert row.metadata_json['hard_patch_attempts']==original['hard_patch_attempts']
   assert row.metadata_json[repair.STATE_KEY][origin]['status']=='blocked'
   assert result['next_slot']==2 and result['remaining_existing_slots']==1 and not result['refund_or_new_allowance']
   approval=await s.get(m.WorkflowRunModel,UUID(result['approval_id']));assert approval.status=='completed'


async def test_registered_approval_purposes_fit_actual_workflow_scope_column():
 from bestseller.services.operator_action_approval import PURPOSES
 limit=m.WorkflowRunModel.__table__.c.scope_type.type.length
 assert all(len(purpose)<=limit for purpose in PURPOSES)
