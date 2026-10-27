"""Explicit retirement of ownerless checkpoint shells; no historical failure claim."""
from copy import deepcopy
from datetime import UTC,datetime
from sqlalchemy import select
from bestseller.infra.db.models import WorkflowRunModel,ExecutionOperationModel
from bestseller.services.prompt_source_policy import content_hash

async def retire_ownerless_workflows(session,*,project_id,scope,expected,owner_inventory,actor,authorization_ref,explicit_authorization=False):
    """Caller has stopped its owned interpreters and freshly read the ARQ keys.

    No age heuristic, paid-row absence inference, grade change, ledger write or
    retry allowance. This is a cancellation now, not a guessed past failure.
    """
    required={'queued','arq:in-progress:*','arq:job:*','arq:retry:*'}
    if (explicit_authorization is not True or not actor or not authorization_ref
        or set(owner_inventory)!=required or any(owner_inventory.values()) or not expected):
        raise ValueError('workflow_retirement_owner_or_authority_unproven')
    operations=list(await session.scalars(select(ExecutionOperationModel).where(ExecutionOperationModel.scope_id==scope)))
    # Exact non-dispatch receipt kinds produced by ExecutionLedger.
    terminal={'completed','transport_result','failed','stopped','cancelled',
        'transport_failure_receipt','unsent_reclaim_receipt','reconciliation_receipt'}
    if any(op.status not in terminal for op in operations):
        raise ValueError('workflow_retirement_nonterminal_operation')
    rows=list(await session.scalars(select(WorkflowRunModel).where(WorkflowRunModel.project_id==project_id,
        WorkflowRunModel.id.in_(list(expected))).with_for_update().execution_options(populate_existing=True)))
    if len(rows)!=len(expected):raise ValueError('workflow_retirement_scope_changed')
    before=[]
    for row in rows:
        prior={'status':row.status,'current_step':row.current_step,'metadata':deepcopy(row.metadata_json),'error_message':row.error_message}
        if (row.status not in {'running','queued','pending'} or row.metadata_json.get('execution_scope_id')!=scope
            or content_hash(prior)!=expected[row.id]):
            raise ValueError('workflow_retirement_snapshot_changed')
        before.append((row,prior))
    receipt={'schema':'ownerless-workflow-retirement.v1','project_id':str(project_id),'scope':scope,
        'actor':actor,'authorization_ref':authorization_ref,'at':datetime.now(UTC).isoformat(),
        'owner_inventory':deepcopy(owner_inventory),'historical_exit_reason':'unknown',
        'ledger_or_grades_changed':False,'new_credit_granted':False,'cancelled_now':True,
        'original_snapshots':{str(row.id):prior for row,prior in before}}
    for row,prior in before:
        row.metadata_json={**prior['metadata'],'ownerless_retirement_receipt':receipt}
        row.status='cancelled';row.current_step='ownerless_checkpoint_retired'
    await session.flush()
    return receipt
