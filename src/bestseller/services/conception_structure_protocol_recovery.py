"""Explicit, additive v1 -> v2 structure-repair checkpoint migration.

The old cumulative spend/attempt count is preserved. This authorizes no grade,
adoption or free calls. One new protocol draw still passes the original scope's
normal paid gateway, and cannot be replenished by changing a workflow label.
"""
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any
from uuid import UUID
from urllib.parse import urlparse
from sqlalchemy import inspect as inspect_row, select
from bestseller.infra.db.models import ExecutionAttemptModel, ExecutionBudgetModel, ExecutionOperationModel, LlmRunModel, WorkflowRunModel
from bestseller.services.budget import completion_authorization, execution_scope_block_code
from bestseller.services.conception_checkpoints import build_conception_checkpoint, validate_conception_checkpoint
from bestseller.services.conception_structure_budget import KEY as COUNTS_KEY, structure_repair_budget
from bestseller.services.conception_structure_repair import STRUCTURE_REPAIR_PROMPT_VERSION

KEY = 'conception_structure_protocol_recovery'
SCHEMA = 'conception-structure-protocol-recovery.v1'


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def require(value, code):
    if not value:
        raise ValueError('conception_structure_protocol_' + code)


def source_policy():
    from bestseller.services.conception import _complete_conception_candidate, _conception_structure_diagnosis, _review_conception_candidate
    from bestseller.services.conception_structure_repair import build_structure_repair_request
    names = ['conception_structure_repair.py','conception_structure_budget.py',
             'intent_review_submission.py','intent_alignment.py',Path(__file__).name]
    return {'protocol':STRUCTURE_REPAIR_PROMPT_VERSION,
            'files':{name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in names},
            'functions':{name:hashlib.sha256(inspect.getsource(fn).encode()).hexdigest() for name,fn in (
                ('_complete_conception_candidate',_complete_conception_candidate),
                ('_review_conception_candidate',_review_conception_candidate),
                ('_conception_structure_diagnosis',_conception_structure_diagnosis),
                ('build_structure_repair_request',build_structure_repair_request))}}


