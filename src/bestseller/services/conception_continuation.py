"""Explicit finite same-story continuation; frozen unknown is never dispatched."""
from datetime import datetime,timedelta,timezone
import hashlib,json
from uuid import uuid5,NAMESPACE_URL
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from bestseller.infra.db.models import ExecutionBudgetModel,ExecutionOperationModel,LlmRunModel
from bestseller.services.independent_acceptance import (KEY,SCHEMA,MODEL,digest,utc,snapshot,
    old_cost_proof,policy,payload_identity,validate_old)
from bestseller.services.minimax_book_cost_contract import CONTRACT_SHA256
from bestseller.services.budget import ExecutionBudgetError,execution_scope_block_code,current_execution_scope

PREFIX='conception-continuation:'

async def prepare(engine,*,task_id,payload,source_scope,authorization_id,authorization_ref,actor):
    if not all((task_id,authorization_id,authorization_ref,actor)):
        raise ValueError('explicit continuation authorization required')
    scope=PREFIX+task_id+':'+str(uuid5(NAMESPACE_URL,authorization_id))
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with factory() as session,session.begin():
        parent=await session.get(ExecutionBudgetModel,source_scope)
        prior_policy=policy(parent) if parent else None
        if prior_policy is None or source_scope!='conception:'+task_id:
            raise ValueError('same-task acceptance source required')
        ancestor_ids=sorted({prior_policy['request']['old_scope'],source_scope})
        ancestors=[];proofs=[]
        for key in ancestor_ids:
            row=await session.scalar(select(ExecutionBudgetModel).where(ExecutionBudgetModel.scope_id==key).with_for_update())
            if row is None or row.status!='unknown' or utc(row.deadline_at)>datetime.now(timezone.utc):
                raise ValueError('ancestors must remain expired and unknown')
            ancestors.append(snapshot(row));proofs.append(await old_cost_proof(session,row))
        existing=await session.get(ExecutionBudgetModel,scope)
        if existing:
            saved=policy(existing)
            if saved['request']['authorization_ref']!=authorization_ref:raise ValueError('authorization conflict')
            return saved
        sibling=await session.scalar(select(ExecutionBudgetModel.scope_id).where(
            ExecutionBudgetModel.metadata_json[KEY]['request']['continuation_task_id'].as_string()==task_id).limit(1))
        if sibling:raise ValueError('continuation already authorized; never auto-renew')
        ops=list(await session.scalars(select(ExecutionOperationModel).where(
            ExecutionOperationModel.scope_id==source_scope)))
        blocked=[x for x in ops if x.status=='unknown']
        if len(blocked)!=1:raise ValueError('exactly one isolated unknown operation required')
        cached=[]
        for op in ops:
            if op.status!='completed':continue
            result=op.result_json or {};origin=(result.get('metadata') or {}).get('paid_call_origin') or {}
            if result.get('model_name')!=MODEL or not origin.get('prompt_template'):
                raise ValueError('original success lacks exact attribution')
            from uuid import UUID
            rid=result.get('llm_run_id')
            run=await session.get(LlmRunModel,UUID(str(rid))) if rid else None
            if run is None:
                runs=list(await session.scalars(select(LlmRunModel).where(
                    LlmRunModel.metadata_json['execution_receipt']['operation_key'].as_string()==op.key)))
                if len(runs)!=1:raise ValueError('original paid success audit is missing or ambiguous')
                run=runs[0]
            cached.append({'operation_key':op.key,'input_hash':op.input_hash,'result_sha256':digest(result),
                'template':origin['prompt_template'],'role':origin['logical_role'],'source_llm_run_id':str(run.id)})
        calls=sum(x['calls'] for x in proofs);tokens=sum(x['tokens_upper'] for x in proofs)
        money=sum(x['standard_micro_cny_upper'] for x in proofs)
        # This is a linked allocation, not a new 75 yuan grant. Preserve 184
        # cumulative sends and all unknowns; 2M new tokens cost at most 8 yuan.
        new_calls=184-calls;new_tokens=2_000_000
        cost=(3*(money+4*new_tokens)+1)//2
        if not 0<new_calls<=128 or cost>75_000_000:
            raise ValueError('cumulative funding or attempt boundary not proven')
        req={'scope':scope,'old_scope':source_scope,'old_snapshot':snapshot(parent),
            'ancestor_snapshots':ancestors,'old_proof':{'calls':calls,'tokens_upper':tokens,
                'standard_micro_cny_upper':money,'evidence_sha256':digest(proofs)},
            'new_calls':new_calls,'new_tokens':new_tokens,'combined_calls':184,
            'combined_tokens_cap':tokens+new_tokens,'combined_tokens_upper':tokens+new_tokens,
            'combined_cost_micro_cny_upper':cost,'total_money_micro_cny':75_000_000,
            'window_seconds':7200,'model':MODEL,'endpoint':prior_policy['request']['endpoint'],
            'max_output':32768,'payload_sha256':payload_identity(payload),'actor':actor,
            'authorization_ref':authorization_ref,'counter_sha256':CONTRACT_SHA256,
            'continuation_task_id':task_id,'cached_results':cached,
            'blocked_operation':blocked[0].key,'blocked_input_hash':blocked[0].input_hash}
        saved={'schema':SCHEMA,'request':req,'request_sha256':digest(req),
            'prepared_at':datetime.now(timezone.utc).isoformat(),'submitted_at':None}
        session.add(ExecutionBudgetModel(scope_id=scope,max_calls=new_calls,max_tokens=new_tokens,
            calls_used=0,tokens_charged=0,status='awaiting_submission',
            deadline_at=datetime.now(timezone.utc)+timedelta(minutes=30),metadata_json={KEY:saved}))
    return saved

async def pending(engine,task_id):
    async with async_sessionmaker(engine)() as session:
        rows=list(await session.scalars(select(ExecutionBudgetModel).where(
            ExecutionBudgetModel.status=='awaiting_submission')))
        for row in rows:
            p=policy(row)
            if p and p['request'].get('continuation_task_id')==task_id and utc(row.deadline_at)>datetime.now(timezone.utc):
                return row.scope_id
    return None

