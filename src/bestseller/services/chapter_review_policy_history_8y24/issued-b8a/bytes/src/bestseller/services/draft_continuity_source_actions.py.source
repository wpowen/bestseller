"""Explicit exact-source decisions over two preserved, nonconformant witnesses.

Neither native submission is repaired or called passing. The second must be a
complete independently paid witness; all its original diagnostics, wrong subject
citations and misclassified comments remain immutable. A privileged approval
must cover EVERY original diagnostic, returned decision and extra comment with
fresh, semantically reviewed exact body/source evidence. No model dispatch.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import re
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL
from sqlalchemy import select
from bestseller.infra.db import models as m
from bestseller.services import draft_continuity as dc, draft_continuity_supplement as supplemental
from bestseller.services.draft_continuity_adjudication import strict_json
from bestseller.services.prompt_source_policy import content_hash

POLICY='draft-continuity-source-corrected-reference.v1'
CATEGORIES={'exact_qualified_prior_continuation','future_or_absent_not_required_currently','approved_present_task_not_past_history',
 'nonmandatory_unestablished_process','misclassified_meta_comment','citation_subject_mismatch_independently_reproved','source_boundary_corrected_in_exact_child','current_axis_source_review'}


def policy_hash():return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def source_kind(path,plan=False):
    if plan:
        if re.fullmatch(r'/scenes/[0-9]+/action_sequence/[0-9]+',path):return 'approved_future_action_not_past_fact'
        if re.fullmatch(r'/scenes/[0-9]+/(hook_requirement|purpose/story)',path):return 'approved_future_intent_not_past_fact'
        return None
    if path=='/chapter_sources/acceptance_contract/source_authority_channels/policy':return 'current_authority_contract'
    if re.fullmatch(r'/prior_manuscript_continuity/sources/[0-9]+/body',path):return 'verified_prior_prose_not_formal_canon'
    if re.fullmatch(r'/foundations/world_spec/content/rules/[0-9]+/(description|enforcement|story_consequence)',path):return 'selected_world_boundary'
    if re.fullmatch(r'/foundations/book_spec/content/(series_engine/[a-z_]+(?:/[0-9]+)?|power_system/tiers/[0-9]+)',path):return 'selected_book_direction_or_tier_label'
    if re.fullmatch(r'/foundations/volume_plan/content/[0-9]+/volume_goal',path):return 'selected_volume_boundary'
    return None


def inspect_complete_supplement(value, *, body,sources,plan,original):
    expected={'schema','body_sha256','source_sha256','original_arguments_sha256','approved_plan_sha256','checked_body_ids','issue_decisions','additional_hard_issues','submission_complete'}
    dc.need(isinstance(value,dict) and set(value)==expected and value['schema']==supplemental.POLICY,'source_actions_supplement_shape')
    dc.need(value['body_sha256']==dc.body_hash(body) and value['source_sha256']==content_hash(sources)
      and value['approved_plan_sha256']==content_hash(plan) and value['original_arguments_sha256']==original['original_arguments_sha256'],'source_actions_supplement_binding')
    bids={a['id'] for a in dc.build_critic_body_anchors(body)};sids={a['id'] for a in dc.source_catalog(sources)}|{a['id'] for a in supplemental.plan_catalog(plan)}
    ids=value['checked_body_ids'];dc.need(isinstance(ids,list) and len(ids)==len(bids) and set(ids)==bids,'source_actions_full_coverage')
    n=len(original['completed_fields']['unknown_issues']);rows=value['issue_decisions']
    dc.need(isinstance(rows,list) and len(rows)==n and {x.get('index') for x in rows}==set(range(n)),'source_actions_native_index_inventory')
    for row in rows:
        dc.need(set(row)=={'index','status','body_ids','source_ids','explanation'} and row['status']=='not_hard_safety_unknown','source_actions_actual_negative_or_unknown')
        dc.need(isinstance(row['body_ids'],list) and row['body_ids'] and len(set(row['body_ids']))==len(row['body_ids']) and set(row['body_ids'])<=bids,'source_actions_native_invalid_body')
        dc.need(isinstance(row['source_ids'],list) and len(set(row['source_ids']))==len(row['source_ids']) and set(row['source_ids'])<=sids,'source_actions_native_invalid_source')
        dc.need(isinstance(row['explanation'],str) and row['explanation'].strip(),'source_actions_native_explanation')
    dc.need(isinstance(value['additional_hard_issues'],list) and all(isinstance(x,str) and x.strip() for x in value['additional_hard_issues']),'source_actions_additional_shape')
    dc.need(value['submission_complete']=='full_body_sources_and_remaining_hard_uncertainty_examined','source_actions_no_completion')
    # This validates completeness of the independent witness, never semantic
    # relevance or a pass. Its wrong indices/citations remain in original data.
    return deepcopy(value)


def derive_actions(*,body,historical_body,correction,sources,plan,original,supplement,actions):
    inspect_complete_supplement(supplement,body=historical_body,sources=sources,plan=plan,original=original)
    dc.need(isinstance(actions,dict) and actions.get('body_sha256')==dc.body_hash(body) and actions.get('source_sha256')==content_hash(sources)
      and actions.get('approved_plan_sha256')==content_hash(plan) and actions.get('original_arguments_sha256')==original['original_arguments_sha256']
      and actions.get('supplement_report_sha256')==content_hash(supplement) and actions.get('unresolved_hard_items')==[],'source_actions_binding_or_unresolved')
    dc.need(original['completed_fields']['body_sha256']==dc.body_hash(historical_body) and original['completed_fields']['source_sha256']==content_hash(sources) and original['completed_fields']['blocking_issues']==[] and all(c['status']=='pass' for c in original['completed_fields']['checks']),'source_actions_nonpositive_historical')
    dc.need(actions.get('acknowledged_historical_hard_violation') is True,'source_actions_historical_violation_not_acknowledged')
    contract=sources.get('chapter_sources',{}).get('acceptance_contract',{})
    dc.need(contract.get('must_deliver')==[],'source_actions_nonempty_obligations_require_distinct_proof')
    dc.need(correction['source_body_sha256']==dc.body_hash(historical_body) and correction['result_body_sha256']==dc.body_hash(body) and historical_body!=body,'source_actions_correction_binding')
    edits=correction.get('edits')
    dc.need(isinstance(edits,list) and len(edits)==1 and set(edits[0])=={'before','after'},'source_actions_single_boundary_edit')
    before,after=edits[0]['before'],edits[0]['after']
    dc.need(isinstance(before,str) and before and isinstance(after,str) and after and '\n' not in before and '\n' not in after
      and historical_body.count(before)==1 and historical_body.replace(before,after)==body,'source_actions_exact_single_paragraph_delta')
    dc.need(actions.get('historical_body_sha256')==dc.body_hash(historical_body) and actions.get('source_correction_kind')=='preserve_explicit_epistemic_unknown','source_actions_correction_kind')
    current_ids={a['id'] for a in dc.build_critic_body_anchors(body)}
    checked=actions.get('checked_current_body_ids')
    dc.need(isinstance(checked,list) and len(checked)==len(current_ids) and set(checked)==current_ids,'source_actions_current_body_coverage')
    anchors={a['id']:a for a in dc.build_critic_body_anchors(body)}
    catalog={a['id']:{**a,'plan':False} for a in dc.source_catalog(sources)}
    catalog.update({a['id']:{**a,'plan':True} for a in supplemental.plan_catalog(plan)})
    groups={'original_diagnostics':original['completed_fields']['unknown_issues'],'supplement_decisions':supplement['issue_decisions'],'supplement_additional':supplement['additional_hard_issues'],'current_axes':list(dc.AXES)}
    dc.need(set(actions.get('groups',{}))==set(groups),'source_actions_group_inventory')
    result={}
    for group,values in groups.items():
        rows=actions['groups'][group]
        dc.need(isinstance(rows,list) and len(rows)==len(values) and {r.get('position') for r in rows}==set(range(len(values))),'source_actions_missing_item')
        resolved=[]
        for row in rows:
            dc.need(row.get('raw_item_sha256')==content_hash(values[row['position']]) and row.get('category') in CATEGORIES
              and row.get('unresolved_hard_items')==[] and row.get('disposition')=='not_a_current_manuscript_hard_violation'
              and isinstance(row.get('reason'),str) and row['reason'].strip(),'source_actions_item_binding_or_unresolved')
            if group=='current_axes':dc.need(row.get('axis')==values[row['position']] and row.get('source_verdict')=='source_verified','source_actions_current_axis_unresolved')
            bproof=[];sproof=[]
            for cite in row.get('body_evidence',[]):
                anchor=anchors.get(cite.get('id'));quote=cite.get('quote')
                dc.need(anchor is not None and isinstance(quote,str) and len(quote)>=8 and quote in anchor['text'],'source_actions_exact_body_quote')
                start=anchor['start']+anchor['text'].index(quote)
                proof={'id':anchor['id'],'quote':quote,'start':start,'end':start+len(quote),'quote_sha256':dc.body_hash(quote),'body_sha256':dc.body_hash(body),'offset_unit':'unicode-codepoint'}
                dc.need(all(k not in cite or cite[k]==proof[k] for k in ('start','end','quote_sha256','body_sha256','offset_unit')),'source_actions_quote_offset')
                bproof.append(proof)
            for cite in row.get('source_evidence',[]):
                selected=catalog.get(cite.get('id'));quote=cite.get('quote')
                dc.need(selected is not None and isinstance(quote,str) and len(quote)>=4 and quote in selected['text'],'source_actions_exact_source_quote')
                kind=source_kind(selected['path'],selected['plan']);dc.need(kind is not None,'source_actions_irrelevant_source_kind')
                dc.need('path' not in cite or cite['path']==selected['path'],'source_actions_source_path')
                sproof.append({'id':selected['id'],'path':selected['path'],'quote':quote,'quote_sha256':dc.body_hash(quote),'source_value_sha256':dc.body_hash(selected['text']),
                  'source_domain':'current_approved_plan' if selected['plan'] else 'original_base_sources','source_domain_sha256':content_hash(plan if selected['plan'] else sources),'source_kind':kind})
            dc.need(bproof and sproof,'source_actions_missing_semantic_evidence')
            if row['category']=='exact_qualified_prior_continuation':dc.need(any(c['source_kind']=='verified_prior_prose_not_formal_canon' for c in sproof),'source_actions_prior_evidence_required')
            if row['category']=='approved_present_task_not_past_history':dc.need(any(c['source_kind']=='approved_future_action_not_past_fact' for c in sproof),'source_actions_action_evidence_required')
            if row['category']=='source_boundary_corrected_in_exact_child':
                dc.need(any(not c['id'].startswith('A') and 'unknown' in c['quote'].casefold() for c in sproof),'source_actions_explicit_unknown_evidence')
                dc.need(any(after in c['quote'] or c['quote'] in after for c in bproof),'source_actions_current_corrected_quote_required')
            negatives=[]
            for check in row.get('absence_checks',[]):
                dc.need(set(check)=={'text','scope','expected_count'} and check['scope']=='body' and isinstance(check['text'],str) and len(check['text'])>=2 and check['expected_count']==0,'source_actions_absence_shape')
                dc.need(body.count(check['text'])==0,'source_actions_asserted_absence_false')
                negatives.append({**check,'actual_count':0,'body_sha256':dc.body_hash(body)})
            resolved.append({**deepcopy(row),'body_evidence':bproof,'source_evidence':sproof,'absence_checks':negatives,'semantic_classification_authority':'explicit_trusted_operator_source_review_not_native_ID_existence'})
        result[group]=resolved
    dc.need(any(row['category']=='source_boundary_corrected_in_exact_child' for rows in result.values() for row in rows),'source_actions_boundary_not_reviewed')
    return {'schema':POLICY,'groups':result,'unresolved_hard_items':[],'original_submission_still_incomplete':True,
      'supplement_native_indices_and_comments_unmodified':deepcopy(supplement),'raw_native_grade_changed':False,'models_reviewed_only_historical_body':True,'current_complete_source_review_is_operator_action':True,'historical_body_sha256':dc.body_hash(historical_body),'current_body_sha256':dc.body_hash(body),'correction_plan_sha256':correction['plan_sha256'],'original_partial_observations_sha256':content_hash(original),
      'qualification_basis':'separately_approved_per_item_exact_source_actions_plus_complete_independent_witness_not_a_repaired_model_report'}


def verify_http_return(wire,actual,arguments):
    choices=wire['choices']
    dc.need(isinstance(choices,list) and len(choices)==1,'source_actions_http_choice_count')
    wire_calls=choices[0]['message']['tool_calls'];sdk_calls=actual.tool_calls
    dc.need(isinstance(wire_calls,list) and len(wire_calls)==1 and isinstance(sdk_calls,list) and len(sdk_calls)==1,'source_actions_http_tool_count')
    wire_tool=wire_calls[0];sdk_tool=sdk_calls[0]
    keys={'id','type','function'}
    dc.need(set(wire_tool) in (keys,keys|{'index'}) and set(sdk_tool)==keys,'source_actions_http_tool_fields')
    if 'index' in wire_tool:dc.need(type(wire_tool['index']) is int and wire_tool['index']==0,'source_actions_http_transport_index')
    # A fresh comparison projection, never an edit to the raw HTTP envelope,
    # internal model JSON, diagnostic indices, citations or paid model grade.
    projected={key:wire_tool[key] for key in keys}
    dc.need(projected==sdk_tool and wire_tool['function']['arguments']==arguments
      and choices[0]['finish_reason']==actual.finish_reason and wire['usage']['prompt_tokens']==actual.input_tokens
      and wire['usage']['completion_tokens']==actual.output_tokens,'source_actions_full_http_return')
    return {'schema':'single-tool-http-index-projection.v1','wire_index':wire_tool.get('index'),'index_present':'index' in wire_tool,'id_type_function_arguments_equal':True,'raw_http_rewritten':False}


async def raw_witnesses(session,settings,*,project,chapter,draft,receipt):
    historical=await session.get(m.ChapterDraftVersionModel,UUID(receipt['historical_draft_id']))
    dc.need(historical is not None and historical.project_id==project.id and historical.chapter_id==chapter.id and historical.id!=draft.id,'source_actions_historical_scope')
    edit_row=await session.get(m.WorkflowRunModel,UUID(receipt['edit_receipt_id']),populate_existing=True)
    dc.need(edit_row is not None and edit_row.workflow_type=='explicit_draft_text_edit' and edit_row.status=='completed' and edit_row.project_id==project.id and edit_row.scope_id==chapter.id,'source_actions_edit_receipt')
    correction=edit_row.metadata_json['plan'];edited=edit_row.metadata_json['result']
    dc.need(correction['source_draft_id']==str(historical.id) and correction['source_body_sha256']==dc.body_hash(historical.content_md)
      and correction['result_content_md']==draft.content_md and correction['result_body_sha256']==dc.body_hash(draft.content_md) and edited['candidate_id']==str(draft.id)
      and draft.promotion_metadata.get('text_edit_plan_sha256')==correction['plan_sha256'] and draft.promotion_metadata.get('parent_draft_id')==str(historical.id),'source_actions_exact_edit_lineage')
    from bestseller.services.operator_action_approval import require_operator_action_approval
    await require_operator_action_approval(session,approval_id=edit_row.metadata_json['approval_id'],purpose='explicit_draft_text_edit',binding=correction,capability='apply_exact_text_edits')
    sources=await dc.current_sources(session,settings,project=project,chapter=chapter)
    plan=await supplemental.applicable_plan(session,settings,project=project,chapter=chapter)
    original,old_physical=await supplemental.original_evidence(session,settings,project=project,chapter=chapter,draft=historical,sources=sources,evidence=receipt['original_evidence'])
    for label,evidence in [('original',receipt['original_evidence']),('supplement',receipt['supplement_evidence'])]:
        workflow=await session.get(m.WorkflowRunModel,UUID(evidence['workflow_id']),populate_existing=True)
        dc.need(workflow is not None and workflow.project_id==project.id and workflow.scope_id==chapter.id and workflow.status=='failed'
          and content_hash(workflow.metadata_json)==receipt['failed_workflow_sha256'][label],'source_actions_original_failed_workflow_changed')
    new=receipt['supplement_evidence']
    request,paid,new_physical=await supplemental.physical_result(session,settings,project=project,run_id=new['run_id'],workflow_id=new['workflow_id'])
    expected=supplemental.request_for(project=project,workflow_id=UUID(new['workflow_id']),draft=historical,sources=sources,original=original,grant_id=new['grant_id'],approved_plan=plan)
    from bestseller.services import retained_review_input_proof as physical
    catalog,_=physical._fresh_catalog();role=physical._fresh_role(settings,project,expected,catalog)
    keys=('logical_role','project_id','workflow_run_id','step_run_id','prompt_template','prompt_version','system_prompt','user_prompt','tools','tool_choice','model_tier','model_catalog_key','temperature_override','max_tokens_override','cache_system','messages_override')
    dc.need(all(getattr(request,k)==getattr(expected,k) for k in keys) and llm_hash(expected,role)==new_physical['input_hash'],'source_actions_paid_request_changed')
    scope=project.metadata_json['execution_scope_id'];key=hashlib.sha256((scope+'\n'+expected.operation_id).encode()).hexdigest()
    ledger=await session.get(m.ExecutionBudgetModel,scope,populate_existing=True)
    grant=(ledger.metadata_json or {}).get('single_dispatch_grants',{}).get(new['grant_id'])
    dc.need(isinstance(grant,dict) and grant.get('status')=='spent' and grant.get('physical_attempt_cap')==1 and new_physical['actual_attempt_ids']==[grant.get('used_attempt_id')],'source_actions_paid_one_send_witness')
    binding=grant['binding']
    dc.need(new_physical['physical_receipt']==key and binding['operation_key']==key and binding['scope_id']==scope and binding['input_hash']==new_physical['input_hash']
      and binding['workflow_id']==str(expected.workflow_run_id) and binding['model']==new_physical['model'] and binding['endpoint']==role.api_base
      and binding['template']==expected.prompt_template and binding['version_prefix']==expected.prompt_version and binding.get('dispatch_scope')=='workflow','source_actions_grant_binding')
    arguments=supplemental.arguments_of(paid);dc.need(dc.body_hash(arguments)==new['arguments_sha256'],'source_actions_paid_arguments_changed')
    report=inspect_complete_supplement(strict_json(arguments),body=historical.content_md,sources=sources,plan=plan,original=original)
    # Verify the physical HTTP envelopes independently of the retained SDK/cache.
    root=Path(settings.artifact_store.local_dir).expanduser().resolve();http=[]
    for kind,run_evidence in [('original',receipt['original_evidence']),('supplement',new)]:
        req,actual,physical_data=await supplemental.physical_result(session,settings,project=project,run_id=run_evidence['run_id'],workflow_id=run_evidence['workflow_id'])
        ref=receipt['http_witnesses'][kind];path=(root/ref['ref']).resolve();dc.need(path.is_relative_to(root),'source_actions_http_path')
        data=path.read_bytes();dc.need(hashlib.sha256(data).hexdigest()==ref['sha256'],'source_actions_http_hash')
        wire=strict_json(data);args=dc.native_arguments(actual) if kind=='original' else supplemental.arguments_of(actual)
        normalization=verify_http_return(wire,actual,args)
        http.append({'kind':kind,'http_body_sha256':ref['sha256'],'actual_input_tokens':actual.input_tokens,'actual_output_tokens':actual.output_tokens,'physical':physical_data,'transport_projection':normalization})
    return sources,plan,original,report,historical.content_md,correction,{'source_correction':{'edit_receipt_id':str(edit_row.id),'plan_sha256':correction['plan_sha256'],'original_body_sha256':dc.body_hash(historical.content_md),'current_body_sha256':dc.body_hash(draft.content_md),'current_body_model_grade_asserted':False},'original':old_physical,'supplement':new_physical,'http':http,'actual_capacity_acceptance_claimed':False}


def llm_hash(request,role):
    from bestseller.services.llm import _effective_input_hash
    return _effective_input_hash(request,role)


async def qualify_reference(session,settings,*,project,chapter,draft,receipt,actions,actor,authorization_ref,explicit_user_approval=False):
    from bestseller.services.operator_action_approval import record_operator_action_approval
    from bestseller.services.drafts import _evaluate_chapter_quality_gate,_collect_post_assembly_duplicate_findings
    dc.need(explicit_user_approval is True and actor and authorization_ref,'source_actions_explicit_approval')
    dc.need(chapter.project_id==project.id and draft.project_id==project.id and draft.chapter_id==chapter.id,'source_actions_scope')
    await session.execute(select(m.ChapterModel.id).where(m.ChapterModel.id==chapter.id).with_for_update())
    await session.refresh(project);await session.refresh(chapter);await session.refresh(draft)
    sources,plan,original,report,historical_body,correction,physical=await raw_witnesses(session,settings,project=project,chapter=chapter,draft=draft,receipt=receipt)
    derived=derive_actions(body=draft.content_md,historical_body=historical_body,correction=correction,sources=sources,plan=plan,original=original,supplement=report,actions=actions)
    prior=chapter.metadata_json.get(dc.POINTER)
    if prior:
        selected=await dc.select_reference(session,settings,project=project,chapter=chapter)
        row=await session.get(m.WorkflowRunModel,UUID(prior));proof=row.metadata_json['proof']
        dc.need(selected[0].id==draft.id and proof.get('schema')==POLICY and proof['receipt']==receipt and proof['actions']==actions,'source_actions_existing_conflict')
        return {'receipt_id':prior,'draft_id':str(draft.id),'already_qualified':True,'formal_adoption':False}
    tag={'evaluation_id':str(uuid4()),'draft_id':str(draft.id),'body_sha256':dc.body_hash(draft.content_md),'source_sha256':content_hash(sources)}
    outcome=await _evaluate_chapter_quality_gate(session=session,project=project,chapter_number=chapter.chapter_number,content=draft.content_md,register_diversity=False,extra_report_payload={'draft_continuity_hard_safety':tag})
    duplicates=await _collect_post_assembly_duplicate_findings(session,project=project,chapter=chapter,content_md=draft.content_md)
    dc.need(outcome=='ok' and not duplicates,'source_actions_deterministic_not_positive')
    rows=list(await session.scalars(select(m.ChapterQualityReportModel).where(m.ChapterQualityReportModel.chapter_id==chapter.id).order_by(m.ChapterQualityReportModel.created_at.desc()).limit(10)))
    qr=next(r for r in rows if r.report_json.get('draft_continuity_hard_safety')==tag)
    proof={'schema':POLICY,'adjudicator_source_sha256':policy_hash(),'project_id':str(project.id),'chapter_id':str(chapter.id),'draft_id':str(draft.id),'body_sha256':dc.body_hash(draft.content_md),
      'source_sha256':content_hash(sources),'approved_plan_sha256':content_hash(plan),'receipt':receipt,'actions':deepcopy(actions),'source_adjudication':derived,'physical_evidence':physical,
      'hard_policy_sha256':dc.hard_policy_hash(),'deterministic_outcome':outcome,'quality_report_id':str(qr.id),'deterministic_binding':tag,'originals_remain_failed':True,'new_body_model_grade_asserted':False,'current_body_eligibility_basis':'complete independently approved exact source review and controlled epistemic correction',
      'formal_adoption':False,'canon_authority':False,'final_export_eligible':False}
    identity=uuid5(NAMESPACE_URL,POLICY+':'+content_hash(proof));binding={'project_id':str(project.id),'target_id':str(identity),'proof_sha256':content_hash(proof),'draft_id':str(draft.id),'body_sha256':proof['body_sha256'],'source_sha256':proof['source_sha256'],'formal_adoption':False}
    approval=await record_operator_action_approval(session,purpose='draft_continuity_reference',binding=binding,capabilities=['reference_pending_manuscript'],actor=actor,authorization_ref=authorization_ref,explicit_user_approval=True)
    session.add(m.WorkflowRunModel(id=identity,project_id=project.id,workflow_type='draft_continuity_reference',scope_type='chapter',scope_id=chapter.id,status='completed',current_step='pending_literary_review',requested_by=actor,metadata_json={'binding':binding,'approval_id':str(approval),'proof':proof}))
    chapter.metadata_json={**chapter.metadata_json,dc.POINTER:str(identity)};await session.flush()
    return {'receipt_id':str(identity),'draft_id':str(draft.id),'body_sha256':proof['body_sha256'],'state':'pending_literary_review','formal_adoption':False,'literary_grade_modified':False}


async def validate_reference(session,settings,*,project,chapter,draft,proof,_memo=None):
    dc.need(proof['schema']==POLICY and proof['adjudicator_source_sha256']==policy_hash(),'source_actions_policy_changed')
    sources,plan,original,report,historical_body,correction,physical=await raw_witnesses(session,settings,project=project,chapter=chapter,draft=draft,receipt=proof['receipt'])
    dc.need(proof['project_id']==str(project.id) and proof['chapter_id']==str(chapter.id) and proof['draft_id']==str(draft.id)
      and proof['body_sha256']==dc.body_hash(draft.content_md) and proof['source_sha256']==content_hash(sources) and proof['approved_plan_sha256']==content_hash(plan),'source_actions_reference_binding')
    dc.need(physical==proof['physical_evidence'] and derive_actions(body=draft.content_md,historical_body=historical_body,correction=correction,sources=sources,plan=plan,original=original,supplement=report,actions=proof['actions'])==proof['source_adjudication'],'source_actions_reference_evidence')
    await dc._verify_deterministic_receipt(session,draft=draft,proof=proof)
