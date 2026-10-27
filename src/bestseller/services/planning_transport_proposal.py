"""Carry an old paid but unreviewed proposal into a fresh, fully audited request.

This does not claim exact retained-input replay, import an asset, grade or approve.
Missing historical request files remain missing. Only known settled responses are
proposals; the complete current canonical request and every validator still rule.
"""
from copy import deepcopy
import hashlib,json
from sqlalchemy import select
from bestseller.infra.db.models import LlmRunModel,ExecutionOperationModel,ExecutionAttemptModel,WorkflowRunModel
from bestseller.services.source_bound_planning_submission import parse_planning_submission
from bestseller.services.tool_call_evidence import same_tool_call_evidence

def proposal_reference(run,operation,attempts,*,kind,project_id,scope):
    if (run.project_id!=project_id or run.logical_role!='planner' or run.prompt_template!='planner_'+kind
        or run.request_payload_ref is not None or operation is None or operation.scope_id!=scope
        or operation.status!='completed' or operation.input_hash!=run.prompt_hash):return None
    result=operation.result_json or {};origin=(result.get('metadata') or {}).get('paid_call_origin') or {}
    if (result.get('fallback_used') is not False or result.get('model_name')!=run.model_name
        or result.get('finish_reason')!='tool_calls' or run.finish_reason!='tool_calls'
        or origin.get('project_id')!=str(project_id) or origin.get('workflow_run_id')!=str(run.workflow_run_id)
        or origin.get('prompt_hash')!=run.prompt_hash or origin.get('prompt_template')!=run.prompt_template
        or origin.get('prompt_version')!=run.prompt_version
        or result.get('input_tokens')!=run.input_tokens or result.get('output_tokens')!=run.output_tokens
        or type(run.input_tokens) is not int or type(run.output_tokens) is not int
        or len(attempts)!=1):return None
    a=attempts[0]
    if (a.operation_key!=operation.key or a.status!='completed' or a.scope_id!=scope or a.model_name!=run.model_name
        or a.input_hash!=run.prompt_hash or a.actual_tokens!=run.input_tokens+run.output_tokens):return None
    calls=result.get('tool_calls');raw=result.get('raw_message') or {};raw_calls=raw.get('tool_calls')
    if (not calls or not isinstance(raw_calls,list) or not any(isinstance(x,dict) and 'index' in x for x in raw_calls)
        or not same_tool_call_evidence(raw_calls,calls)):return None
    try:asset,_,_=parse_planning_submission(result,kind=kind)
    except (ValueError,TypeError,KeyError):return None
    return {'schema':'planning-transport-proposal.v1','authority':'unreviewed_paid_candidate',
        'source_llm_run_id':str(run.id),'source_workflow_run_id':str(run.workflow_run_id),
        'source_operation_key':operation.key,'source_input_hash':run.prompt_hash,
        'source_result_sha256':hashlib.sha256(json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'exact_historical_request_proven':False,'grade_reused':False,'approval_asserted':False,
        'proposal':deepcopy(asset)}

async def append_prior_proposal(session,project,*,kind,user_prompt):
    scope=(project.metadata_json or {}).get('execution_scope_id')
    if not scope:return user_prompt,None
    runs=list(await session.scalars(select(LlmRunModel).where(LlmRunModel.project_id==project.id,
        LlmRunModel.logical_role=='planner',LlmRunModel.prompt_template=='planner_'+kind,
        LlmRunModel.request_payload_ref.is_(None)).order_by(LlmRunModel.created_at.desc()).limit(5)))
    for run in runs:
        owner=await session.get(WorkflowRunModel,run.workflow_run_id) if run.workflow_run_id else None
        if (owner is None or owner.project_id!=project.id or owner.status!='failed'
            or (owner.metadata_json or {}).get('execution_scope_id')!=scope):continue
        key=((run.metadata_json or {}).get('execution_receipt') or {}).get('operation_key')
        op=await session.get(ExecutionOperationModel,key) if key else None
        attempts=list(await session.scalars(select(ExecutionAttemptModel).where(ExecutionAttemptModel.operation_key==key))) if key else []
        reference=proposal_reference(run,op,attempts,kind=kind,project_id=project.id,scope=scope)
        if reference is None:continue
        return prompt_with_prior_proposal(user_prompt,reference),reference

    return user_prompt,None

def prompt_with_prior_proposal(user_prompt,reference):
    # Keep the unreviewed proposal inside the complete logical document so the
    # existing lossless encoder measures/encodes it with every canonical source.
    # A raw suffix would be outside that encoding and can exceed model capacity.
    from bestseller.services.planning_dispatch_transport import _document
    from bestseller.services.source_bound_planning_content import decode_source_bound_planning_document
    document,suffix=_document(user_prompt)
    logical=deepcopy(decode_source_bound_planning_document(document))
    if 'unreviewed_prior_paid_proposal' in logical:
        raise ValueError('planning_prior_proposal_already_present')
    logical['unreviewed_prior_paid_proposal']={
        'instructions':('This is a prior model proposal, not author intent, canonical authority or an approved grade. '
            'Reuse only facts compatible with the complete current canonical sources; correct and submit the complete current typed asset. '
            'All current schema, source and semantic review requirements remain mandatory.'),
        'evidence':deepcopy(reference)}
    return json.dumps(logical,ensure_ascii=False,sort_keys=True,separators=(',',':'))+suffix