async def consume(engine,task_id,payload):
    scope=await pending(engine,task_id)
    if scope is None:raise ValueError('no pending same-story continuation authorization')
    async with async_sessionmaker(engine,expire_on_commit=False)() as session,session.begin():
        row=await session.scalar(select(ExecutionBudgetModel).where(ExecutionBudgetModel.scope_id==scope).with_for_update())
        p=policy(row);now=datetime.now(timezone.utc)
        if row.status!='awaiting_submission' or utc(row.deadline_at)<=now or p['request']['payload_sha256']!=payload_identity(payload):
            raise ValueError('continuation expired, consumed or story changed')
        await validate_old(session,p)
        p={**p,'submitted_at':now.isoformat()}
        row.metadata_json={**row.metadata_json,KEY:p};row.status='active'
        row.deadline_at=(datetime.fromisoformat(p['request']['fixed_deadline_at'])
            if p['request'].get('fixed_deadline_at') else now+timedelta(seconds=7200))
        from bestseller.services.budget import completion_authorization
        if not completion_authorization(row) and utc(row.deadline_at)<=now:
            raise ValueError('original continuation window expired')
        return scope

async def active_scope(engine,task_id,payload,*,audit_only=False):
    scope=payload.get('_active_continuation_scope')
    if not scope:return None
    async with async_sessionmaker(engine)() as session:
        row=await session.get(ExecutionBudgetModel,scope);p=policy(row) if row else None
        if not p or p['request'].get('continuation_task_id')!=task_id:
            raise ExecutionBudgetError('execution_continuation_binding_invalid',scope)
        if p['request']['payload_sha256']!=payload_identity(payload):
            from bestseller.services.execution_phase_handoff import validate_binding
            try:
                if audit_only:
                    await validate_binding(session,task_id=task_id,payload=payload,audit_only=True)
                else:
                    await validate_binding(session,task_id=task_id,payload=payload)
            except (ValueError,TypeError,KeyError) as error:
                raise ExecutionBudgetError('execution_continuation_binding_invalid',scope) from error
        if not p.get('submitted_at'):raise ExecutionBudgetError('execution_submission_not_authorized',scope)
        return scope

async def version_request(session,request):
    scope=current_execution_scope()
    if not scope or not scope.startswith(PREFIX) or request.prompt_template!='conception_finalize':return request
    async with async_sessionmaker(session.bind)() as read:
        row=await read.get(ExecutionBudgetModel,scope);p=policy(row) if row else None
        if not p or not p.get('submitted_at'):raise ExecutionBudgetError('execution_continuation_binding_invalid',scope)
    return request.model_copy(update={'prompt_template':'conception_finalize_continuation_v2','prompt_version':'v2',
        'user_prompt':request.user_prompt+'\n\n【已审核构思续行·新版本定稿】原定稿请求结果未知，保留隔离。仅以本提示给出的成功提案与主编评审为事实来源，不采用旧请求的任何未完成思考。请形成可执行的新版本，明确逐项处置主编指出的事实矛盾，保持用户原始故事设定与四段完结目标。',
        'operation_id':None,'metadata':{**request.metadata,'supersedes_unknown_operation':p['request']['blocked_operation']}})

async def _retained_sources(session, scope, grant, *, include_windows=False):
    """Read immutable original successes and current-window completed successes."""
    sources=[{**x, 'source_scope':x.get('source_scope', grant['request']['old_scope'])}
             for x in grant['request']['cached_results']]
    if include_windows:
        window_runs=list(await session.scalars(select(LlmRunModel).where(
            LlmRunModel.prompt_template=='chapter_window_quality_judge',
            LlmRunModel.metadata_json['execution_receipt']['scope_id'].as_string()==scope)))
        for run in window_runs:
            key=(run.metadata_json.get('execution_receipt') or {}).get('operation_key')
            root=await session.get(ExecutionOperationModel,key) if key else None
            value=root.result_json if root is not None else None
            origin=((value or {}).get('metadata') or {}).get('paid_call_origin') or {}
            if (root is None or root.scope_id!=scope or root.status!='completed'
                or not isinstance(value,dict) or value.get('model_name')!=MODEL
                or origin.get('prompt_template')!='chapter_window_quality_judge'
                or origin.get('logical_role')!='critic'
                or root.input_hash!=run.prompt_hash or root.input_hash!=origin.get('prompt_hash')
                or root.input_hash!=(run.metadata_json.get('effective_input_receipt') or {}).get('hash')
                or root.input_hash!=(((value or {}).get('metadata') or {}).get('effective_input_receipt') or {}).get('hash')):
                raise ExecutionBudgetError('execution_success_audit_missing',scope)
    ops=list(await session.scalars(select(ExecutionOperationModel).where(
        ExecutionOperationModel.scope_id==scope, ExecutionOperationModel.status=='completed')))
    for op in ops:
        result=op.result_json or {};origin=(result.get('metadata') or {}).get('paid_call_origin') or {}
        if (result.get('model_name')!=MODEL
            or origin.get('prompt_template') not in {'conception_finalize_continuation_v2','market_validation_collision_judge','chapter_window_quality_judge'}):continue
        if origin['prompt_template']=='chapter_window_quality_judge' and not include_windows:continue
        runs=list(await session.scalars(select(LlmRunModel).where(
            LlmRunModel.metadata_json['execution_receipt']['operation_key'].as_string()==op.key)))
        if len(runs)!=1:raise ExecutionBudgetError('execution_success_audit_missing',scope)
        if origin['prompt_template']=='market_validation_collision_judge':
            from bestseller.infra.db.models import ExecutionAttemptModel
            attempts=list(await session.scalars(select(ExecutionAttemptModel).where(ExecutionAttemptModel.operation_key==op.key)))
            run=runs[0]
            if (run.prompt_template!=origin['prompt_template'] or run.logical_role!='critic'
                or run.model_name!=MODEL or run.finish_reason!='stop' or result.get('finish_reason')!='stop'
                or result.get('fallback_used') is not False
                or result.get('input_tokens')!=run.input_tokens or result.get('output_tokens')!=run.output_tokens
                or len(attempts)!=1 or attempts[0].status!='completed'
                or attempts[0].scope_id!=scope or attempts[0].input_hash!=op.input_hash
                or attempts[0].model_name!=MODEL or attempts[0].actual_tokens!=run.input_tokens+run.output_tokens):
                raise ExecutionBudgetError('execution_success_audit_missing',scope)
        window_attempt_ids={}
        if origin['prompt_template']=='chapter_window_quality_judge':
            from bestseller.infra.db.models import ExecutionAttemptModel
            attempts=list(await session.scalars(select(ExecutionAttemptModel).where(ExecutionAttemptModel.operation_key==op.key)))
            if len(attempts)!=1 or attempts[0].status!='completed':
                raise ExecutionBudgetError('execution_success_audit_missing',scope)
            window_attempt_ids={'source_attempt_ids':[attempts[0].id]}
        sources.append({**window_attempt_ids,'operation_key':op.key,'input_hash':op.input_hash,
            'result_sha256':digest(result),'template':origin['prompt_template'],
            'role':origin['logical_role'],'source_llm_run_id':str(runs[0].id),'source_scope':scope})
    return sources