def derive(checkpoint, *, workflow_id, scope, proof, actor, reason):
    state = validate_conception_checkpoint(checkpoint, input_hash=checkpoint['input_hash'])
    require(actor and reason, 'authorization_text')
    require(state.get('structure_repair_attempts') == 1 and COUNTS_KEY not in state, 'legacy_count')
    require(state.get('last_failure',{}).get('structure_repair_error') == {
        'type':'ValueError','message':'structure_repair_candidate_unchanged'}, 'legacy_failure')
    require(proof.get('schema') == 'derived-missing-structure-input-proof.v1'
            and proof.get('kind') == 'derived_missing_input_proof'
            and proof.get('workflow_run_id') == str(workflow_id) and proof.get('scope') == scope
            and proof.get('checkpoint_sha256') == digest(checkpoint)
            and proof.get('prompt_template') == 'conception_structure_repair'
            and proof.get('prompt_version') == 'v1'
            and proof.get('checkpoint_state_sha256') == checkpoint['state_sha256'], 'legacy_proof_binding')
    require(proof.get('only_removed_path') == 'last_failure.structure_repair_error'
            and proof.get('removed_value') == state['last_failure']['structure_repair_error']
            and proof.get('original_request_replayable') is False
            and proof.get('old_physical_request_bytes_verified') is False
            and proof.get('old_payload_refs_rewritten') is False
            and proof.get('credit_released') is False and proof.get('new_grade_created') is False,
            'legacy_proof_limits')
    reconstructed = proof.get('reconstructed_request')
    require(isinstance(reconstructed,str) and hashlib.sha256(reconstructed.encode()).hexdigest()
            == proof.get('reconstructed_request_sha256'), 'derived_input_hash')
    request = json.loads(reconstructed)
    from bestseller.services.llm import LLMCompletionRequest, _effective_input_hash
    from bestseller.settings import apply_runtime_llm_profile, load_settings
    expected_request = LLMCompletionRequest(
        logical_role='editor', model_tier='strong', system_prompt=request['system_prompt'],
        user_prompt=request['user_prompt'], fallback_response='{}',
        prompt_template='conception_structure_repair', prompt_version='v1',
        project_id=None, workflow_run_id=UUID(str(workflow_id)), max_tokens_override=8192,
    )
    require(request == expected_request.model_dump(mode='json'), 'legacy_request_contract')
    require(_effective_input_hash(
        LLMCompletionRequest.model_validate(request),
        apply_runtime_llm_profile(load_settings()).llm.editor,
    ) == proof.get('prompt_hash'), 'original_effective_input_hash')
    source = deepcopy(state)
    source['last_failure'].pop('structure_repair_error')
    from bestseller.services.conception import _conception_structure_diagnosis
    user = json.dumps({'state':source,'diagnosis':_conception_structure_diagnosis(source)},
                      ensure_ascii=False,sort_keys=True,separators=(',', ':'),allow_nan=False)
    require(request.get('user_prompt') == user and len(user) == proof['user_chars']
            and hashlib.sha256(user.encode()).hexdigest() == proof['user_sha256'], 'exact_source_input')
    state[COUNTS_KEY] = {'v1':1,STRUCTURE_REPAIR_PROMPT_VERSION:0}
    require(structure_repair_budget(state,protocol=STRUCTURE_REPAIR_PROMPT_VERSION)==(1,0), 'new_budget')
    return {'schema':SCHEMA,'workflow_run_id':str(workflow_id),'scope':scope,
            'original_checkpoint_sha256':digest(checkpoint),'old_protocol':'v1',
            'new_protocol':STRUCTURE_REPAIR_PROMPT_VERSION,'source_policy':source_policy(),
            'legacy_paid_proof':deepcopy(proof),'legacy_paid_proof_sha256':digest(proof),
            'derived_checkpoint':build_conception_checkpoint(state,input_hash=checkpoint['input_hash']),
            'actor':actor,'reason':reason,'cumulative_attempts_before':1,'cumulative_attempts_after':1,
            'credit_released':False,'old_checkpoint_modified':False,'grade_modified':False,
            'new_protocol_attempt_limit':1,'all_new_calls_charged_to_original_scope':True}


def row(value):
    return json.loads(json.dumps({field.key:getattr(value,field.key)
        for field in inspect_row(type(value)).column_attrs},default=str,allow_nan=False))


