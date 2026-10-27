# ruff: noqa: F811
"""Actual SQLite state transitions; no provider, original accounting untouched."""
from copy import deepcopy
from uuid import uuid4
import pytest
from sqlalchemy import select
from test_chapter_outline_readiness import rig,sqlite_identity_store  # noqa:F401
from test_prefix_production_entry_fault_replay import entry_case,_entry  # noqa:F401
from bestseller.infra.db.models import WorkflowRunModel,ExecutionOperationModel
from bestseller.services.workflow_retirement import retire_ownerless_workflows
from bestseller.services.prompt_source_policy import content_hash
pytestmark=[pytest.mark.unit,pytest.mark.asyncio]
EMPTY={'queued':[],'arq:in-progress:*':[],'arq:job:*':[],'arq:retry:*':[]}

@pytest.mark.parametrize('fault',[None,'snapshot','owner','authority','operation','scope','unknown_receipt','transport_failure_receipt','unsent_reclaim_receipt','reconciliation_receipt'])
async def test_exact_ownerless_retirement_preserves_old_truth(entry_case,fault):
 c=entry_case;await _entry(c);identity=uuid4()
 async with c.sessions() as s:
  row=WorkflowRunModel(id=identity,project_id=c.project_id,workflow_type='chapter_pipeline',status='running',
   scope_type='project',scope_id=c.project_id,current_step='old_checkpoint',requested_by='fixture',
   metadata_json={'execution_scope_id':c.scope,'old_unknown':True,'paid_counter':3})
  s.add(row);await s.commit()
  prior={'status':row.status,'current_step':row.current_step,'metadata':deepcopy(row.metadata_json),'error_message':row.error_message}
  expected={identity:content_hash(prior)}
  owners=deepcopy(EMPTY)
  if fault=='snapshot':row.current_step='concurrent_new_stage';await s.commit()
  if fault=='owner':owners['arq:in-progress:*']=['active-owner']
  if fault=='scope':row.metadata_json={**row.metadata_json,'execution_scope_id':'foreign'};await s.commit()
  receipt_kind=fault in {'transport_failure_receipt','unsent_reclaim_receipt','reconciliation_receipt'}
  if fault=='operation' or fault=='unknown_receipt' or receipt_kind:
   operation=await s.scalar(select(ExecutionOperationModel).where(ExecutionOperationModel.scope_id==c.scope))
   assert operation is not None;operation.status='pending' if fault=='operation' else ('invented_receipt' if fault=='unknown_receipt' else fault);await s.commit()
  args=dict(project_id=c.project_id,scope=c.scope,expected=expected,owner_inventory=owners,
   actor='fixture',authorization_ref='isolated local test',explicit_authorization=fault!='authority')
  rejected=bool(fault) and not receipt_kind
  if rejected:
   with pytest.raises(ValueError):await retire_ownerless_workflows(s,**args)
   await s.rollback();await s.refresh(row);assert row.status=='running'
  else:
   receipt=await retire_ownerless_workflows(s,**args);await s.commit()
   assert receipt['historical_exit_reason']=='unknown' and receipt['ledger_or_grades_changed'] is False
   assert receipt['original_snapshots'][str(identity)]==prior
 async with c.sessions() as s:
  row=await s.get(WorkflowRunModel,identity)
  if not rejected:
   assert row.status=='cancelled' and row.metadata_json['old_unknown'] is True and row.metadata_json['paid_counter']==3
  else:assert row.status=='running'