def resumable_control_error(error):
    text=str(error or '')
    return (text=='execution_continuation_preflight_changed'
        or text=='execution_operation_identity_required'
        or 'source_bound_planning_canonical_selection_required' in text
        or 'ValueError: conception checkpoint requires a project-less conception_initial workflow run' in text
        or 'CONCEPT_RECONCILIATION_UNRESOLVED' in text
        or text.startswith('model_request_capacity_exceeded:')
        or '\nbestseller.services.llm_capacity.ModelRequestCapacityError: model_request_capacity_exceeded:' in text)


async def adopt_cached(session,request,prompt_hash,effective_payload=None):
    scope=current_execution_scope()
    if not scope or not scope.startswith(PREFIX):return None
    from bestseller.services.budget import completion_authorization
    from bestseller.services.independent_acceptance import serialize
    factory=async_sessionmaker(session.bind,expire_on_commit=False)
    # Discovery may read arbitrarily many retained results. It must finish and
    # release its transaction before taking any budget or ancestor row locks.
    async with factory() as read,read.begin():
        row=await read.get(ExecutionBudgetModel,scope)
        p=policy(row) if row else None
        if not p:raise ExecutionBudgetError('execution_continuation_binding_invalid',scope)
        code=execution_scope_block_code(row)
        if code:raise ExecutionBudgetError(code,scope)
        await validate_old(read,p,lock=False);req=p['request']
        grant_sha256=digest(p)
        authorization_sha256=digest((row.metadata_json or {}).get('provider_authorization'))
        if prompt_hash in {req['blocked_input_hash'], *req.get('blocked_input_hashes', [])}:
            raise ExecutionBudgetError('execution_unknown_replay_forbidden',scope)
        originals=(await _retained_sources(read,scope,p,include_windows=True)
            if request.prompt_template=='chapter_window_quality_judge'
            else await _retained_sources(read,scope,p))
        templates={x['template'] for x in originals}
        if request.prompt_template not in templates:return None
        matches=[x for x in originals if x['input_hash']==prompt_hash and x['template']==request.prompt_template and x['role']==request.logical_role]
        input_matches = list(matches)
        window = _stable_window_request(request, effective_payload)
        window_sources = [x for x in originals if x['template']=='chapter_window_quality_judge']
        if request.prompt_template=='chapter_window_quality_judge' and window_sources:
            if not window or digest(effective_payload)!=prompt_hash:
                raise ExecutionBudgetError('execution_continuation_preflight_changed',scope)
            _window_authorization(row, p, effective_payload, scope)
            binding={'project_id':str(request.project_id),'workflow_run_id':str(request.workflow_run_id), 'requested':{
                key:getattr(request,key) for key in _WINDOW_PARAMETERS},
                'model':effective_payload['model'], 'endpoint_hash':effective_payload['endpoint_hash'],
                'request_input_hash':prompt_hash}
            originals=[{**x,'window_binding':binding} if x in window_sources else x for x in originals]
            matches=[x for x in originals if x['input_hash']==prompt_hash
                and x['template']==request.prompt_template and x['role']==request.logical_role
                and x['source_scope']==scope]
            input_matches=list(matches)
        if not window and len(matches)>1 and request.operation_id:
            opid=request.operation_id+(f':sample:{request.sample_slot}' if request.sample_slot is not None else '')
            matches=[x for x in matches if x['operation_key']==hashlib.sha256((x['source_scope']+'\n'+opid).encode()).hexdigest()]
        proofs=None
        revision=not matches if window else len(matches)!=1
        if revision:
            # Only a changed effective input under the explicit completion
            # objective may request a fresh review; ambiguity is never novelty.
            objective = completion_authorization(row)
            same_role_sources = [x for x in originals
                if x['template']==request.prompt_template and x['role']==request.logical_role]
            if (not input_matches and same_role_sources and objective
                and objective.get('status')=='active'
                and req.get('completion_authorization_ref')==objective.get('authorization_ref')
                and isinstance(effective_payload,dict)
                and hashlib.sha256(json.dumps(effective_payload,ensure_ascii=False,
                    sort_keys=True,separators=(',',':')).encode()).hexdigest()==prompt_hash):
                content_version = digest({key:effective_payload.get(key)
                    for key in ('messages','tools','tool_choice')})
                key = digest({'schema':'conception-review-input-revision.v1',
                    'scope':scope,'role':request.logical_role,'template':request.prompt_template,
                    'effective_input_hash':prompt_hash})
                selected=same_role_sources
            else:
                error=ExecutionBudgetError('execution_continuation_preflight_changed',scope)
                error.diagnostic={'template':request.prompt_template,'role':request.logical_role,
                    'input_hash':prompt_hash,'effective_payload':effective_payload,
                    'expected_sources':[x for x in originals if x['template']==request.prompt_template]}
                raise error
        else:
            # A stable window is one evaluation, even after legacy namespaces
            # accidentally bought it twice. Prove ALL witnesses before choosing.
            selected=matches if window else [matches[0]]
            if window:
                witness_proofs=[await _cached_adoption_source_proof(read,scope,x) for x in selected]
                ordered=sorted(zip(selected,witness_proofs),key=lambda item:(item[1]['created_at'],item[0]['operation_key']))
                selected=[x for x,_ in ordered]
                proofs=[proof for _,proof in ordered]
            source=selected[0]
            key=hashlib.sha256((scope+'\nadopt\n'+source['operation_key']).encode()).hexdigest()
        if proofs is None:
            proofs=[await _cached_adoption_source_proof(read,scope,source) for source in selected]

    # Serialize only the short receipt phase (also on SQLite, whose dialect
    # ignores FOR UPDATE). PostgreSQL's fresh budget lock covers other workers.
    async with serialize('cached-adoption-receipt:'+scope):
        async with factory() as write,write.begin():
            row=await write.scalar(select(ExecutionBudgetModel).where(
                ExecutionBudgetModel.scope_id==scope).with_for_update())
            current=policy(row) if row else None
            if not current:raise ExecutionBudgetError('execution_continuation_binding_invalid',scope)
            code=execution_scope_block_code(row)
            if code:raise ExecutionBudgetError(code,scope)
            if (digest(current)!=grant_sha256
                or digest((row.metadata_json or {}).get('provider_authorization'))!=authorization_sha256):
                raise ExecutionBudgetError('execution_continuation_preflight_changed',scope)
            req=current['request']
            if prompt_hash in {req['blocked_input_hash'], *req.get('blocked_input_hashes', [])}:
                raise ExecutionBudgetError('execution_unknown_replay_forbidden',scope)
            await validate_old(write,current)
            # Primary-key reads only: never repeat the retained inventory scan
            # while financial locks are held. Freeze exact source provenance
            # through insertion, even when an idempotent receipt already exists.
            for source,expected in zip(selected,proofs):
                actual=await _cached_adoption_source_proof(write,scope,source,lock=True)
                if actual!=expected:
                    raise ExecutionBudgetError('execution_original_success_changed',scope)
            if revision:
                objective=completion_authorization(row)
                if await write.get(ExecutionOperationModel,key) is None:
                    write.add(ExecutionOperationModel(key=key,scope_id=scope,input_hash=prompt_hash,
                        status='review_revision',owner='audited-completion-objective',lease_until=row.deadline_at,
                        result_json={'schema':'conception-review-input-revision.v1',
                            'authorization_ref':objective['authorization_ref'],
                            'template':request.prompt_template,'role':request.logical_role,
                            'content_version_sha256':content_version,'effective_input_hash':prompt_hash,
                            'previous_sources':same_role_sources,'grade_reused':False,
                            **({'source_evidence_mode':'financial_history_only',
                                'source_proofs':proofs} if window else {}),
                            'decision':'fresh_current_input_review_required',
                            'reuse_policy':'only_identical_effective_input_can_reuse_a_completed_result'}))
                from bestseller.services.progress_context import emit_activity
                emit_activity('conception_review_revalidation_required',{
                    'template':request.prompt_template,'content_version_sha256':content_version,
                    'effective_input_hash':prompt_hash,'revision_receipt':key,'grade_reused':False})
                return None
            source=selected[0]
            value=json.loads(json.dumps(proofs[0]['value']));run_id=source['source_llm_run_id']
            window_receipt=({'schema':'stable-window-paid-reuse.v1',
                'selection':'earliest_created_at_then_operation_key',
                'selected_source_operation_key':source['operation_key'],
                'duplicate_witnesses':[{'operation_key':x['operation_key'],
                    'source_llm_run_id':x['source_llm_run_id'],'result_sha256':x['result_sha256'],
                    'created_at':proof['created_at']} for x,proof in zip(selected,proofs)]}
                if window else {})
            value['llm_run_id']=run_id
            if await write.get(ExecutionOperationModel,key) is None:
                write.add(ExecutionOperationModel(key=key,scope_id=scope,input_hash=prompt_hash,
                    status='recovered_success_reference',owner='audited-continuation',lease_until=row.deadline_at,
                    result_json={'source_scope':source['source_scope'],**source,'source_llm_run_id':run_id,**window_receipt}))
            value.setdefault('metadata',{})['execution_receipt']={
                'scope_id':scope,'operation_key':key,'attempts':[],
                'policy':'frozen-known-success-adoption.v1','source_scope':source['source_scope'],
                'source_operation_key':source['operation_key'],'source_result_sha256':source['result_sha256'],**window_receipt}
            value['metadata']['reused_source_llm_run_id']=run_id
            from bestseller.services.progress_context import emit_activity
            emit_activity('execution_success_reused',{'template':request.prompt_template,'source_operation':source['operation_key'],'paid_sends':0})
            return value

