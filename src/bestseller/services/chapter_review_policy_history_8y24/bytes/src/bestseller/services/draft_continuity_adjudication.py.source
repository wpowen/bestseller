"""Source-grounded adjudication of a nonconformant *completed* hard review.

No raw grade/Unknown is rewritten. A separate privileged decision can combine
verified positive per-axis assessments, exact full-body coverage and explicit
source/body adjudication. Unknown/failing axes and nonempty hard blockers can
never enter this route. No model, payment, Canon or working-pointer mutation.
"""
from copy import deepcopy
from pathlib import Path
import hashlib,json,re
from uuid import UUID,uuid4,uuid5,NAMESPACE_URL
from sqlalchemy import select
from bestseller.infra.db import models as m
from bestseller.services import draft_continuity as dc,llm
from bestseller.services.prompt_source_policy import content_hash

POLICY='source-adjudicated-draft-continuity.v1'
FIELDS=('content','provider','model_name','tool_calls','raw_message','finish_reason','input_tokens','output_tokens','fallback_used','fallback_source')
def policy_hash():return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

def strict_json(text):
    def unique(pairs):
        value={}
        for key,item in pairs:
            dc.need(key not in value,'adjudication_duplicate_json_key');value[key]=item
        return value
    return json.loads(text,object_pairs_hook=unique)

def applicable_action_source(path):
    if re.fullmatch(r'/chapter_sources/scenes/[0-9]+/forbidden_actions/[0-9]+',path):return True
    if re.fullmatch(r'/foundations/world_spec/content/rules/[0-9]+/(description|enforcement|story_consequence)',path):return True
    return path in ('/foundations/book_spec/content/series_engine/first_three_chapter_hook',
        '/foundations/book_spec/content/series_engine/continuity_contract',
        '/foundations/book_spec/content/series_engine/knowledge_boundary',
        '/chapter_sources/chapter/main_conflict','/chapter_sources/chapter/chapter_goal') or path.startswith('/chapter_sources/chapter/information_revealed/')

