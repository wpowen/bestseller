"""Restore an exact named person from a complete settled native Cast proposal.

A rejected whole proposal is only repair material. No unknown output is read
as a candidate, no person is generated here, and no semantic grade is granted.
"""
from copy import deepcopy
from uuid import UUID
from sqlalchemy import select
from bestseller.infra.db.models import ExecutionAttemptModel, ExecutionOperationModel, LlmRunModel, WorkflowRunModel
from bestseller.services.budget import ExecutionBudgetError, current_execution_scope

POLICY = 'retained-native-cast-person.v1'


def extract(completion, *, name):
    from bestseller.services.source_bound_planning_submission import (
        parse_planning_submission, PlanningSubmissionError, _strict,
    )
    status = 'unreviewed_wire_valid'
    try:
        payload, _, physical = parse_planning_submission(completion,kind='cast_spec')
    except PlanningSubmissionError as error:
        # Protocol conflicts, truncation and malformed JSON are never salvaged.
        if not error.code.startswith('planning_submission:schema_'):
            raise
        physical = error.receipt
        payload = _strict(physical['asset_json'])
        status = 'rejected_schema'
    if not isinstance(payload,dict):
        raise ValueError('retained_cast_container_not_object')
    found=[]
    for pointer, value in [('$/protagonist',payload.get('protagonist')),
            ('$/antagonist',payload.get('antagonist')),
            *[(f'$/supporting_cast/{index}',value) for index,value in enumerate(payload.get('supporting_cast') or [])]]:
        if isinstance(value,dict) and value.get('name')==name:
            found.append((pointer,value))
    if not found:return None
    if len(found)!=1:raise ValueError('retained_cast_person_ambiguous')
    pointer, person = found[0]
    return {'person':deepcopy(person)}, {
        'container_json':physical['asset_json'], 'container_pointer':pointer,
        'whole_submission_status':status, 'whole_submission_approved':False,
        'original_submission_receipt':physical,
    }


def source_matches(user, document):
    from bestseller.services.planning_dispatch_transport import _document
    from bestseller.services.source_bound_planning_content import decode_source_bound_planning_document
    from bestseller.services.source_cast_parts import _sha
    root, suffix = _document(user)
    packet = deepcopy(decode_source_bound_planning_document(root))
    contract = packet.pop('current_output_contract',None)
    packet.pop('prompt_capacity',None)
    # No arbitrary metadata removal or semantic normalization. Both the whole
    # source packet and the original contract must match literally as JSON data.
    if packet!=document['frozen_source_packet'] or contract!=document['reference_full_cast_contract']:
        return None
    import hashlib
    return {'source_packet_sha256':_sha(packet),
        'retained_retry_suffix_sha256':hashlib.sha256(suffix.encode()).hexdigest(),
        'source_comparison':'exact_decoded_packet_and_contract',
        'grade_reused':False}