async def resume_allowed(engine,task_id,payload):
    scope=await active_scope(engine,task_id,payload)
    if scope is None:raise ValueError('active continuation required')
    from bestseller.infra.db.models import ExecutionAttemptModel
    async with async_sessionmaker(engine)() as session:
        row=await session.get(ExecutionBudgetModel,scope)
        code=execution_scope_block_code(row)
        if code:raise ExecutionBudgetError(code,scope)
        await validate_old(session,policy(row))
        pending_send=await session.scalar(select(ExecutionAttemptModel.id).where(
            ExecutionAttemptModel.scope_id==scope,ExecutionAttemptModel.status.in_(['reserved','unknown'])).limit(1))
        if pending_send:raise ExecutionBudgetError('execution_dispatcher_inflight',scope)
        return scope


async def assert_reconciliation_recovery_source(engine, scope, payload):
    from bestseller.services.concept_tournament import _story_architecture_audit
    from bestseller.infra.db.models import ExecutionAttemptModel
    async with async_sessionmaker(engine)() as session:
        rows=list(await session.scalars(select(ExecutionOperationModel).where(
            ExecutionOperationModel.scope_id==scope,ExecutionOperationModel.status=='completed')))
        for row in rows:
            result=row.result_json or {};origin=(result.get('metadata') or {}).get('paid_call_origin') or {}
            if (origin.get('prompt_template')!='conception_concept_seed_final_premise_reconciliation'
                or result.get('model_name')!=MODEL):continue
            attempt=await session.scalar(select(ExecutionAttemptModel).where(
                ExecutionAttemptModel.scope_id==scope,ExecutionAttemptModel.operation_key==row.key,
                ExecutionAttemptModel.status=='completed'))
            runs=list(await session.scalars(select(LlmRunModel).where(
                LlmRunModel.metadata_json['execution_receipt']['operation_key'].as_string()==row.key)))
            if (attempt is None or len(runs)!=1 or runs[0].model_name!=MODEL
                or runs[0].input_tokens!=result.get('input_tokens')
                or runs[0].output_tokens!=result.get('output_tokens')):continue
            content=str(result.get('content') or '').strip()
            if content.startswith('```'):
                content=content[content.find('\n')+1:content.rfind('```')].strip()
            try:fixed=json.loads(content)
            except (ValueError,TypeError):continue
            if not isinstance(fixed,dict):continue
            report=_story_architecture_audit(fixed,
                genre=str(payload.get('genre') or ''),sub_genre=str(payload.get('sub_genre') or ''),
                chapter_count=int(payload.get('target_chapters') or 0))
            if report.get('passed') and (report.get('placement_normalization') or {}).get('moved'):
                return {'source_operation':row.key,'source_sha256':digest(result),
                        'placement_normalization':report['placement_normalization']}
    raise ExecutionBudgetError('execution_recovery_success_source_missing',scope)