def derive_axis_evidence(raw,*,body,sources,decisions):
    """A new action receipt, never a normalized/repaired original submission."""
    dc.need(isinstance(raw,dict) and set(raw)=={'schema','body_sha256','source_sha256','checked_body_ids','checks','blocking_issues','unknown_issues','submission_complete'}
        and raw.get('schema')==dc.POLICY and raw.get('submission_complete')=='all_applicable_checks_examined','adjudication_native_protocol')
    dc.need(raw.get('body_sha256')==dc.body_hash(body) and raw.get('source_sha256')==content_hash(sources),'adjudication_native_binding')
    anchors={a['id']:a for a in dc.build_critic_body_anchors(body)};catalog={x['id']:x for x in dc.source_catalog(sources)}
    reported=raw.get('checked_body_ids')
    dc.need(isinstance(reported,list) and all(isinstance(x,str) for x in reported) and set(reported)==set(anchors),'adjudication_full_body_coverage')
    checks=raw.get('checks');dc.need(isinstance(checks,list) and len(checks)==len(dc.AXES) and {c.get('axis') for c in checks}==set(dc.AXES),'adjudication_axes')
    for c in checks:
        dc.need(set(c)=={'axis','status','coverage','body_ids','source_ids','explanation'},'adjudication_axis_shape')
        dc.need(c.get('coverage')=='complete' and c.get('status') in ('pass','not_applicable'),'adjudication_axis_not_positive')
        if c['status']=='not_applicable':
            dc.need(c['axis']=='prior_continuity' and sources.get('chapter_sources',{}).get('chapter',{}).get('chapter_number')==1
                and sources.get('prior_manuscript_continuity',{}).get('sources')==[],'adjudication_invalid_na')
        dc.need(isinstance(c.get('body_ids'),list) and c['body_ids'] and set(c['body_ids'])<=set(anchors),'adjudication_body_citation')
        dc.need(isinstance(c.get('source_ids'),list) and c['source_ids'] and set(c['source_ids'])<=set(catalog),'adjudication_source_citation')
        dc.need(isinstance(c.get('explanation'),str) and c['explanation'].strip(),'adjudication_axis_explanation')
    blocking=raw.get('blocking_issues');unknowns=raw.get('unknown_issues')
    dc.need(isinstance(blocking,list) and all(isinstance(x,str) and not x.strip() for x in blocking),'adjudication_hard_blocker_present')
    dc.need(isinstance(unknowns,list) and all(isinstance(x,str) and x.strip() for x in unknowns),'adjudication_unknown_shape')
    dc.need(isinstance(decisions,list) and len(decisions)==len(unknowns) and {x.get('index') for x in decisions}==set(range(len(unknowns))),'adjudication_unresolved_diagnostic')
    actions=[]
    for item in decisions:
        index=item['index'];dc.need(item.get('raw_issue_sha256')==dc.body_hash(unknowns[index]),'adjudication_issue_changed')
        dc.need(item.get('category') in ('preserved_epistemic_boundary','current_plan_delivered_without_past_invention'),'adjudication_category')
        dc.need(isinstance(item.get('reason'),str) and item['reason'].strip(),'adjudication_reason')
        body_proof=[];source_proof=[]
        for citation in item.get('body_evidence',[]):
            anchor=anchors.get(citation.get('id'));quote=citation.get('quote')
            dc.need(anchor is not None and isinstance(quote,str) and len(quote)>=8 and quote in anchor['text'],'adjudication_body_quote')
            start=anchor['start']+anchor['text'].index(quote)
            body_proof.append({**citation,'start':start,'end':start+len(quote),'quote_sha256':dc.body_hash(quote),'body_sha256':dc.body_hash(body),'offset_unit':'unicode-codepoint'})
        for citation in item.get('source_evidence',[]):
            selected=catalog.get(citation.get('id'));quote=citation.get('quote')
            dc.need(selected is not None and isinstance(quote,str) and len(quote)>=4 and quote in selected['text'],'adjudication_source_quote')
            # A methodology flag, runtime log, cast name or future scene title
            # cannot by itself authorize an epistemic/plan adjudication.
            dc.need(applicable_action_source(selected['path']),'adjudication_applicable_source_kind')
            source_proof.append({**citation,'path':selected['path'],'source_value_sha256':dc.body_hash(selected['text']),'source_sha256':content_hash(sources)})
        dc.need(body_proof and source_proof,'adjudication_positive_source_evidence_required')
        actions.append({**deepcopy(item),'body_evidence':body_proof,'source_evidence':source_proof,'effective_action':'preserved_story_boundary_not_unresolved_safety'})
    return {'schema':POLICY,'raw_submission_sha256':content_hash(raw),'raw_protocol_status':'nonconformant_retained',
        'raw_blocking_issues':deepcopy(blocking),'raw_unknown_issues':deepcopy(unknowns),'raw_axis_assessments':deepcopy(checks),
        'coverage':{'raw_rows':len(reported),'distinct_ids':sorted(set(reported)),'catalog_rows':len(anchors),
            'duplicate_echoes':sorted(x for x in set(reported) if reported.count(x)>1),'complete':True,'counts_as_grade_votes':False},
        'diagnostic_actions':actions,'raw_model_grade_modified':False,'qualification_evidence':'positive_native_axes_plus_trusted_exact_source_adjudication'}

