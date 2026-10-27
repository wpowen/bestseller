"""Prove spent, complete capacity failures without granting grade or fee credit.

Reconstructed old messages are untrusted until their whole effective-input hash
matches the original physical send. Original truncated logs remain unchanged.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import inspect

from bestseller.infra.db import models as db
from bestseller.services import llm, retained_planning_generation as physical
from bestseller.services import retained_review_input_proof as proof
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.retained_planning_generation import _Reader

SCHEMA='planning-capacity-predecessor.v1'
LEGACY_SCHEMA='full-outline-revision-proposal.v1'
OLD_VERSION='3.4-window-complete-repair'
TEMPLATE='planner_volume_1_chapter_outline_commercial_surgical_repair'
LEGACY_SOURCE_CONTEXT_PREFIX=(
    '按来源归因核对以下完整原始材料；只有显式作者字段可形成作者硬约束。'
    '已选规划是当前来源，候选与历史诊断不是新增作者授权。'
    '只修补可执行字段与真实因果/时序冲突，不添加模板化情节。\n')


def _need(ok,code):
    if not ok:raise ValueError('planning_capacity_predecessor:'+code)


def _row_hash(row,model):
    return content_hash({a.key:getattr(row,a.key) for a in inspect(model).column_attrs})


async def fresh_physical_return(session,run,**kwargs):
    """Reuse strict physical proof while reading columns instead of ORM caches."""
    reader=_Reader(session)
    class Fresh:
        async def get(self,model,key):
            return await reader.get(model,key)
    return await proof._physical_return(Fresh(),run,**kwargs)


def _json_at(value,marker):
    _need(value.count(marker)==1,'source_marker')
    tail=value.split(marker,1)[1]
    _need(tail.startswith(LEGACY_SOURCE_CONTEXT_PREFIX),'original_source_context_preamble')
    return json.JSONDecoder().raw_decode(tail[len(LEGACY_SOURCE_CONTEXT_PREFIX):])[0]


async def _accounting(reader,scope,known):
    """Read the existing charges; no reset, release, or inferred missing fee."""
    budget=await reader.get(db.ExecutionBudgetModel,scope)
    attempts=await reader.rows(db.ExecutionAttemptModel,db.ExecutionAttemptModel.scope_id==scope)
    _need(budget is not None and budget.calls_used==len(attempts),'scope_total_calls')
    charged=0
    for attempt in attempts:
        _need(attempt.status in {'completed','not_sent','rejected','reconciled_charged'},
            'scope_total_unsettled')
        amount=attempt.actual_tokens if attempt.actual_tokens is not None else attempt.reserved_tokens
        _need(type(amount) is int and amount>=0,'scope_total_charge_invalid')
        charged+=amount
    _need(budget.tokens_charged==charged,'scope_total_tokens')
    metadata=budget.metadata_json or {}
    journals=metadata.get('finite_book_runs') or {}
    active=metadata.get('active_finite_book_run')
    covered=set()
    for run_id,journal in journals.items():
        rows=journal.get('attempts')
        _need(isinstance(rows,dict) and journal.get('calls')==len(rows),'finite_calls')
        counts={key:0 for key in ('primary','transport_retry','hard_repair')}
        held=spent=0
        for aid,item in rows.items():
            category=item.get('category')
            _need(category in counts,'finite_category')
            counts[category]+=1
            status=item.get('status')
            if status=='completed':
                amount=item.get('actual_micro_cny')
                _need(type(amount) is int and amount>=0,'finite_fee')
                spent+=amount
            elif status=='not_sent':
                _need(item.get('zero_usage_proven') is True,'finite_not_sent')
            else:
                amount=item.get('reserved_micro_cny')
                _need(type(amount) is int and amount>=0,'finite_held')
                held+=amount
            if aid in known:
                expected=known[aid]
                _need(status=='completed' and item.get('operation_key')==expected['operation_key']
                    and item.get('input_hash')==expected['input_hash']
                    and item.get('model')==expected['model']
                    and item.get('actual_micro_cny')==expected['cost'],'finite_charged_closure')
                _need(aid not in covered,'finite_duplicate_attempt')
                covered.add(aid)
        _need(journal.get('counts')==counts and journal.get('spent_micro_cny')==spent
            and journal.get('held_micro_cny')==held,'finite_accounting_totals')
        if run_id==active:
            _need(set(known)<=set(rows),'finite_missing_entry')
    _need(active is None or active in journals,'finite_active_missing')
    return {'budget_row_sha256':_row_hash(budget,db.ExecutionBudgetModel),
        'scope_attempt_rows_sha256':content_hash(sorted(
            (_row_hash(a,db.ExecutionAttemptModel) for a in attempts))),
        'finite_journals_sha256':content_hash(journals),
        'calls_used':budget.calls_used,'tokens_charged':charged,
        'limits':{'scope_id':scope,'max_calls':budget.max_calls,'max_tokens':budget.max_tokens,
            'deadline_at':budget.deadline_at.isoformat() if budget.deadline_at else None}}


async def verify_capacity_predecessor(session,settings,*,project,predecessor_workflow_id,
        source_context,reconstructed_requests,target_contract):
    from bestseller.services.budget import current_execution_scope
    from bestseller.services.outline_revision_lifecycle import _runnable
    from bestseller.services.retry_ledger import project_execution_scope

    with session.no_autoflush:
        _need(settings.artifact_store.mode=='local','physical_store_unavailable')
        fresh=await _runnable(session,project)
        scope=project_execution_scope(fresh)
        reader=_Reader(session)
        owner=await reader.get(db.WorkflowRunModel,UUID(str(predecessor_workflow_id)))
        _need(owner is not None and owner.project_id==fresh.id
            and owner.workflow_type=='propose_full_outline_revision'
            and owner.scope_type=='planning_artifact' and owner.status=='failed','owner')
        state=owner.metadata_json or {}
        _need(state.get('schema')==LEGACY_SCHEMA and state.get('phase')=='terminal_error'
            and state.get('execution_scope_id')==scope==current_execution_scope()
            and state.get('candidate_payload') is None and state.get('proposal_receipt') is None,
            'known_unusable_owner')
        _need(content_hash(state.get('source_context'))==content_hash(source_context),
            'source_family_changed')
        _need(isinstance(target_contract,dict)
            and target_contract.get('prompt_version')=='3.5-source-complete-repair'
            and type(target_contract.get('total_output_tokens')) is int
            and target_contract['total_output_tokens']>12000,'genuine_capacity_successor')
        runs=await reader.rows(db.LlmRunModel,db.LlmRunModel.workflow_run_id==owner.id)
        runs.sort(key=lambda r:(r.created_at,str(r.id)))
        _need(len(runs)==2 and isinstance(reconstructed_requests,list)
            and len(reconstructed_requests)==2,'exact_spent_attempt_set')
        supplied={str(item['llm_run_id']):item['request'] for item in reconstructed_requests}
        _need(set(supplied)=={str(r.id) for r in runs},'reconstructed_run_set')
        receipts=[];known_attempts=set();known_hashes=set();accounting={}
        for run in runs:
            _need(run.project_id==fresh.id and run.logical_role=='planner'
                and run.prompt_template==TEMPLATE and run.prompt_version==OLD_VERSION
                and run.finish_reason=='length' and run.output_tokens==12000
                and type(run.input_tokens) is int and run.input_tokens>0,'known_capacity_failure')
            root=Path(settings.artifact_store.local_dir).expanduser().resolve()
            path=(root/run.request_payload_ref).resolve()
            _need(path.is_relative_to(root),'request_path')
            raw=path.read_bytes()
            original=json.loads(raw,object_pairs_hook=physical._unique,parse_constant=physical._constant)
            _need(original.get('schema_version')=='llm-payload.v1'
                and original.get('kind')=='request' and original.get('replayable') is False
                and original.get('replayability_reasons')==['prompt_truncated']
                and original.get('llm_run_id')==str(run.id)
                and original.get('workflow_run_id')==str(owner.id)
                and original.get('project_id')==str(fresh.id)
                and original.get('logical_role')=='planner','original_request_receipt')
            req=llm.LLMCompletionRequest.model_validate(supplied[str(run.id)])
            _need(req.logical_role=='planner' and req.project_id==fresh.id
                and req.workflow_run_id==owner.id and req.prompt_template==TEMPLATE
                and req.prompt_version==OLD_VERSION and req.messages_override is None
                and req.tools is None and req.tool_choice is None
                and req.max_tokens_override==12000,'reconstructed_request_scope')
            requested=original.get('requested')
            fields={'model_catalog_key','model_tier','temperature_override','max_tokens_override','cache_system'}
            _need(isinstance(requested,dict) and set(requested)==fields
                and all(getattr(req,k)==requested[k] for k in fields),'request_parameters')
            for key,text in [('system',req.system_prompt),('user',req.user_prompt)]:
                stored=original['messages'][key]
                _need(type(stored.get('original_chars')) is int
                    and stored['original_chars']==len(text)
                    and isinstance(stored.get('text'),str)
                    and text.startswith(stored['text'])
                    and stored.get('truncated') is (len(stored['text'])!=len(text)),
                    'request_prefix_or_length_changed')
            _need(content_hash(_json_at(req.user_prompt,'[FULL REVISION SOURCE CONTEXT]\n'))
                ==content_hash(source_context),'reconstructed_source_context')
            catalog,_=proof._fresh_catalog()
            proof._remember_historical_route(req,run)
            role=llm._apply_conception_phase_settings(req,proof._fresh_role(settings,fresh,req,catalog))
            digest=llm._effective_input_hash(req,role)
            _need(role.model==run.model_name==target_contract.get('model'),'model_family_changed')
            returned,key=await fresh_physical_return(session,run,scope=scope,
                expected_hash=digest,expected_model=role.model)
            response,response_sha=physical._file(settings,run,'response')
            physical._response(response,returned)
            _need(returned.finish_reason=='length' and returned.output_tokens==12000
                and returned.input_tokens==run.input_tokens,'complete_unusable_return')
            attempts=await reader.rows(db.ExecutionAttemptModel,
                db.ExecutionAttemptModel.operation_key==key)
            _need(len(attempts)==1 and attempts[0].status=='completed'
                and attempts[0].actual_tokens==run.input_tokens+run.output_tokens
                and attempts[0].input_hash==digest and attempts[0].model_name==role.model,
                'charged_closure')
            known_attempts.add(attempts[0].id);known_hashes.add(digest)
            accounting[attempts[0].id]={'operation_key':key,'input_hash':digest,
                'model':role.model,'cost':run.input_tokens+4*run.output_tokens}
            receipts.append({'llm_run_id':str(run.id),'effective_input_sha256':digest,
                'model':role.model,'request_file_sha256':hashlib.sha256(raw).hexdigest(),
                'response_file_sha256':response_sha,'llm_row_sha256':_row_hash(run,db.LlmRunModel),
                'actual_attempt_id':attempts[0].id,
                'attempt_row_sha256':_row_hash(attempts[0],db.ExecutionAttemptModel),
                'input_tokens':run.input_tokens,'output_tokens':run.output_tokens,
                'original_request_replayable':False,'derived_full_input_hash_verified':True,
                'finish_reason':'length','grade_asserted':False,'fee_credit_granted':False})
        matching=await reader.rows(db.ExecutionAttemptModel,db.ExecutionAttemptModel.scope_id==scope,
            db.ExecutionAttemptModel.input_hash.in_(known_hashes))
        _need({a.id for a in matching}==known_attempts,'unlisted_matching_attempt')
        fees=await _accounting(reader,scope,accounting)
        return {'schema':SCHEMA,'project_id':str(fresh.id),'original_scope':scope,
            'predecessor_workflow_id':str(owner.id),'predecessor_row_sha256':_row_hash(owner,db.WorkflowRunModel),
            'source_context_sha256':content_hash(source_context),'spent_physical_attempts':receipts,
            'target_contract':deepcopy(target_contract),'target_contract_sha256':content_hash(target_contract),
            'existing_accounting':fees,
            'original_logs_rewritten':False,'grade_asserted':False,'fee_credit_granted':False,
            'old_attempts_consumed':2,'budget_reset':False}
