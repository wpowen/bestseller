"""A request-bound, durable physical-send ceiling inside the existing ledger."""
from copy import deepcopy
from datetime import datetime,timezone
from hashlib import sha256


def authorization_id(user_message_id, prior_workflow_id, *, review_stage=None):
    if not user_message_id or not prior_workflow_id:
        raise ValueError('single_dispatch_authority_missing')
    domain = 'single-dispatch.v1\n'+user_message_id+'\n'+str(prior_workflow_id)
    if review_stage is not None:
        if review_stage not in {'initial', 'consistency'}:
            raise ValueError('single_dispatch_stage_invalid')
        domain += '\noutline-review-stage.v1\n' + review_stage
    return sha256(domain.encode()).hexdigest()


def bind_grant(metadata, *, grant_id, binding):
    out=deepcopy(metadata or {});grants=out.setdefault('single_dispatch_grants',{})
    required={'scope_id','operation_key','input_hash','model','endpoint','workflow_id','user_message_id',
              'prior_workflow_id','prior_journal_sha256','template','version_prefix','authority_evidence'}
    stage = binding.get('review_stage')
    if (set(binding) not in (required, required | {'review_stage'}, required | {'dispatch_scope'})
        or ('dispatch_scope' in binding and binding['dispatch_scope'] != 'workflow')
        or ('review_stage' in binding and stage is None)
        or grant_id!=authorization_id(binding['user_message_id'],binding['prior_workflow_id'],review_stage=stage)):
        raise ValueError('single_dispatch_binding_invalid')
    if stage is not None:
        templates={'initial':'outline_commercial_judge',
                   'consistency':'outline_commercial_judge_consistency_adjudication'}
        if (binding['template'] != templates[stage]
            or binding['workflow_id'] != binding['prior_workflow_id']
            or not isinstance(binding['version_prefix'],str) or not binding['version_prefix']):
            raise ValueError('single_dispatch_stage_binding_invalid')
    if grant_id in grants:
        if grants[grant_id]['binding']!=binding:raise ValueError('single_dispatch_binding_changed')
        return out
    grants[grant_id]={'schema':'single-dispatch-authorization.v1','binding':deepcopy(binding),
        'physical_attempt_cap':1,'status':'available','used_attempt_id':None}
    return out


def consume_grant(metadata, *, scope_id, operation_key, input_hash, model, endpoint,
                  workflow_id, template, version, grant_id, attempt_id):
    """Called under the budget row lock in the SAME reservation transaction."""
    grants=(metadata or {}).get('single_dispatch_grants',{})
    matching=[]
    for key,g in grants.items():
        b=g['binding']
        if b.get('review_stage') is not None or b.get('dispatch_scope') == 'workflow':
            # Review slots protect one case, not every later outline template.
            # A dropped ID/version/template cannot evade the same workflow,
            # operation, or exact paid input. Unrelated future workflows remain
            # outside this cap. The public runner also binds workflow to plan.
            applies = (key == grant_id or str(workflow_id) == b['workflow_id']
                or operation_key == b['operation_key'] or input_hash == b['input_hash'])
        else:
            # Preserve historical Volume grant protection exactly.
            applies = b['template']==template and str(version or '').startswith(b['version_prefix'])
        if applies:
            matching.append(key)
    if not matching and grant_id is None:return metadata
    if grant_id not in matching:raise ValueError('single_dispatch_authorization_missing')
    grant=grants[grant_id];b=grant['binding']
    if (b.get('review_stage') is not None or b.get('dispatch_scope') == 'workflow') and (template != b['template'] or version != b['version_prefix']):
        raise ValueError('single_dispatch_request_changed')
    if grant['status']!='available' or grant['used_attempt_id'] is not None:
        raise ValueError('single_dispatch_already_spent')
    expected=(scope_id,operation_key,input_hash,model,endpoint,str(workflow_id))
    actual=tuple(b[k] for k in ('scope_id','operation_key','input_hash','model','endpoint','workflow_id'))
    if expected!=actual:raise ValueError('single_dispatch_request_changed')
    out=deepcopy(metadata);g=out['single_dispatch_grants'][grant_id]
    g.update(status='spent',used_attempt_id=attempt_id,reserved_at=datetime.now(timezone.utc).isoformat())
    return out


def no_retry_settings(settings):
    """Copy only this invocation; do not alter global role or budget settings."""
    result=settings.model_copy(deep=True)
    result.llm.retry.max_attempts=1
    result.llm.retry.max_attempts_per_class={k:1 for k in result.llm.retry.max_attempts_per_class}
    result.llm.retry.rate_limit_max_attempts=1
    result.llm.retry.rate_limit_fallback_enabled=False
    return result


async def register_grant(ledger, *, grant_id, binding):
    from bestseller.services.budget import ExecutionBudgetError
    async with ledger.transaction() as session:
        row=await ledger._lock(session)
        error=ledger._check(row)
        if error:raise ExecutionBudgetError(error,ledger.scope_id)
        before=(row.max_calls,row.max_tokens,row.deadline_at)
        try:
            row.metadata_json=bind_grant(row.metadata_json,grant_id=grant_id,binding=binding)
        except ValueError as exc:
            raise ExecutionBudgetError(str(exc),ledger.scope_id) from exc
        assert before==(row.max_calls,row.max_tokens,row.deadline_at)
        return deepcopy(row.metadata_json['single_dispatch_grants'][grant_id])