def _canonical_prompt_json(text):
    def unique(pairs):
        out={}
        for key,value in pairs:
            if key in out:raise ValueError('duplicate JSON key')
            out[key]=value
        return out
    def invalid(value):raise ValueError('nonfinite JSON')
    decoder=json.JSONDecoder(object_pairs_hook=unique,parse_constant=invalid)
    out=[];pos=0;i=0
    while i<len(text):
        if text[i] in '{[':
            try:value,end=decoder.raw_decode(text,i)
            except ValueError:
                i+=1;continue
            out.extend([text[pos:i],json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)])
            pos=end;i=end
        else:i+=1
    out.append(text[pos:]);return ''.join(out)


def serialization_equivalent(left,right):
    if left==right:return True
    return _canonical_prompt_json(left)==_canonical_prompt_json(right)


async def restore_retained_serialization(session,settings,request,role):
    scope=current_execution_scope()
    if not scope or not scope.startswith(PREFIX) or request.messages_override is not None or request.tools:
        return request
    from bestseller.services.llm import _effective_input_hash
    current_hash=_effective_input_hash(request,role)
    async with async_sessionmaker(session.bind)() as read:
        row=await read.get(ExecutionBudgetModel,scope);p=policy(row) if row else None
        if not p:return request
        sources=[x for x in await _retained_sources(read,scope,p) if x['template']==request.prompt_template and x['role']==request.logical_role]
        if not sources or any(x['input_hash']==current_hash for x in sources):return request
        from uuid import UUID
        from pathlib import Path
        root=Path(settings.artifact_store.local_dir).expanduser().resolve()
        for source in sources:
            run=await read.get(LlmRunModel,UUID(source['source_llm_run_id']))
            if run is None or not run.request_payload_ref:continue
            path=(root/run.request_payload_ref).resolve()
            if not path.is_relative_to(root) or not path.is_file():continue
            retained=json.loads(path.read_text());messages=retained.get('messages',{})
            if messages.get('source')!='system_user':continue
            system=messages.get('system') or {};user=messages.get('user') or {}
            if system.get('truncated') or user.get('truncated'):continue
            a,b=system.get('text'),user.get('text')
            if not isinstance(a,str) or not isinstance(b,str):continue
            if not serialization_equivalent(a,request.system_prompt) or not serialization_equivalent(b,request.user_prompt):continue
            restored=request.model_copy(update={'system_prompt':a,'user_prompt':b})
            if _effective_input_hash(restored,role)!=source['input_hash']:continue
            from bestseller.services.progress_context import emit_activity
            emit_activity('execution_serialization_restored',{'template':request.prompt_template,'source_operation':source['operation_key'],'paid_sends':0})
            return restored
    return request