async def verify_paid_evidence(session,settings,*,project,chapter,draft,sources,evidence):
    from bestseller.services import retained_review_input_proof as physical
    request=llm.LLMCompletionRequest.model_validate(evidence['request'])
    dc.need(request.project_id==project.id and request.workflow_run_id==UUID(evidence['workflow_id']) and request.prompt_template=='draft_continuity_hard_safety'
        and request.prompt_version=='3.0' and request.system_prompt==dc.SAFETY_SYSTEM_PROMPT and request.tools==dc.safety_tools(draft.content_md,sources)
        and request.messages_override is None and request.tool_choice=={'type':'function','function':{'name':dc.SUBMIT}},'adjudication_original_request_protocol')
    wire=strict_json(request.user_prompt)
    from bestseller.services.lossless_source_encoding import decode_lossless_source
    logical=decode_lossless_source(wire['value']) if wire.get('encoding')=='lossless-source-encoding' else wire
    dc.need(logical['schema']==dc.POLICY and logical['sources']==sources and logical['body']==draft.content_md
        and logical['body_sha256']==dc.body_hash(draft.content_md) and logical['source_sha256']==content_hash(sources)
        and logical['axes']==list(dc.AXES) and logical['body_catalog']==list(dc.build_critic_body_anchors(draft.content_md))
        and logical['source_catalog']==dc.source_catalog_index(sources) and logical['source_catalog_sha256']==content_hash(dc.source_catalog(sources)),
        'adjudication_actual_source_binding')
    # Original evaluation-code hash remains an original witness, not rewritten
    # to the current storage/selection implementation's whole-file hash.
    original=Path(settings.artifact_store.local_dir).expanduser().resolve()/evidence['evaluation_source_ref']
    dc.need(original.resolve().is_relative_to(Path(settings.artifact_store.local_dir).expanduser().resolve())
        and hashlib.sha256(original.read_bytes()).hexdigest()==logical['verifier_source_sha256']==evidence['evaluation_source_sha256'],'adjudication_original_code_witness')
    catalog,_=physical._fresh_catalog();role=physical._fresh_role(settings,project,request,catalog)
    digest=llm._effective_input_hash(request,role);scope=project.metadata_json['execution_scope_id']
    from bestseller.services.budget import execution_scope_block_code
    budget=await session.get(m.ExecutionBudgetModel,scope)
    dc.need(budget is not None and execution_scope_block_code(budget) in (None,'execution_deadline_exhausted','execution_budget_exhausted'),'adjudication_scope_unsettled')
    operation=await session.get(m.ExecutionOperationModel,evidence['operation_key'],populate_existing=True)
    dc.need(operation is not None and operation.scope_id==scope and operation.status=='completed' and operation.input_hash==digest,'adjudication_paid_operation')
    attempts=list(await session.scalars(select(m.ExecutionAttemptModel).where(m.ExecutionAttemptModel.scope_id==scope,m.ExecutionAttemptModel.operation_key==operation.key)))
    dc.need(len(attempts)==1 and attempts[0].status=='completed' and attempts[0].input_hash==digest and attempts[0].model_name==role.model,'adjudication_paid_attempt')
    attempt=attempts[0];key=hashlib.sha256(f'transport-result.v1\n{scope}\n{attempt.id}'.encode()).hexdigest()
    transport=await session.get(m.ExecutionOperationModel,key,populate_existing=True)
    dc.need(transport is not None and transport.status=='transport_result' and transport.result_json['operation_key']==operation.key
        and transport.result_json['attempt_id']==attempt.id and transport.result_json['input_hash']==digest,'adjudication_physical_transport')
    paid=llm.LLMCompletionResult.model_validate(operation.result_json);physical_result=transport.result_json['result']
    dc.need(all(getattr(paid,k)==physical_result.get(k) for k in FIELDS) and not paid.fallback_used and paid.provider==llm._provider_from_model(role.model)
        and paid.model_name==role.model and paid.finish_reason=='tool_calls' and attempt.actual_tokens==paid.input_tokens+paid.output_tokens,'adjudication_full_physical_result')
    origin=paid.metadata['paid_call_origin']
    execution=paid.metadata['execution_receipt']
    dc.need(paid.metadata['effective_input_receipt']['hash']==digest and execution['scope_id']==scope and execution['operation_key']==operation.key
        and [x['attempt_id'] for x in execution['attempts'] if x['status']=='completed']==[attempt.id],'adjudication_paid_receipt_binding')
    dc.need(origin['project_id']==str(project.id) and origin['workflow_run_id']==str(request.workflow_run_id)
        and origin['prompt_hash']==digest and origin['prompt_template']==request.prompt_template and origin['prompt_version']=='3.0','adjudication_physical_origin')
    root=Path(settings.artifact_store.local_dir).expanduser().resolve();http=(root/evidence['http_body_ref']).resolve()
    dc.need(http.is_relative_to(root),'adjudication_http_ref')
    data=http.read_bytes();dc.need(hashlib.sha256(data).hexdigest()==evidence['http_body_sha256'],'adjudication_http_changed')
    upstream=strict_json(data);choices=upstream['choices'];dc.need(len(choices)==1,'adjudication_http_choice')
    args=dc.native_arguments(paid);calls=choices[0]['message']['tool_calls']
    dc.need(len(calls)==1 and calls[0]['function']['name']==dc.SUBMIT and calls[0]['function']['arguments']==args
        and choices[0]['finish_reason']==paid.finish_reason and upstream['usage']['prompt_tokens']==paid.input_tokens
        and upstream['usage']['completion_tokens']==paid.output_tokens,'adjudication_http_sdk_mismatch')
    return strict_json(args),{'operation_key':operation.key,'attempt_id':attempt.id,'input_hash':digest,'model':role.model,
        'original_origin':origin,'physical_result_sha256':content_hash(operation.result_json),'http_body_sha256':evidence['http_body_sha256'],
        'original_database_run_missing':await session.get(m.LlmRunModel,UUID(evidence['observed_llm_run_id'])) is None,
        'original_row_reinserted':False,'new_paid_calls':0}