async def verify_paid_binding(session, proof, scope):
    run = await session.get(LlmRunModel,UUID(proof['llm_run_id']))
    require(run is not None and digest(row(run)) == proof['run_row_sha256']
            and run.prompt_hash == proof['prompt_hash'] and run.prompt_version == 'v1'
            and str(run.workflow_run_id) == proof['workflow_run_id'], 'paid_run_changed')
    root = await session.get(ExecutionOperationModel,proof['operation_key'])
    require(root is not None and root.scope_id == scope and root.status == 'completed'
            and root.input_hash == proof['prompt_hash']
            and root.result_json.get('fallback_used') is False, 'paid_root_changed')
    # The completed gateway envelope adds runtime telemetry after the original
    # physical transport return. Bind each whole retained row independently;
    # only the actual provider fields must be identical across the envelopes.
    require(digest(row(root)) == proof.get('root_row_sha256'),
            'original_completed_row_changed')
    budget = await session.get(ExecutionBudgetModel, scope)
    require(budget is not None and budget.calls_used >= proof['counters']['calls_used']
            and budget.tokens_charged >= proof['counters']['tokens_charged'], 'counters_regressed')
    actual = list(await session.scalars(select(ExecutionAttemptModel).where(
        ExecutionAttemptModel.scope_id == scope,ExecutionAttemptModel.operation_key == root.key)))
    require(actual and sorted(a.id for a in actual)==proof['actual_attempt_ids']
            and all(a.status=='completed' and a.input_hash==run.prompt_hash and a.model_name==run.model_name for a in actual),
            'all_actual_attempts')
    require(digest([row(a) for a in sorted(actual, key=lambda a: a.id)])
            == proof.get('actual_attempt_rows_sha256'), 'actual_attempt_rows_changed')
    declared = (run.metadata_json or {}).get('execution_receipt',{}).get('attempts',[])
    require(digest(declared)==proof['declared_attempts_sha256'], 'declared_attempts')
    for attempt in actual:
        key = hashlib.sha256(f'transport-result.v1\n{scope}\n{attempt.id}'.encode()).hexdigest()
        transport = await session.get(ExecutionOperationModel,key)
        data = (transport.result_json or {}) if transport is not None else {}
        require(transport is not None and digest(row(transport)) == proof.get('transport_row_sha256')
                and transport.status=='transport_result' and transport.scope_id==scope
                and data.get('operation_key')==root.key and data.get('attempt_id')==attempt.id
                and data.get('input_hash')==run.prompt_hash
                and data.get('result',{}).get('fallback_used') is False
                and digest(data.get('result',{})) == proof['response_proof']['paid_result_sha256']
                and all(data.get('result',{}).get(k)==root.result_json.get(k) for k in (
                    'content','provider','model_name','finish_reason','tool_calls','raw_message','input_tokens','output_tokens')),
                'physical_return_changed')
    return run


async def select_recovery(session, *, run, checkpoint):
    recovery = (run.metadata_json or {}).get(KEY)
    if recovery is None or recovery.get('original_checkpoint_sha256') != digest(checkpoint):
        return checkpoint
    require(recovery.get('schema') == SCHEMA and run.project_id is None
            and run.workflow_type=='conception_initial', 'stored_target')
    expected = derive(checkpoint,workflow_id=run.id,scope=run.metadata_json['execution_scope_id'],
                      proof=recovery['legacy_paid_proof'],actor=recovery['actor'],reason=recovery['reason'])
    require(recovery == expected,'stored_receipt_changed')
    await verify_paid_binding(session,recovery['legacy_paid_proof'],recovery['scope'])
    return deepcopy(recovery['derived_checkpoint'])


async def persist_recovery(session, *, workflow_run_id, recovery):
    run = await session.scalar(select(WorkflowRunModel).where(WorkflowRunModel.id==workflow_run_id)
                               .execution_options(populate_existing=True).with_for_update())
    require(run is not None and run.status=='failed' and run.project_id is None
            and run.workflow_type=='conception_initial','failed_target')
    scope = run.metadata_json['execution_scope_id']
    budget = await session.get(ExecutionBudgetModel,scope)
    auth = completion_authorization(budget) if budget is not None else None
    require(auth and auth.get('status')=='active' and not execution_scope_block_code(budget),'scope_authorization')
    from bestseller.settings import apply_runtime_llm_profile, load_settings
    role = apply_runtime_llm_profile(load_settings()).llm.editor
    require(auth.get('provider') == 'minimax' and auth.get('models') == [role.model]
            and auth.get('endpoint_hosts') == [urlparse(role.api_base or '').hostname],
            'original_provider_authorization')
    old = run.metadata_json['conception_checkpoint']
    expected = derive(old,workflow_id=run.id,scope=scope,proof=recovery['legacy_paid_proof'],
                      actor=recovery['actor'],reason=recovery['reason'])
    require(expected==recovery,'prepared_recovery_changed')
    await verify_paid_binding(session,recovery['legacy_paid_proof'],scope)
    existing = run.metadata_json.get(KEY)
    if existing is not None:
        require(existing==recovery,'existing_receipt_changed')
        return existing
    run.metadata_json = {**run.metadata_json,KEY:deepcopy(recovery)}
    await session.flush()
    return recovery