async def restore(session, settings, *, project, request, document, schema, workflow_run_id):
    from bestseller.services import llm
    from bestseller.services.retained_review_input_proof import (
        _fresh_catalog,_fresh_role,_retained_request,_physical_return,
    )
    from bestseller.services.source_cast_parts import _sha
    from bestseller.services.source_cast_field_repair import correction_contract
    from bestseller.services.source_bound_planning_submission import wire_diagnostics
    meta=project.metadata_json or {}
    history=meta.get('execution_reconciliation_history') or []
    relevant=[row.get('receipt') for row in history
        if (row.get('terminal') or {}).get('code')=='execution_ledger_unavailable']
    if not relevant:return None
    scope=current_execution_scope()
    if not scope or scope!=meta.get('execution_scope_id'):
        raise ExecutionBudgetError('execution_continuation_binding_invalid',scope or '')
    catalog,_=_fresh_catalog()
    role=llm._apply_conception_phase_settings(request,_fresh_role(settings,project,request,catalog))
    current_hash=llm._effective_input_hash(request,role)
    operations=list(await session.scalars(select(ExecutionOperationModel).where(
        ExecutionOperationModel.scope_id==scope,ExecutionOperationModel.input_hash==current_hash)))
    if any(row.status=='completed' for row in operations):
        return None  # Existing per-person paid result and corrections win unchanged.
    if any(row.status in {'pending','unknown'} for row in operations):
        raise ExecutionBudgetError('execution_operation_inflight',scope)
    predecessor=None
    stopped=[row for row in operations if row.status=='stopped']
    if stopped:
        if len(stopped)!=1 or stopped[0].result_json is not None:
            raise ExecutionBudgetError('execution_operation_stopped',scope)
        from bestseller.services.failed_stream_recovery import retained_quarantine
        for receipt in relevant:
            if not isinstance(receipt,dict):continue
            proof=await retained_quarantine(session,project=project,scope_id=scope,receipt=receipt)
            if proof and proof['operation_key']==stopped[0].key and proof['input_hash']==current_hash:
                if predecessor is not None:raise ExecutionBudgetError('execution_operation_stopped',scope)
                predecessor=proof
        if predecessor is None:raise ExecutionBudgetError('execution_operation_stopped',scope)
    owner=await session.get(WorkflowRunModel,workflow_run_id)
    if (owner is None or owner.project_id!=project.id or owner.status!='running'
        or (owner.metadata_json or {}).get('execution_scope_id')!=scope):
        raise ExecutionBudgetError('execution_continuation_binding_invalid',scope)
    runs=list(await session.scalars(select(LlmRunModel).where(
        LlmRunModel.project_id==project.id,LlmRunModel.logical_role=='planner',
        LlmRunModel.prompt_template=='planner_cast_spec',LlmRunModel.finish_reason=='tool_calls')
        .order_by(LlmRunModel.created_at.desc(),LlmRunModel.id).limit(20)))
    for run in runs:
        old_request=_retained_request(settings,run)
        comparison=source_matches(old_request.user_prompt,document)
        if comparison is None:continue
        old_role=llm._apply_conception_phase_settings(old_request,_fresh_role(settings,project,old_request,catalog))
        effective_hash=llm._effective_input_hash(old_request,old_role)
        if effective_hash!=run.prompt_hash:
            raise ValueError('retained_cast_physical_input_changed')
        completion,key=await _physical_return(session,run,scope=scope,
            expected_hash=effective_hash,expected_model=old_role.model)
        if (completion.finish_reason!='tool_calls' or completion.input_tokens!=run.input_tokens
            or completion.output_tokens!=run.output_tokens):
            raise ValueError('retained_cast_complete_usage_required')
        source_owner=await session.get(WorkflowRunModel,run.workflow_run_id)
        settled=list(await session.scalars(select(ExecutionAttemptModel).where(
            ExecutionAttemptModel.operation_key==key,ExecutionAttemptModel.scope_id==scope)))
        if (source_owner is None or source_owner.project_id!=project.id
            or (source_owner.metadata_json or {}).get('execution_scope_id')!=scope
            or len(settled)!=1 or settled[0].status!='completed'
            or settled[0].actual_tokens!=run.input_tokens+run.output_tokens):
            raise ValueError('retained_cast_settlement_or_owner_mismatch')
        extracted=extract(completion,name=document['current_part']['required_name'])
        if extracted is None:continue
        value, origin=extracted
        errors=wire_diagnostics(value,schema,schema)
        if errors:correction_contract(value,schema)  # Only independently repairable existing leaves.
        completion.llm_run_id=run.id
        import hashlib
        receipt={'schema':POLICY,**comparison,'source_llm_run_id':str(run.id),
            'source_operation_key':key,'source_effective_input_hash':effective_hash,
            'source_model':run.model_name,'source_finish_reason':run.finish_reason,
            'source_input_tokens':run.input_tokens,'source_output_tokens':run.output_tokens,
            'container_pointer':origin['container_pointer'],
            'container_sha256':hashlib.sha256(origin['container_json'].encode()).hexdigest(),
            'person_payload_sha256':_sha(value),'wire_errors':errors,
            'whole_submission_status':origin['whole_submission_status'],
            'whole_submission_approved':False,'quality_approval_granted':False,
            'single_person_provider_response_claimed':False,
            'isolated_predecessor':predecessor,'unknown_output_adopted':False,
            'old_operation_replayed':False,'new_person_generation_calls':0}
        return value,completion,{**origin,'projection_receipt':receipt}
    if stopped:
        # The isolated operation is never a cache miss that starts a new person.
        raise ExecutionBudgetError('execution_operation_stopped',scope)
    return None
