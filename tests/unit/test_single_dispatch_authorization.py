"""Durable reservation cap; no network or production DB."""
from copy import deepcopy
import pytest
from sqlalchemy import select,func
from test_execution_budget_core import ledger_db as ledger_db  # noqa:F401
from test_execution_budget_core import ledger
from bestseller.infra.db.models import ExecutionAttemptModel,ExecutionBudgetModel
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.single_dispatch_authorization import authorization_id,register_grant,no_retry_settings

pytestmark=pytest.mark.unit

def binding(l,claim):
 return {'scope_id':l.scope_id,'operation_key':claim.key,'input_hash':'input','model':'model','endpoint':'https://example.invalid/v1',
  'workflow_id':'fixed-workflow','user_message_id':'human-approval-1','prior_workflow_id':'failed-before',
  'prior_journal_sha256':'immutable-old','template':'planning_draft_admission','version_prefix':'volume-source-admission.',
  'authority_evidence':{'text':'one additional attempt only'}}

@pytest.mark.asyncio
@pytest.mark.parametrize('settlement',['completed','rejected','not_sent','unknown'])
async def test_one_reservation_survives_new_operator_and_all_outcomes(ledger_db,settlement):
 l=ledger(ledger_db,scope='audit:single-dispatch',execution_max_calls=9,execution_max_tokens=9000)
 claim=await l.claim('first','input');b=binding(l,claim);gid=authorization_id(b['user_message_id'],b['prior_workflow_id'])
 await register_grant(l,grant_id=gid,binding=b)
 aid=await l.reserve(claim,'model','input',100,endpoint=b['endpoint'],single_dispatch_id=gid,
  workflow_id=b['workflow_id'],template=b['template'],version='volume-source-admission.v2')
 await l.settle(aid,settlement,actual_tokens=None if settlement=='unknown' else 0 if settlement=='not_sent' else 10)
 # New ledger instance simulates process recovery; no new budget/authorization.
 resumed=ledger(ledger_db,scope=l.scope_id,execution_max_calls=9,execution_max_tokens=9000)
 with pytest.raises(ExecutionBudgetError):
  await resumed.reserve(claim,'model','input',100,endpoint=b['endpoint'],single_dispatch_id=gid,
   workflow_id=b['workflow_id'],template=b['template'],version='volume-source-admission.v2')
 async with l.sessions() as s:
  row=await s.get(ExecutionBudgetModel,l.scope_id)
  assert row.calls_used==1 and row.max_calls==9 and row.max_tokens==9000
  assert row.metadata_json['single_dispatch_grants'][gid]['used_attempt_id']==aid
  assert await s.scalar(select(func.count()).select_from(ExecutionAttemptModel))==1

@pytest.mark.asyncio
async def test_new_operation_or_dropped_grant_does_not_create_credit(ledger_db):
 l=ledger(ledger_db,scope='audit:single-fence',execution_max_calls=9,execution_max_tokens=9000)
 first=await l.claim('first','input');b=binding(l,first);gid=authorization_id(b['user_message_id'],b['prior_workflow_id']);await register_grant(l,grant_id=gid,binding=b)
 other=await l.claim('different-key','input')
 for claim,grant in ((other,gid),(first,None)):
  with pytest.raises(ExecutionBudgetError):
   await l.reserve(claim,'model','input',100,endpoint=b['endpoint'],single_dispatch_id=grant,
    workflow_id='another-workflow',template=b['template'],version='volume-source-admission.v2')
 async with l.sessions() as s:assert (await s.get(ExecutionBudgetModel,l.scope_id)).calls_used==0

@pytest.mark.asyncio
async def test_authorization_identity_cannot_be_rebound(ledger_db):
 l=ledger(ledger_db,scope='audit:single-binding');claim=await l.claim('first','input');b=binding(l,claim);gid=authorization_id(b['user_message_id'],b['prior_workflow_id'])
 await register_grant(l,grant_id=gid,binding=b);changed=deepcopy(b);changed['input_hash']='different'
 with pytest.raises(ExecutionBudgetError,match='binding_changed'):await register_grant(l,grant_id=gid,binding=changed)


def test_retry_copy_closes_every_outer_retry_and_fallback_without_global_mutation():
 from bestseller.settings import load_settings
 st=load_settings();before=st.model_dump(mode='json');scoped=no_retry_settings(st)
 assert st.model_dump(mode='json')==before
 assert scoped.llm.retry.max_attempts==scoped.llm.retry.rate_limit_max_attempts==1
 assert not scoped.llm.retry.rate_limit_fallback_enabled
 assert all(v==1 for v in scoped.llm.retry.max_attempts_per_class.values())

@pytest.mark.asyncio
async def test_spent_volume_grant_still_blocks_new_quote_policy_and_dropped_id(ledger_db):
 l=ledger(ledger_db,scope='audit:single-quote-version',execution_max_calls=9,execution_max_tokens=9000)
 claim=await l.claim('first','input');b=binding(l,claim);gid=authorization_id(b['user_message_id'],b['prior_workflow_id'])
 await register_grant(l,grant_id=gid,binding=b)
 aid=await l.reserve(claim,'model','input',100,endpoint=b['endpoint'],single_dispatch_id=gid,
  workflow_id=b['workflow_id'],template=b['template'],version='volume-source-admission.v2')
 await l.settle(aid,'completed',actual_tokens=10)
 for grant in (gid,None):
  with pytest.raises(ExecutionBudgetError):
   await l.reserve(claim,'model','input',100,endpoint=b['endpoint'],single_dispatch_id=grant,
    workflow_id=b['workflow_id'],template=b['template'],version='volume-source-admission.v3')
 async with l.sessions() as s:
  row=await s.get(ExecutionBudgetModel,l.scope_id)
  assert row.calls_used==1 and row.tokens_charged==10
  assert row.metadata_json['single_dispatch_grants'][gid]['used_attempt_id']==aid

@pytest.mark.asyncio
async def test_workflow_scoped_revision_denies_all_hidden_requests_but_not_later_workflows(ledger_db):
 l=ledger(ledger_db,scope='audit:bounded-editor',execution_max_calls=9,execution_max_tokens=9000)
 claim=await l.claim('authorized-primary','input');b=binding(l,claim)
 b.update(dispatch_scope='workflow',template='chapter_rewrite',version_prefix='3.2-source-selected-voice-editor')
 gid=authorization_id(b['user_message_id'],b['prior_workflow_id']);await register_grant(l,grant_id=gid,binding=b)
 aid=await l.reserve(claim,'model','input',100,endpoint=b['endpoint'],single_dispatch_id=gid,
  workflow_id=b['workflow_id'],template=b['template'],version=b['version_prefix'])
 await l.settle(aid,'completed',actual_tokens=20)
 for template in ['chapter_rewrite_repair','chapter_rewrite_quality_retrofit_repair','meta_leak_cleanup']:
  hidden=await l.claim(template,'other-input')
  with pytest.raises(ExecutionBudgetError,match='single_dispatch'):
   await l.reserve(hidden,'model','other-input',100,endpoint=b['endpoint'],workflow_id=b['workflow_id'],template=template,version='different')
 other=await l.claim('later-chapter','future-input')
 allowed=await l.reserve(other,'model','future-input',100,endpoint=b['endpoint'],workflow_id='different-future-workflow',template=b['template'],version=b['version_prefix'])
 assert allowed!=aid
 async with l.sessions() as s:
  row=await s.get(ExecutionBudgetModel,l.scope_id)
  assert row.calls_used==2 and row.metadata_json['single_dispatch_grants'][gid]['used_attempt_id']==aid
