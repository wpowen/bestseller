"""Explicit recovery from a witnessed pre-claim capacity rejection.

Historical attempts stay consumed and unchanged. This only reopens a blocked
record so the NEXT already-authorized shared slot can use a corrected request.
No spent slot is refunded, no paid/unknown dispatch is recoverable here.
"""
from copy import deepcopy
from sqlalchemy import select
from bestseller.infra.db import models as m
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.operator_action_approval import record_operator_action_approval

POLICY='prefix-zero-send-capacity-recovery.v1'


def need(value,reason):
    if not value:raise ValueError('prefix_zero_send_'+reason)


async def recover_capacity_rejection(session,settings,*,project,workflow_id,actor,authorization_ref,explicit_user_approval=False):
    from bestseller.services import retained_review_input_proof as retained,llm,prefix_contract_repair as repair
    from bestseller.services.llm_capacity import assert_effective_request_capacity,ModelRequestCapacityError
    need(explicit_user_approval is True and actor and authorization_ref,'explicit_authority')
    row=await session.get(m.WorkflowRunModel,workflow_id,with_for_update=True,populate_existing=True)
    need(row is not None and row.project_id==project.id and row.workflow_type=='approve_chapter_outline_prefix' and row.scope_id==project.id,'workflow_scope')
    state=deepcopy(row.metadata_json);attempts=state.get('hard_patch_attempts',[])
    need(0<len(attempts)<state['identity']['max_repair_rounds'],'no_remaining_existing_slot')
    attempt=attempts[-1]
    need(attempt.get('status')=='unavailable' and attempt.get('consumed') is True and not attempt.get('completion') and attempt.get('error')=={'type':'ModelRequestCapacityError'},'not_preclaim_capacity')
    request=llm.LLMCompletionRequest.model_validate(attempt['request'])
    need(request.project_id==project.id and request.workflow_run_id==row.id and request.prompt_template==repair.TEMPLATE,'request_scope')
    catalog,_=retained._fresh_catalog();role=retained._fresh_role(settings,project,request,catalog)
    try:assert_effective_request_capacity(llm._effective_input_payload(request,role))
    except ModelRequestCapacityError as exc:capacity=exc.receipt
    else:raise ValueError('prefix_zero_send_original_rejection_not_reproduced')
    digest=llm._effective_input_hash(request,role);scope=state['execution_scope_id']
    need(await session.scalar(select(m.ExecutionOperationModel.key).where(m.ExecutionOperationModel.scope_id==scope,m.ExecutionOperationModel.input_hash==digest).limit(1)) is None,'operation_already_exists')
    need(await session.scalar(select(m.ExecutionAttemptModel.id).where(m.ExecutionAttemptModel.scope_id==scope,m.ExecutionAttemptModel.input_hash==digest).limit(1)) is None,'physical_attempt_exists')
    need(await session.scalar(select(m.LlmRunModel.id).where(m.LlmRunModel.workflow_run_id==row.id,m.LlmRunModel.prompt_template==repair.TEMPLATE).limit(1)) is None,'repair_run_exists')
    origin=attempt['parent_hash'];record=state[repair.STATE_KEY][origin]
    need(record['status']=='unavailable' and record['candidate_hash']==origin and record['fence']==attempt['source_fence'],'record_scope')
    binding={'project_id':str(project.id),'target_id':str(row.id),'schema':POLICY,'old_attempt_sha256':content_hash(attempt),'old_record_sha256':content_hash(record),'input_hash':digest,'scope':scope,'capacity_receipt':capacity,'preserved_attempts_sha256':content_hash(attempts),'remaining_existing_slots':state['identity']['max_repair_rounds']-len(attempts),'refund_or_new_allowance':False}
    approval=await record_operator_action_approval(session,purpose='prefix_zero_send_recovery',binding=binding,capabilities=['reopen_unspent_transport'],actor=actor,authorization_ref=authorization_ref,explicit_user_approval=True)
    recovery={**binding,'approval_id':str(approval),'previous_status':record['status'],'next_slot':len(attempts)+1}
    record.setdefault('zero_send_recovery_history',[]).append(recovery);record['status']='blocked'
    state.setdefault('zero_send_capacity_recoveries',[]).append(recovery)
    need(state['hard_patch_attempts']==attempts,'historical_attempts_changed')
    row.metadata_json=state;await session.flush()
    return recovery