async def qualify_adjudicated_reference(session,settings,*,project,chapter,draft,evidence,decisions,actor,authorization_ref,explicit_user_approval=False):
    from bestseller.services.operator_action_approval import record_operator_action_approval
    from bestseller.services.drafts import _evaluate_chapter_quality_gate,_collect_post_assembly_duplicate_findings
    dc.need(explicit_user_approval is True,'adjudication_explicit_authorization')
    dc.need(chapter.project_id==project.id and draft.project_id==project.id and draft.chapter_id==chapter.id,'adjudication_scope')
    await session.execute(select(m.ChapterModel.id).where(m.ChapterModel.id==chapter.id).with_for_update())
    await session.refresh(project);await session.refresh(chapter);await session.refresh(draft)
    sources=await dc.current_sources(session,settings,project=project,chapter=chapter)
    pointer=chapter.metadata_json.get(dc.POINTER)
    if pointer:
        previous=await session.get(m.WorkflowRunModel,UUID(pointer),populate_existing=True)
        old=(previous.metadata_json or {}).get('proof') if previous is not None else None
        if old and old.get('schema')==POLICY and old.get('native_evidence')==evidence and old.get('decisions')==decisions and old.get('draft_id')==str(draft.id):
            selected=await dc.select_reference(session,settings,project=project,chapter=chapter)
            dc.need(selected is not None and selected[0].id==draft.id,'adjudication_existing_reference_changed')
            return {'receipt_id':str(previous.id),'draft_id':str(draft.id),'body_sha256':dc.body_hash(draft.content_md),'state':'pending_literary_review','evidence_kind':POLICY,'raw_model_status_changed':False,'formal_adoption':False,'already_qualified':True}
    raw,physical=await verify_paid_evidence(session,settings,project=project,chapter=chapter,draft=draft,sources=sources,evidence=evidence)
    actions=derive_axis_evidence(raw,body=draft.content_md,sources=sources,decisions=decisions)
    binding_tag={'evaluation_id':str(uuid4()),'draft_id':str(draft.id),'body_sha256':dc.body_hash(draft.content_md),'source_sha256':content_hash(sources)}
    outcome=await _evaluate_chapter_quality_gate(session=session,project=project,chapter_number=chapter.chapter_number,content=draft.content_md,register_diversity=False,
        extra_report_payload={'draft_continuity_hard_safety':binding_tag})
    duplicates=await _collect_post_assembly_duplicate_findings(session,project=project,chapter=chapter,content_md=draft.content_md)
    dc.need(outcome=='ok' and not duplicates,'adjudication_current_deterministic_not_positive')
    reports=list(await session.scalars(select(m.ChapterQualityReportModel).where(m.ChapterQualityReportModel.chapter_id==chapter.id).order_by(m.ChapterQualityReportModel.created_at.desc()).limit(10)))
    qr=next(r for r in reports if r.report_json.get('draft_continuity_hard_safety')==binding_tag)
    proof={'schema':POLICY,'adjudicator_source_sha256':policy_hash(),'project_id':str(project.id),'chapter_id':str(chapter.id),'draft_id':str(draft.id),
        'body_sha256':dc.body_hash(draft.content_md),'source_sha256':content_hash(sources),'hard_policy_sha256':dc.hard_policy_hash(),
        'deterministic_outcome':outcome,'quality_report_id':str(qr.id),'deterministic_binding':binding_tag,'native_evidence':evidence,'physical_evidence':physical,
        'source_adjudication':actions,'decisions':decisions,'raw_model_status_changed':False,'formal_adoption':False,'canon_authority':False,'final_export_eligible':False}
    identity=uuid5(NAMESPACE_URL,'bestseller:source-adjudicated-reference:'+content_hash(proof))
    binding={'project_id':str(project.id),'target_id':str(identity),'proof_sha256':content_hash(proof),'draft_id':str(draft.id),'body_sha256':proof['body_sha256'],'source_sha256':proof['source_sha256'],'formal_adoption':False}
    approval=await record_operator_action_approval(session,purpose='draft_continuity_reference',binding=binding,capabilities=['reference_pending_manuscript'],actor=actor,authorization_ref=authorization_ref,explicit_user_approval=True)
    row=await session.get(m.WorkflowRunModel,identity)
    if row is None:session.add(m.WorkflowRunModel(id=identity,project_id=project.id,workflow_type='draft_continuity_reference',scope_type='chapter',scope_id=chapter.id,status='completed',current_step='pending_literary_review',requested_by=actor,metadata_json={'binding':binding,'approval_id':str(approval),'proof':proof,'authority':'Explicit operator source adjudication; not a changed model grade.'}))
    chapter.metadata_json={**chapter.metadata_json,dc.POINTER:str(identity)}
    await session.flush()
    return {'receipt_id':str(identity),'draft_id':str(draft.id),'body_sha256':proof['body_sha256'],'state':'pending_literary_review','evidence_kind':POLICY,'raw_model_status_changed':False,'formal_adoption':False}

async def validate_reference(session,settings,*,project,chapter,draft,proof,_memo=None):
    dc.need(proof['schema']==POLICY and proof['adjudicator_source_sha256']==policy_hash(),'adjudication_policy_changed')
    sources=await dc.current_sources(session,settings,project=project,chapter=chapter,_memo=_memo)
    dc.need(proof['source_sha256']==content_hash(sources) and proof['body_sha256']==dc.body_hash(draft.content_md),'adjudication_sources_changed')
    raw,physical=await verify_paid_evidence(session,settings,project=project,chapter=chapter,draft=draft,sources=sources,evidence=proof['native_evidence'])
    dc.need(physical==proof['physical_evidence'] and derive_axis_evidence(raw,body=draft.content_md,sources=sources,decisions=proof['decisions'])==proof['source_adjudication'],'adjudication_evidence_changed')
    await dc._verify_deterministic_receipt(session,draft=draft,proof=proof)