async def recover_completed_market_identity_stop(session, *, task_id, scope, payload):
    """Audited explicit same-book recovery of a known local identity stop.

    It never reconciles unknown results, releases cost holds, changes grades,
    or creates execution credit. Called only by explicit reviewed continuation.
    """
    from copy import deepcopy
    from bestseller.infra.db.models import ProjectModel,ChapterModel,WorkflowRunModel,ExecutionAttemptModel
    from bestseller.services.budget import completion_authorization
    if not scope.startswith(PREFIX+task_id+':') or payload.get('_active_continuation_scope')!=scope:
        raise ValueError('market_identity_recovery_task_binding_changed')
    row=await session.scalar(select(ExecutionBudgetModel).where(ExecutionBudgetModel.scope_id==scope).with_for_update())
    objective=completion_authorization(row) if row else None
    if not objective or objective.get('status')!='active':
        raise ValueError('market_identity_recovery_requires_active_completion')
    code=execution_scope_block_code(row)
    if code:raise ExecutionBudgetError(code,scope)
    await validate_old(session,policy(row))
    if await session.scalar(select(ExecutionAttemptModel.id).where(
        ExecutionAttemptModel.scope_id==scope,ExecutionAttemptModel.status.in_(['reserved','unknown'])).limit(1)):
        raise ValueError('market_identity_recovery_has_pending_send')
    project=await session.scalar(select(ProjectModel).where(ProjectModel.slug==payload.get('slug')).with_for_update())
    meta=deepcopy(project.metadata_json or {}) if project else {}
    if (project is None or meta.get('execution_scope_id')!=scope
        or meta.get('execution_terminal',{}).get('code')!='execution_operation_identity_required'
        or project.status!='paused' or meta.get('production_pause_reason')!='execution_operation_identity_required'
        or meta.get('conception_approved')):
        raise ValueError('market_identity_recovery_project_state_changed')
    if await session.scalar(select(ChapterModel.id).where(ChapterModel.project_id==project.id).limit(1)):
        raise ValueError('market_identity_recovery_empty_book_required')
    if await session.scalar(select(WorkflowRunModel.id).where(WorkflowRunModel.project_id==project.id,
        WorkflowRunModel.status.in_(['pending','queued','running','stopping'])).limit(1)):
        raise ValueError('market_identity_recovery_active_writer')
    sources=[x for x in await _retained_sources(session,scope,policy(row))
             if x['source_scope']==scope and x['template']=='market_validation_collision_judge']
    if len(sources)!=1:raise ValueError('market_identity_recovery_exact_paid_source_required')
    source=sources[0]
    semantic=hashlib.sha256(json.dumps({'input_hash':source['input_hash'],'role':'critic',
        'template':'market_validation_collision_judge','version':'v1'},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    legacy=hashlib.sha256((scope+'\n'+semantic+':slot:0').encode()).hexdigest()
    if source['operation_key']!=legacy:raise ValueError('market_identity_recovery_legacy_identity_mismatch')
    proof={'schema':'completed-market-identity-recovery.v1','task_id':task_id,'project_id':str(project.id),
        'scope_id':scope,'source':source,'previous_terminal':deepcopy(meta['execution_terminal']),
        'authorization_ref':objective['authorization_ref'],'actor':'explicit same-book web continuation',
        'reason':'Bind unchanged advisory evaluation to stable semantic identity and reuse settled real result',
        'unknown_reconciled':False,'grade_changed':False,'provider_calls':0,'credit_released':False}
    key=digest(proof)
    if await session.get(ExecutionOperationModel,key) is None:
        session.add(ExecutionOperationModel(key=key,scope_id=scope,input_hash=source['input_hash'],status='identity_recovery',
            owner='audited-completion-objective',lease_until=row.deadline_at,result_json=proof))
    history=list(meta.get('execution_programmatic_recovery_history') or [])
    if len(history)>=16:raise ValueError('market_identity_recovery_history_limit')
    history.append({'receipt_key':key,**proof})
    meta.pop('execution_terminal');meta['execution_programmatic_recovery_history']=history
    meta.update(production_paused=False,production_pause_reason=None,requires_human_review=False)
    project.metadata_json=meta;project.status='planning'
    await session.flush()
    return {'receipt_key':key,**proof}


async def _cached_adoption_source_proof(session, scope, source, *, lock=False):
    """Validate and fingerprint only one exact retained operation and paid audit."""
    from uuid import UUID
    from bestseller.infra.db.models import ExecutionAttemptModel
    statement=select(ExecutionOperationModel).where(ExecutionOperationModel.key==source['operation_key'])
    op=await session.scalar(statement.with_for_update() if lock else statement)
    if (op is None or op.status!='completed' or op.scope_id!=source['source_scope']
        or op.input_hash!=source['input_hash'] or digest(op.result_json)!=source['result_sha256']):
        raise ExecutionBudgetError('execution_original_success_changed',scope)
    value=op.result_json
    statement=select(LlmRunModel).where(LlmRunModel.id==UUID(source['source_llm_run_id']))
    run=await session.scalar(statement.with_for_update() if lock else statement)
    if (run is None or run.model_name!=MODEL
        or run.input_tokens!=value.get('input_tokens') or run.output_tokens!=value.get('output_tokens')):
        raise ExecutionBudgetError('execution_success_audit_missing',scope)
    rows=[op,run]
    # Current-window collision sources also rely on an exactly settled attempt.
    # Recheck that narrow proof, never the whole scope's operation inventory.
    if source['source_scope']==scope and source['template']=='market_validation_collision_judge':
        statement=select(ExecutionAttemptModel).where(ExecutionAttemptModel.operation_key==op.key)
        attempts=list(await session.scalars(statement.with_for_update() if lock else statement))
        if (run.prompt_template!=source['template'] or run.logical_role!='critic'
            or run.finish_reason!='stop' or value.get('finish_reason')!='stop'
            or value.get('fallback_used') is not False
            or len(attempts)!=1 or attempts[0].status!='completed'
            or attempts[0].scope_id!=scope or attempts[0].input_hash!=op.input_hash
            or attempts[0].model_name!=MODEL or attempts[0].actual_tokens!=run.input_tokens+run.output_tokens):
            raise ExecutionBudgetError('execution_success_audit_missing',scope)
        rows.extend(attempts)
    if source['template']=='chapter_window_quality_judge':
        return await _window_source_proof(session,scope,source,op,run,rows,lock=lock)
    return {'value':json.loads(json.dumps(value)),
        'rows_sha256':[digest({attr.key:getattr(row,attr.key)
            for attr in row.__mapper__.column_attrs}) for row in rows]}


_WINDOW_PARAMETERS=('model_tier','model_catalog_key','temperature_override','max_tokens_override','cache_system')


def _stable_window_request(request, payload):
    """Only the native single window; never an independent commercial draw."""
    return (request.logical_role=='critic' and request.prompt_template=='chapter_window_quality_judge'
        and request.prompt_version=='v1' and request.metadata.get('judge_scope')=='window'
        and request.project_id is not None and request.workflow_run_id is not None
        and request.tools is None and request.messages_override is None
        and type(request.sample_slot) is int and request.sample_slot==0
        and not any(key in request.metadata for key in ('sample_slot','sample_index','candidate_index'))
        and isinstance(payload,dict) and payload.get('n_candidates')==1)


def _window_authorization(row, grant, payload, scope):
    from urllib.parse import urlsplit
    from bestseller.services.budget import completion_authorization
    objective=completion_authorization(row)
    endpoint=grant['request']['endpoint']
    if (not grant.get('submitted_at') or not objective or objective.get('status')!='active'
        or grant['request'].get('completion_authorization_ref')!=objective.get('authorization_ref')
        or payload.get('model')!=MODEL or MODEL not in objective['models']
        or payload.get('endpoint_hash')!=hashlib.sha256(endpoint.encode()).hexdigest()
        or urlsplit(endpoint).hostname not in objective['endpoint_hosts']):
        raise ExecutionBudgetError('execution_continuation_preflight_changed',scope)


async def _window_original_operation_proof(session,scope,op,run,workflow,*,lock):
    """Prove the original native slot, which legacy physical requests omit."""
    from uuid import UUID
    from bestseller.infra.db.models import ChapterModel,ChapterDraftVersionModel
    from bestseller.services.budget import execution_operation_id
    from bestseller.services.reviews import chapter_review_body_fingerprint

    def need(condition):
        if not condition:raise ExecutionBudgetError('execution_success_audit_missing',scope)

    need(workflow.workflow_type=='chapter_pipeline' and workflow.scope_type=='chapter'
        and workflow.scope_id is not None)
    statement=select(ChapterModel).where(ChapterModel.id==workflow.scope_id).execution_options(populate_existing=True)
    chapter=await session.scalar(statement.with_for_update() if lock else statement)
    need(chapter is not None and chapter.project_id==run.project_id)
    cycles=(chapter.metadata_json or {}).get('chapter_critic_cycles')
    need(isinstance(cycles,dict))
    matches=[]
    # Current editor-control pointers/policy may have moved. The producer is the
    # persisted original cycle owner and namespace, never the current alias.
    for cycle_id,cycle in cycles.items():
        if not isinstance(cycle,dict) or cycle.get('review_workflow_run_id')!=str(run.workflow_run_id):continue
        need(isinstance(cycle_id,str) and len(cycle_id)==64
            and all(c in '0123456789abcdef' for c in cycle_id)
            and cycle.get('schema_version')=='chapter-critic-cycle.v1' and cycle.get('cycle_id')==cycle_id)
        attempts=cycle.get('attempts')
        need(isinstance(attempts,list) and 1<=len(attempts)<=2)
        for index,entry in enumerate(attempts,1):
            namespace=f'chapter-critic:{cycle_id}:{index}'
            need(isinstance(entry,dict) and type(entry.get('attempt')) is int
                and entry['attempt']==index and entry.get('operation_namespace')==namespace)
            operation_id=execution_operation_id(namespace,
                subject='critic:chapter_window_quality_judge:v1',revision=op.input_hash)+':sample:0'
            if hashlib.sha256(f'{scope}\n{operation_id}'.encode()).hexdigest()==op.key:
                matches.append((cycle,entry))
    need(len(matches)==1)
    cycle,entry=matches[0]
    try:draft_id=UUID(cycle['source_draft_id'])
    except (ValueError,TypeError,KeyError,AttributeError) as error:
        raise ExecutionBudgetError('execution_success_audit_missing',scope) from error
    statement=select(ChapterDraftVersionModel).where(ChapterDraftVersionModel.id==draft_id).execution_options(populate_existing=True)
    draft=await session.scalar(statement.with_for_update() if lock else statement)
    need(draft is not None and draft.project_id==run.project_id and draft.chapter_id==chapter.id)
    fingerprint=chapter_review_body_fingerprint(draft.content_md)
    need(cycle.get('source_body_hash')==fingerprint['digest']
        and ('draft_id' not in entry or entry['draft_id']==str(draft.id))
        and ('body_fingerprint' not in entry or entry['body_fingerprint']==fingerprint))
    # Recovery IDs bind the policy at creation, which compatibility receipts
    # may since have superseded. This proves provenance, not current policy.
    revisions=['initial',cycle.get('policy_source_hash')]
    compatibility=cycle.get('candidate_selection_reconciliations') or []
    rebindings=cycle.get('policy_rebind_history') or []
    need(isinstance(compatibility,list) and isinstance(rebindings,list))
    for receipt in compatibility:
        if (isinstance(receipt,dict) and receipt.get('schema')=='candidate-selection-review-compatibility.v1'
            and receipt.get('cycle_id')==cycle['cycle_id'] and receipt.get('source_draft_id')==str(draft.id)
            and receipt.get('source_body_hash')==fingerprint['digest']):
            revisions.append(receipt.get('previous_policy_hash'))
    for receipt in rebindings:
        proof=receipt.get('proof') if isinstance(receipt,dict) else None
        records=proof.get('before_records') if isinstance(proof,dict) else None
        before=records.get('critic') if isinstance(records,dict) else None
        if (isinstance(before,dict) and before.get('cycle_id')==cycle['cycle_id']
            and before.get('review_workflow_run_id')==str(run.workflow_run_id)
            and before.get('source_draft_id')==str(draft.id) and before.get('source_body_hash')==fingerprint['digest']
            and before.get('policy_source_hash')==proof.get('previous_policy_source_hash')):
            revisions.append(before.get('policy_source_hash'))
    need(cycle['cycle_id'] in {
        hashlib.sha256(f"chapter-critic-cycle.v1\n{run.project_id}\n{chapter.id}\n{fingerprint['digest']}\n{revision}".encode()).hexdigest()
        for revision in revisions if isinstance(revision,str) and (revision=='initial'
            or len(revision)==64 and all(c in '0123456789abcdef' for c in revision))})
    return [chapter,draft]


async def _window_source_proof(session,scope,source,op,run,rows,*,lock):
    """Prove grade reuse or financial history, repeated fresh under receipt locks."""
    from pathlib import Path
    from uuid import UUID
    from bestseller.infra.db.models import ExecutionAttemptModel,WorkflowRunModel
    from bestseller.services import llm
    from bestseller.services.retained_review_input_proof import _retained_request,_physical_return
    from bestseller.services.source_complete_retention import retention_limit
    from bestseller.settings import load_settings,LLMRoleSettings

    def need(condition):
        if not condition:raise ExecutionBudgetError('execution_success_audit_missing',scope)

    async def exact(model,key):
        statement=select(model).where(model.__mapper__.primary_key[0]==key)
        return await session.scalar(statement.with_for_update() if lock else statement)

    binding=source.get('window_binding') or {}
    metadata=run.metadata_json or {};value=op.result_json
    need(source['source_scope']==scope and binding and run.project_id is not None
        and str(run.project_id)==binding.get('project_id') and run.workflow_run_id is not None
        and run.logical_role=='critic' and run.prompt_template=='chapter_window_quality_judge'
        and run.prompt_version=='v1' and run.finish_reason=='stop' and value.get('finish_reason')=='stop'
        and value.get('fallback_used') is False and metadata.get('judge_scope')=='window')
    workflow=await exact(WorkflowRunModel,run.workflow_run_id)
    current_workflow=await exact(WorkflowRunModel,UUID(binding['workflow_run_id']))
    need(workflow is not None and workflow.project_id==run.project_id
        and current_workflow is not None and current_workflow.project_id==run.project_id)
    request_hash=binding.get('request_input_hash')
    need(isinstance(request_hash,str) and len(request_hash)==64
        and all(c in '0123456789abcdef' for c in request_hash))
    # Original slot/body eligibility gates grade reuse only. Changed inputs
    # prove the paid history without consuming any historical grade or payload.
    if request_hash==op.input_hash:
        need(not any(key in metadata for key in ('sample_slot','sample_index','candidate_index')))
    provenance=(await _window_original_operation_proof(session,scope,op,run,workflow,lock=lock)
        if request_hash==op.input_hash else [])
    execution=metadata.get('execution_receipt') or {};attempts=execution.get('attempts') or []
    need(execution.get('policy')=='execution-budget.v1' and execution.get('scope_id')==scope
        and execution.get('operation_key')==op.key and len(attempts)==1
        and attempts[0].get('status')=='completed' and attempts[0].get('attempt_id')
        and source.get('source_attempt_ids')==[attempts[0]['attempt_id']])
    attempt=await exact(ExecutionAttemptModel,attempts[0]['attempt_id'])
    need(attempt is not None and attempt.status=='completed' and attempt.scope_id==scope
        and attempt.operation_key==op.key and attempt.input_hash==op.input_hash and attempt.model_name==MODEL
        and type(run.input_tokens) is int and run.input_tokens>0
        and type(run.output_tokens) is int and run.output_tokens>0
        and attempt.actual_tokens==run.input_tokens+run.output_tokens
        and attempt.reserved_tokens>=attempt.actual_tokens)
    transport_key=hashlib.sha256(f'transport-result.v1\n{scope}\n{attempt.id}'.encode()).hexdigest()
    transport=await exact(ExecutionOperationModel,transport_key)
    need(transport is not None and transport.input_hash==op.input_hash)
    budget=await session.get(ExecutionBudgetModel,scope)
    grant=policy(budget);need(grant is not None and budget.calls_used>=1
        and budget.tokens_charged>=attempt.actual_tokens)
    _window_authorization(budget,grant,binding,scope)
    if request_hash!=op.input_hash:
        try:
            need(isinstance(op.input_hash,str) and len(op.input_hash)==64
                and all(c in '0123456789abcdef' for c in op.input_hash))
            core={'schema':'effective-input.v1','hash':op.input_hash,'model':MODEL}

            def financial_receipt(candidate):
                if not isinstance(candidate,dict):return False
                if candidate==core:return True
                legacy=candidate.get('legacy_prompt_hash')
                return (set(candidate)==set(core)|{'messages_source','tools_included','legacy_prompt_hash'}
                    and all(candidate.get(key)==expected for key,expected in core.items())
                    and candidate.get('messages_source')=='system_user'
                    and candidate.get('tools_included') is False
                    and isinstance(legacy,str) and len(legacy)==64
                    and all(c in '0123456789abcdef' for c in legacy))

            receipt=metadata.get('effective_input_receipt')
            root_metadata=value.get('metadata') or {}
            need(financial_receipt(receipt)
                and financial_receipt(root_metadata.get('effective_input_receipt'))
                and root_metadata['effective_input_receipt']==receipt)
            # This helper verifies ledger/transport closure; it performs no file IO.
            paid,paid_key=await _physical_return(session,run,scope=scope,
                expected_hash=op.input_hash,expected_model=MODEL)
            paid_receipt=paid.metadata.get('effective_input_receipt')
            need(financial_receipt(paid_receipt))
            if 'legacy_prompt_hash' in receipt and 'legacy_prompt_hash' in paid_receipt:
                need(receipt['legacy_prompt_hash']==paid_receipt['legacy_prompt_hash'])
            origin=llm._paid_call_origin(run,op.input_hash)
            need(paid_key==op.key and paid.metadata.get('paid_call_origin')==origin
                and root_metadata.get('paid_call_origin')==origin
                and metadata.get('paid_call_origin')==origin
                and paid.input_tokens==run.input_tokens and paid.output_tokens==run.output_tokens)
            physical_evidence={}
            for kind,ref in (('request',run.request_payload_ref),('response',run.response_payload_ref)):
                need(ref is None or isinstance(ref,str) and bool(ref))
                physical_evidence[kind]=({'status':'not_retained'} if ref is None else
                    {'status':'reference_recorded_not_validated','ref':ref})
        except (ValueError,TypeError,KeyError) as error:
            raise ExecutionBudgetError('execution_success_audit_missing',scope) from error
        rows.extend([workflow,current_workflow,attempt,transport])
        # Legacy hashes above are declared evidence only: no original body was
        # reconstructed or verified. Deliberately omit any grade/result value.
        return {'proof_kind':'financial_history_only','grade_reusable':False,
            'physical_payload_verified':False,'operation_key':op.key,
            'physical_payload_evidence':physical_evidence,
            'source_llm_run_id':str(run.id),'input_hash':op.input_hash,
            'rows_sha256':[digest({attr.key:getattr(row,attr.key)
                for attr in row.__mapper__.column_attrs}) for row in rows]}
    # Global settings are resolved per request, just as in the production API.
    settings=load_settings()
    root=Path(settings.artifact_store.local_dir).expanduser().resolve()
    files=[];payloads=[]
    try:
        for ref in (run.request_payload_ref,run.response_payload_ref):
            need(isinstance(ref,str) and bool(ref))
            path=(root/ref).resolve();need(path.is_relative_to(root) and path.is_file())
            raw=path.read_bytes();files.append({'ref':ref,'sha256':hashlib.sha256(raw).hexdigest()})
            payloads.append(json.loads(raw))
        retained=_retained_request(settings,run)
        request_payload,response=payloads
        need(request_payload['requested']==binding['requested'])
        effective=request_payload['effective']
        need(effective.get('model')==MODEL and effective.get('provider')==run.provider)
        role=LLMRoleSettings(model=MODEL,api_base=grant['request']['endpoint'],
            temperature=effective['temperature'],max_tokens=effective['max_tokens'],
            timeout_seconds=effective['timeout_seconds'],stream=effective['stream'],thinking_type=effective.get('thinking_type'),
            reasoning_effort=effective.get('reasoning_effort'))
        retained.metadata=dict(metadata)
        retention_limit(settings,retained,audit=llm._project_prompt_audit.get(),role=role)
        need(llm._effective_input_hash(retained,role)==op.input_hash)
        core_receipt={'schema':'effective-input.v1','hash':op.input_hash,'model':MODEL}
        native_receipt={**core_receipt,'messages_source':'system_user','tools_included':False,
            'legacy_prompt_hash':llm._hash_prompt(retained.system_prompt,retained.user_prompt)}

        def recognized_receipt(candidate):
            # Exactly the raw transport or complete_text's native enrichment;
            # dict equality alone would also accept 0 as tools_included=False.
            return isinstance(candidate,dict) and (candidate==core_receipt or (
                candidate==native_receipt and candidate.get('tools_included') is False
                and retained.messages_override is None and not retained.tools))

        receipt=metadata.get('effective_input_receipt') or {}
        root_receipt=(value.get('metadata') or {}).get('effective_input_receipt')
        need(recognized_receipt(receipt) and recognized_receipt(root_receipt) and root_receipt==receipt)
        paid,paid_key=await _physical_return(session,run,scope=scope,
            expected_hash=op.input_hash,expected_model=MODEL)
        origin=llm._paid_call_origin(retained,op.input_hash)
        need(paid_key==op.key and paid.metadata.get('paid_call_origin')==origin
            and (value.get('metadata') or {}).get('paid_call_origin')==origin
            and metadata.get('paid_call_origin')==origin
            and recognized_receipt(paid.metadata.get('effective_input_receipt'))
            and paid.input_tokens==run.input_tokens and paid.output_tokens==run.output_tokens)
        need(response.get('schema_version')=='llm-payload.v1' and response.get('kind')=='response'
            and response.get('replayable') is True and not response.get('replayability_reasons')
            and response.get('llm_run_id')==str(run.id)
            and response.get('project_id')==str(run.project_id)
            and response.get('workflow_run_id')==str(run.workflow_run_id)
            and response.get('finish_reason')=='stop' and response.get('fallback_used') is False
            and response.get('model')==MODEL and response.get('provider')==run.provider
            and response.get('input_tokens')==run.input_tokens and response.get('output_tokens')==run.output_tokens)
        need(response.get('raw_message')==paid.raw_message and response.get('tool_calls')==paid.tool_calls
            and response.get('fallback_source')==paid.fallback_source)
        content=response.get('content') or {}
        need(content.get('text')==paid.content and content.get('truncated') is False
            and content.get('original_chars')==len(paid.content))
    except (ValueError,TypeError,KeyError,OSError) as error:
        raise ExecutionBudgetError('execution_success_audit_missing',scope) from error
    rows.extend([workflow,current_workflow,*provenance,attempt,transport])
    return {'value':json.loads(json.dumps(value)), 'created_at':utc(run.created_at).isoformat(),
        'files':files,'rows_sha256':[digest({attr.key:getattr(row,attr.key)
            for attr in row.__mapper__.column_attrs}) for row in rows]}
