"""Compose an approved earlier source transition with exact new reference dispatch.

Old raw reviews, action receipts, bodies and reference IDs never change. The
only additional active code is an explicit new schema branch plus manifest
entries; all prior parser/qualification branches must remain AST identical.
"""
import ast
from copy import deepcopy
from pathlib import Path
from uuid import UUID, uuid4
from sqlalchemy import select
from bestseller.infra.db import models as m
from bestseller.services import draft_continuity as dc,draft_continuity_revalidation as old_refresh
from bestseller.services.prompt_source_policy import content_hash

NEW_FILES={'src/bestseller/services/draft_continuity_source_actions.py','src/bestseller/services/draft_continuity_supplement.py','src/bestseller/services/draft_continuity_reference_transition.py'}
OWNED={'src/bestseller/services/draft_continuity.py','src/bestseller/services/pipelines.py'}


def verify_dispatch_delta(*,baseline_manifest,baseline_sources,current_root):
    current=old_refresh.policy_manifest(current_root)
    changed={p for p in set(baseline_manifest)|set(current) if baseline_manifest.get(p)!=current.get(p)}
    if 'src/bestseller/services/foundation_catalog_transition.py' in changed:
        return _verify_foundation_catalog_delta(baseline_manifest=baseline_manifest,baseline_sources=baseline_sources,current_root=current_root)
    if changed == {'src/bestseller/services/retained_review_input_proof.py','src/bestseller/services/draft_continuity_reference_transition.py'}:
        return _verify_historical_route_delta(baseline_manifest=baseline_manifest,baseline_sources=baseline_sources,current_root=current_root)
    if changed == {
        'src/bestseller/services/draft_continuity.py',
        'src/bestseller/services/draft_continuity_reference_transition.py',
        'src/bestseller/services/pipelines.py',
        'src/bestseller/services/book_reference_source_transition.py',
        'src/bestseller/services/book_source_review.py',
        'src/bestseller/services/book_continuity_index.py',
    }:
        from bestseller.services.book_reference_source_transition import verify_additive_dispatch
        return verify_additive_dispatch(baseline_manifest=baseline_manifest, baseline_sources=baseline_sources, current_root=current_root)
    current_task_delta={'src/bestseller/services/draft_continuity.py',
        'src/bestseller/services/chapter_writer_delivery.py',
        'src/bestseller/services/draft_continuity_reference_transition.py'}
    if changed==current_task_delta:
        for name in changed:
            dc.need(old_refresh.sha(baseline_sources[name].encode())==baseline_manifest[name], 'reference_transition_old_source_witness')
        _verify_runtime_memo_and_task_delta(baseline_sources=baseline_sources,current_root=current_root)
        return current,{'schema':'exact-source-memo-and-chapter-task-transition.v1',
            'baseline_policy_sha256':old_refresh.manifest_hash(baseline_manifest),
            'current_policy_sha256':old_refresh.manifest_hash(current),
            'baseline_manifest_sha256':content_hash(baseline_manifest),'current_manifest_sha256':content_hash(current),
            'changed_paths':sorted(changed),'old_eligibility_parser_and_source_rules_unchanged':True,
            'cache_lifetime':'one top-level selection; no cross-entry, transaction or paid reuse'}
    dc.need(changed==NEW_FILES|OWNED,'reference_transition_unrelated_policy_change')
    for name in OWNED:
        dc.need(old_refresh.sha(baseline_sources[name].encode())==baseline_manifest[name],'reference_transition_old_source_witness')
        before=ast.parse(baseline_sources[name]);after=ast.parse((current_root/name).read_text())
        if name.endswith('/draft_continuity.py'):
            fn=next(n for n in after.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='select_reference')
            expected=ast.parse("if proof.get('schema')=='draft-continuity-source-corrected-reference.v1':\n from bestseller.services.draft_continuity_source_actions import validate_reference\n await validate_reference(session,settings,project=project,chapter=chapter,draft=draft,proof=proof,_memo=_memo)\nelse:\n pass").body[0]
            matches=[n for n in fn.body if isinstance(n,ast.If) and ast.dump(n.test)==ast.dump(expected.test)]
            dc.need(len(matches)==1,'reference_transition_exact_schema')
            branch=matches[0]
            dc.need(ast.dump(ast.Module(body=branch.body,type_ignores=[]))==ast.dump(ast.Module(body=expected.body,type_ignores=[])) and len(branch.orelse)==1 and isinstance(branch.orelse[0],ast.If),'reference_transition_exact_dispatch')
            fn.body[fn.body.index(branch)]=branch.orelse[0]
        else:
            fn=next(n for n in after.body if isinstance(n,ast.FunctionDef) and n.name=='chapter_review_recovery_source_hash')
            values=next(n.iter for n in ast.walk(fn) if isinstance(n,ast.comprehension) and isinstance(n.target,ast.Name) and n.target.id=='name')
            names={Path(p).name for p in NEW_FILES};added=[n for n in values.elts if isinstance(n,ast.Constant) and n.value in names]
            dc.need(len(added)==len(names) and {n.value for n in added}==names,'reference_transition_exact_manifest_additions')
            values.elts=[n for n in values.elts if n not in added]
        dc.need(ast.dump(before)==ast.dump(after),'reference_transition_old_rules_changed')
    return current,{'schema':'exact-added-reference-policy-transition.v1','baseline_policy_sha256':old_refresh.manifest_hash(baseline_manifest),'current_policy_sha256':old_refresh.manifest_hash(current),
      'baseline_manifest_sha256':content_hash(baseline_manifest),'current_manifest_sha256':content_hash(current),'changed_paths':sorted(changed),'old_eligibility_parser_and_source_rules_unchanged':True}


async def refresh_reference(session,settings,*,project,chapter,reference_id,baseline_manifest,baseline_sources,actor,authorization_ref,explicit_user_approval=False):
    from bestseller.services.retained_review_input_proof import historical_reference_route_scope
    async with historical_reference_route_scope(session,settings,project=project,chapter=chapter):
        return await _refresh_reference_without_operation_scope(session,settings,project=project,chapter=chapter,
            reference_id=reference_id,baseline_manifest=baseline_manifest,baseline_sources=baseline_sources,
            actor=actor,authorization_ref=authorization_ref,explicit_user_approval=explicit_user_approval)


async def _refresh_reference_without_operation_scope(session,settings,*,project,chapter,reference_id,baseline_manifest,baseline_sources,actor,authorization_ref,explicit_user_approval=False):
    from bestseller.services.operator_action_approval import require_operator_action_approval,record_operator_action_approval
    from bestseller.services import draft_continuity_adjudication as legacy,draft_continuity_axis_adjudication as axis,draft_continuity_source_actions as corrected
    from bestseller.services.drafts import _evaluate_chapter_quality_gate,_collect_post_assembly_duplicate_findings
    from bestseller.services.quality_report_claim import graded_text_fingerprint
    dc.need(explicit_user_approval is True and actor and authorization_ref,'reference_transition_explicit_authority')
    row=await session.get(m.WorkflowRunModel,reference_id,populate_existing=True)
    dc.need(row is not None and row.project_id==project.id and row.scope_id==chapter.id and row.workflow_type=='draft_continuity_reference' and row.status=='completed','reference_transition_original_reference')
    original=deepcopy(row.metadata_json);proof=original['proof']
    validator=legacy if proof.get('schema')==legacy.POLICY else axis if proof.get('schema')==axis.POLICY else corrected if proof.get('schema')==corrected.POLICY else None
    dc.need(validator is not None and proof['adjudicator_source_sha256']==validator.policy_hash() and original['binding']['proof_sha256']==content_hash(proof),'reference_transition_old_review_policy')
    expected_original_binding={'project_id':str(project.id),'target_id':str(row.id),'proof_sha256':content_hash(proof),'draft_id':proof['draft_id'],'body_sha256':proof['body_sha256'],'source_sha256':proof['source_sha256'],'formal_adoption':False}
    dc.need(original['binding']==expected_original_binding,'reference_transition_original_approval_binding')
    await require_operator_action_approval(session,approval_id=UUID(original['approval_id']),purpose='draft_continuity_reference',binding=original['binding'],capability='reference_pending_manuscript')
    draft=await session.get(m.ChapterDraftVersionModel,UUID(proof['draft_id']))
    dc.need(draft.project_id==project.id and draft.chapter_id==chapter.id and dc.body_hash(draft.content_md)==proof['body_sha256'],'reference_transition_original_body')
    sources=await dc.current_sources(session,settings,project=project,chapter=chapter)
    dc.need(content_hash(sources)==proof['source_sha256'],'reference_transition_original_sources')
    baseline=old_refresh.manifest_hash(baseline_manifest)
    previous=None
    if proof['hard_policy_sha256']==baseline:
        child=proof
        prior_identity=str(row.id)
    else:
        previous=await session.get(m.WorkflowRunModel,old_refresh.identity(proof,baseline),populate_existing=True)
        dc.need(previous is not None and previous.project_id==project.id and previous.scope_id==chapter.id and previous.workflow_type=='draft_continuity_deterministic_revalidation' and previous.status=='completed','reference_transition_prior_approved_child_missing')
        prior=previous.metadata_json;child=prior['proof']
        dc.need(child['schema']==old_refresh.SCHEMA and child['original_reference_id']==str(row.id) and child['original_proof_sha256']==content_hash(proof) and child['hard_policy_sha256']==baseline
          and child['draft_id']==str(draft.id) and child['body_sha256']==proof['body_sha256'] and child['source_sha256']==proof['source_sha256']
          and child['source_transition']['old_policy_sha256']==proof['hard_policy_sha256'] and child['source_transition']['current_policy_sha256']==baseline
          and child['source_transition']['substantive_old_review_rules_unchanged'] is True,'reference_transition_prior_child_binding')
        expected_prior_binding={'project_id':str(project.id),'target_id':str(previous.id),'proof_sha256':content_hash(child),'draft_id':str(draft.id),'body_sha256':proof['body_sha256'],'source_sha256':proof['source_sha256'],'formal_adoption':False}
        dc.need(prior['binding']==expected_prior_binding,'reference_transition_prior_approval_binding')
        await require_operator_action_approval(session,approval_id=UUID(prior['approval_id']),purpose='draft_continuity_reference',binding=prior['binding'],capability='reference_pending_manuscript')
        dc.need(prior['binding']['proof_sha256']==content_hash(child),'reference_transition_prior_child_approval')
        prior_identity=str(previous.id)
    prior_qr=await session.get(m.ChapterQualityReportModel,UUID(child['quality_report_id']))
    dc.need(prior_qr is not None and not prior_qr.blocks_write and prior_qr.chapter_id==chapter.id
      and prior_qr.report_json.get('graded')==graded_text_fingerprint(draft.content_md)
      and prior_qr.report_json.get('draft_continuity_hard_safety')==child['deterministic_binding']
      and child['deterministic_outcome']=='ok' and prior_qr.report_json.get('blocking_codes')==[], 'reference_transition_prior_positive_report')
    current_manifest,delta=verify_dispatch_delta(baseline_manifest=baseline_manifest,baseline_sources=baseline_sources,current_root=Path(__file__).resolve().parents[3])
    current=dc.hard_policy_hash();dc.need(old_refresh.manifest_hash(current_manifest)==current,'reference_transition_current_policy')
    if validator is corrected:
        from bestseller.services.chapter_writer_delivery import reference_source_delivery_scope
        async with reference_source_delivery_scope(session,settings,project=project,proof=proof):
            retained_sources,plan,raw,report,historical_body,correction,physical=await corrected.raw_witnesses(
                session,settings,project=project,chapter=chapter,draft=draft,receipt=proof['receipt'])
        dc.need(retained_sources==sources, 'reference_transition_corrected_source_changed')
        action=corrected.derive_actions(body=draft.content_md,historical_body=historical_body,correction=correction,
            sources=sources,plan=plan,original=raw,supplement=report,actions=proof['actions'])
    else:
        raw,physical=await legacy.verify_paid_evidence(session,settings,project=project,chapter=chapter,draft=draft,sources=sources,evidence=proof['native_evidence'])
        action=(legacy.derive_axis_evidence(raw,body=draft.content_md,sources=sources,decisions=proof['decisions']) if validator is legacy else axis.derive_actions(raw,body=draft.content_md,sources=sources,axis_action=proof['axis_action'],decisions=proof['decisions']))
    dc.need(physical==proof['physical_evidence'] and action==proof['source_adjudication'],'reference_transition_original_physical_or_actions_changed')
    identity=old_refresh.identity(proof,current)
    if await session.get(m.WorkflowRunModel,identity) is not None:
        await dc._verify_deterministic_receipt(session,draft=draft,proof=proof)
        return {'id':str(identity),'reused':True,'original_reference_id':str(row.id)}
    tag={'evaluation_id':str(uuid4()),'draft_id':str(draft.id),'body_sha256':proof['body_sha256'],'source_sha256':proof['source_sha256']}
    outcome=await _evaluate_chapter_quality_gate(session=session,project=project,chapter_number=chapter.chapter_number,content=draft.content_md,register_diversity=False,extra_report_payload={'draft_continuity_hard_safety':tag})
    duplicates=await _collect_post_assembly_duplicate_findings(session,project=project,chapter=chapter,content_md=draft.content_md)
    dc.need(outcome=='ok' and not duplicates,'reference_transition_current_deterministic_not_positive')
    reports=list(await session.scalars(select(m.ChapterQualityReportModel).where(m.ChapterQualityReportModel.chapter_id==chapter.id).order_by(m.ChapterQualityReportModel.created_at.desc()).limit(10)))
    qr=next(q for q in reports if q.report_json.get('draft_continuity_hard_safety')==tag)
    transition={'schema':'composed-explicit-reference-transition.v1','old_policy_sha256':proof['hard_policy_sha256'],'current_policy_sha256':current,'substantive_old_review_rules_unchanged':True,
      'prior_trusted_transition_id':prior_identity,'prior_transition_proof_sha256':content_hash(child),'current_exact_dispatch_delta':delta,'original_grade_relabelled':False}
    fresh={'schema':old_refresh.SCHEMA,'original_reference_id':str(row.id),'original_proof_sha256':content_hash(proof),'draft_id':str(draft.id),'body_sha256':proof['body_sha256'],
      'source_sha256':proof['source_sha256'],'hard_policy_sha256':current,'quality_report_id':str(qr.id),'deterministic_outcome':outcome,'deterministic_binding':tag,'source_transition':transition,'new_model_calls':0,'original_paid_or_grade_changed':False}
    binding={'project_id':str(project.id),'target_id':str(identity),'proof_sha256':content_hash(fresh),'draft_id':str(draft.id),'body_sha256':fresh['body_sha256'],'source_sha256':fresh['source_sha256'],'formal_adoption':False}
    approval=await record_operator_action_approval(session,purpose='draft_continuity_reference',binding=binding,capabilities=['reference_pending_manuscript'],actor=actor,authorization_ref=authorization_ref,explicit_user_approval=True)
    session.add(m.WorkflowRunModel(id=identity,project_id=project.id,workflow_type='draft_continuity_deterministic_revalidation',scope_type='chapter',scope_id=chapter.id,status='completed',current_step='current_deterministic_verified',requested_by=actor,metadata_json={'proof':fresh,'binding':binding,'approval_id':str(approval)}))
    await session.flush();dc.need(row.metadata_json==original and chapter.metadata_json[dc.POINTER]==str(row.id),'reference_transition_original_mutated')
    return {'id':str(identity),'reused':False,'original_reference_id':str(row.id),'proof':fresh}


def _verify_runtime_memo_and_task_delta(*, baseline_sources, current_root):
    """Invert only per-selection source memo plumbing and future-task delivery.

    No historical parser, source projection, grading condition, authorization or
    model request constructor is allowed to change under this transition.
    """
    name='src/bestseller/services/draft_continuity.py'
    before=ast.parse(baseline_sources[name]);after=ast.parse((current_root/name).read_text())
    for code in ('from contextvars import ContextVar','from asyncio import current_task',
        "_REFERENCE_SOURCE_MEMO = ContextVar('bestseller_reference_source_memo', default=None)"):
        expected=ast.parse(code).body[0]
        matches=[n for n in after.body if ast.dump(n)==ast.dump(expected)]
        dc.need(len(matches)==1,'source_memo_exact_declaration');after.body.remove(matches[0])
    wrapper=ast.parse("""async def select_reference(session,settings,*,project,chapter,_memo=None):
    memo={} if _memo is None else _memo
    token=_REFERENCE_SOURCE_MEMO.set((session,project.id,memo,current_task()))
    try:
        return await _select_reference_without_source_scope(session,settings,project=project,chapter=chapter,_memo=memo)
    finally:
        _REFERENCE_SOURCE_MEMO.reset(token)
""").body[0]
    actual=next(n for n in after.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='select_reference')
    dc.need(ast.dump(actual)==ast.dump(wrapper),'source_memo_exact_scope');after.body.remove(actual)
    inner=next(n for n in after.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_select_reference_without_source_scope')
    inner.name='select_reference'
    historical=next(n for n in inner.body if isinstance(n,ast.If)
        and ast.dump(n.test)==ast.dump(ast.parse("proof.get('schema')=='draft-continuity-source-corrected-reference.v1'",mode='eval').body))
    expected=ast.parse('''from bestseller.services.draft_continuity_source_actions import validate_reference
from bestseller.services.chapter_writer_delivery import reference_source_delivery_scope
async with reference_source_delivery_scope(session,settings,project=project,proof=proof):
    await validate_reference(session,settings,project=project,chapter=chapter,draft=draft,proof=proof,_memo=_memo)
''').body
    dc.need(ast.dump(ast.Module(body=historical.body,type_ignores=[]))==ast.dump(ast.Module(body=expected,type_ignores=[])), 'source_memo_exact_historical_delivery_scope')
    historical.body=[historical.body[0],historical.body[-1].body[0]]
    source=next(n for n in after.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='current_sources')
    snippet=ast.parse("""active=_REFERENCE_SOURCE_MEMO.get()
if _memo is None and active is not None and active[0] is session and active[1]==project.id and active[3] is current_task():
    _memo=active[2]
""").body
    matches=[i for i in range(len(source.body)-len(snippet)+1) if ast.dump(ast.Module(body=source.body[i:i+len(snippet)],type_ignores=[]))==ast.dump(ast.Module(body=snippet,type_ignores=[]))]
    dc.need(len(matches)==1,'source_memo_exact_forwarding');del source.body[matches[0]:matches[0]+len(snippet)]
    dc.need(ast.dump(before)==ast.dump(after),'source_memo_substantive_rules_changed')
    name='src/bestseller/services/chapter_writer_delivery.py'
    before=ast.parse(baseline_sources[name]);after=ast.parse((current_root/name).read_text())
    for code in ('from contextlib import asynccontextmanager','from contextvars import ContextVar','from asyncio import current_task',
            "_REFERENCE_DELIVERY_SCHEMA = ContextVar('bestseller_reference_delivery_schema', default=None)"):
        expected=ast.parse(code).body[0]
        matches=[n for n in after.body if ast.dump(n)==ast.dump(expected)]
        dc.need(len(matches)==1,'delivery_exact_protocol_declaration');after.body.remove(matches[0])
    legacy=next(n for n in after.body if isinstance(n,ast.FunctionDef) and n.name=='_source_protocol_delivery_block_v1')
    witness=deepcopy(legacy);witness.name='delivery_block'
    old_fn=next(n for n in before.body if isinstance(n,ast.FunctionDef) and n.name=='delivery_block')
    dc.need(ast.dump(witness)==ast.dump(old_fn),'delivery_legacy_protocol_rewritten');after.body.remove(legacy)
    scope=next(n for n in after.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='reference_source_delivery_scope')
    dc.need(content_hash(ast.dump(scope))=='48f42f28db2f4f4beadf248292e2f95db753af87b0bc9aa46ba495de6a7db938','delivery_exact_retained_protocol_selector')
    after.body.remove(scope)
    loader=next(n for n in after.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='load_delivery_blocks')
    expected=ast.parse('''selected=_REFERENCE_DELIVERY_SCHEMA.get()
constructor=(_source_protocol_delivery_block_v1 if selected is not None
    and selected[0] is current_task() and selected[1]=='approved-current-chapter-delivery.v1'
    and selected[2]==project.id and selected[3]==chapter.id else delivery_block)
''').body
    matches=[i for i in range(len(loader.body)-len(expected)+1) if ast.dump(ast.Module(body=loader.body[i:i+len(expected)],type_ignores=[]))==ast.dump(ast.Module(body=expected,type_ignores=[]))]
    dc.need(len(matches)==1,'delivery_exact_protocol_dispatch');del loader.body[matches[0]:matches[0]+len(expected)]
    constructors=[n for n in ast.walk(loader) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='constructor']
    dc.need(len(constructors)==1,'delivery_exact_constructor');constructors[0].func.id='delivery_block'
    fn=next(n for n in after.body if isinstance(n,ast.FunctionDef) and n.name=='delivery_block')
    payload=next(n.value for n in fn.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='payload' for t in n.targets))
    index=next(i for i,k in enumerate(payload.keys) if isinstance(k,ast.Constant) and k.value=='chapter_contract')
    expected=ast.parse("{key:deepcopy(value) for key,value in approved.items() if key not in {'scenes','_meta'}}",mode='eval').body
    dc.need(ast.dump(payload.values[index])==ast.dump(expected),'delivery_exact_chapter_fields')
    payload.keys.pop(index);payload.values.pop(index)
    index=next(i for i,k in enumerate(payload.keys) if isinstance(k,ast.Constant) and k.value=='schema')
    dc.need(isinstance(payload.values[index],ast.Constant) and payload.values[index].value=='approved-current-chapter-delivery.v2','delivery_schema')
    payload.values[index].value='approved-current-chapter-delivery.v1'
    instruction=next(n.value for n in fn.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='instruction' for t in n.targets))
    extra=('章级目标、开场时点和因果选择与场景动作共同构成本章任务，不能只执行场景而丢失章级条件。'
        '前章原文中的今夜、明日等相对日期属于前章时点，不得照搬为本章现在；以本章开场及有序动作推进。')
    dc.need(isinstance(instruction,ast.Constant) and instruction.value.count(extra)==1,'delivery_exact_temporal_instruction')
    instruction.value=instruction.value.replace(extra,'')
    call=next(n for n in ast.walk(fn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='PromptBlock')
    source=next(k for k in call.keywords if k.arg=='source')
    dc.need(isinstance(source.value,ast.Constant) and source.value.value=='chapter_writer_delivery.v2','delivery_source_version')
    source.value.value='chapter_writer_delivery.v1'
    provenance=next(n for n in ast.walk(fn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='PromptProvenance')
    paths=next(k for k in provenance.keywords if k.arg=='field_paths')
    expected=ast.parse("tuple('/'+key for key in approved if key not in {'scenes','_meta'})",mode='eval').body
    dc.need(isinstance(paths.value,ast.BinOp) and isinstance(paths.value.op,ast.Add) and ast.dump(paths.value.left)==ast.dump(expected),'delivery_exact_provenance')
    paths.value=paths.value.right
    dc.need(ast.dump(before)==ast.dump(after),'delivery_other_source_or_instruction_changed')


def _verify_historical_route_delta(*,baseline_manifest,baseline_sources,current_root):
    """Invert only exact historical verification-route binding additions."""
    name='src/bestseller/services/retained_review_input_proof.py'
    transition='src/bestseller/services/draft_continuity_reference_transition.py'
    current=old_refresh.policy_manifest(current_root)
    changed={p for p in set(current)|set(baseline_manifest) if current.get(p)!=baseline_manifest.get(p)}
    dc.need(changed=={name,transition},'historical_route_unrelated_policy_change')
    for path in (name,transition):
        dc.need(old_refresh.sha(baseline_sources[path].encode())==baseline_manifest[path],'historical_route_original_source_witness')
    before=ast.parse(baseline_sources[name]);after=ast.parse((current_root/name).read_text())
    for code in ('from contextvars import ContextVar','from asyncio import current_task'):
        node=ast.parse(code).body[0]
        found=[n for n in after.body if ast.dump(n)==ast.dump(node)]
        dc.need(len(found)==1,'historical_route_exact_import');after.body.remove(found[0])
    names={'_HISTORICAL_ROUTE_WITNESSES','_route_owner','_route_request_key','_remember_historical_route','_historical_route_role'}
    added=[n for n in after.body if getattr(n,'name',None) in names or isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id in names for x in n.targets)]
    dc.need(old_refresh.sha(ast.dump(ast.Module(body=added,type_ignores=[])).encode())=='8ce6aaef8c93b14741a41a63c39b0a79aeb6d65d29b494be1861dcaa05878d34','historical_route_exact_new_helpers')
    after.body=[n for n in after.body if n not in added]
    role=next(n for n in after.body if isinstance(n,ast.FunctionDef) and n.name=='_fresh_role')
    head=ast.parse('historical = _historical_route_role(settings, project, request, catalog)\nif historical is not None:\n return historical').body
    dc.need(ast.dump(ast.Module(body=role.body[:2],type_ignores=[]))==ast.dump(ast.Module(body=head,type_ignores=[])),'historical_route_exact_dispatch')
    del role.body[:2]
    request=next(n for n in after.body if isinstance(n,ast.FunctionDef) and n.name=='_retained_request')
    registration=ast.parse('_remember_historical_route(request, run)').body[0]
    found=[n for n in request.body if ast.dump(n)==ast.dump(registration)]
    dc.need(len(found)==1 and request.body[-2] is found[0] and isinstance(request.body[-1],ast.Return),'historical_route_exact_registration')
    request.body.remove(found[0])
    dc.need(ast.dump(before)==ast.dump(after),'historical_route_old_parsers_or_proofs_changed')
    old=ast.parse(baseline_sources[transition]);new=ast.parse((current_root/transition).read_text())
    guard=next(n for n in new.body if isinstance(n,ast.FunctionDef) and n.name=='_verify_historical_route_delta')
    new.body.remove(guard)
    dispatch=next(n for n in new.body if isinstance(n,ast.FunctionDef) and n.name=='verify_dispatch_delta')
    branch=ast.parse("if changed == {'src/bestseller/services/retained_review_input_proof.py','src/bestseller/services/draft_continuity_reference_transition.py'}:\n return _verify_historical_route_delta(baseline_manifest=baseline_manifest,baseline_sources=baseline_sources,current_root=current_root)").body[0]
    found=[n for n in dispatch.body if ast.dump(n)==ast.dump(branch)]
    dc.need(len(found)==1,'historical_route_exact_transition_dispatch');dispatch.body.remove(found[0])
    dc.need(ast.dump(old)==ast.dump(new),'historical_route_other_transition_rules_changed')
    return current,{'schema':'exact-historical-source-review-route-transition.v1','baseline_policy_sha256':old_refresh.manifest_hash(baseline_manifest),'current_policy_sha256':old_refresh.manifest_hash(current),'changed_paths':sorted(changed),'source_parser_body_and_grade_rules_unchanged':True,'historical_protocols_only':['draft_continuity_hard_safety/3.0','draft_continuity_hard_safety/4.0-completion-supplement'],'physical_model_endpoint_and_full_effective_hash_still_required':True,'old_grade_relabelled':False,'new_model_calls':0}


def _verify_foundation_catalog_delta(*, baseline_manifest, baseline_sources, current_root):
    """Exact additive catalog provenance and historical foundation route only.

    Extra old source witnesses were not covered by the former policy manifest;
    they are pinned to the reviewed pre-change source snapshot, not represented
    as if the old paid policy had already hashed those dependency modules.
    """
    prefix = 'src/bestseller/services/'
    current = old_refresh.policy_manifest(current_root)
    changed = {p for p in set(current) | set(baseline_manifest) if current.get(p) != baseline_manifest.get(p)}
    expected = {prefix + n for n in ('retained_review_input_proof.py', 'pipelines.py', 'draft_continuity_reference_transition.py', 'foundation_volume_edit.py', 'foundation_revision_selection.py', 'foundation_catalog_transition.py', 'operator_action_approval.py', 'volume_source_admission_input.py', 'draft_continuity_applicable_review.py', 'draft_continuity.py')}
    dc.need(changed in (expected, expected | {'config/model_catalog.yaml'}), 'foundation_catalog_unrelated_policy_change')
    witnesses = {'src/bestseller/services/foundation_volume_edit.py': '0e556f1ed5a80b0f0ff59c2e2be0eed720509266b36d9aeb9dc200185b815eef', 'src/bestseller/services/foundation_revision_selection.py': '5d1b53e699aaaab82eb6eefa78f27c76057d985f5ccb9ac0b20d4729d0f1668d', 'src/bestseller/services/operator_action_approval.py': 'c4b72c907238d7a35c2965b3fe9fc9a496e0605bb610b8a305665b614fd73274', 'src/bestseller/services/volume_source_admission_input.py': '4c1ac4f50ffdc9d45ded6262393fe47e646adb68bfef8a2d295cbab6ad9cc750', 'src/bestseller/services/draft_continuity_applicable_review.py': 'fa9300e2348343c860877f30819fc7c0d3bd7bbfd51b7311098330f79530cd89'}
    for name in ('retained_review_input_proof.py', 'pipelines.py', 'draft_continuity_reference_transition.py'):
        key = prefix + name
        dc.need(old_refresh.sha(baseline_sources[key].encode()) == baseline_manifest[key], 'foundation_catalog_original_policy_source')
    for key, digest in witnesses.items():
        dc.need(old_refresh.sha(baseline_sources[key].encode()) == digest, 'foundation_catalog_reviewed_extra_source_witness')

    def same(name, expected_source):
        dc.need(ast.dump(ast.parse((current_root / prefix / name).read_text())) == ast.dump(ast.parse(expected_source)), 'foundation_catalog_exact_source_delta_' + name)
    old = baseline_sources[prefix + 'retained_review_input_proof.py']
    dc.need(old.count("    if (request.prompt_template != 'draft_continuity_hard_safety'\n        or request.prompt_version not in {'3.0', '4.0-completion-supplement'}") == 1, 'foundation_catalog_original_route_guard')
    retained_tree = ast.parse((current_root / prefix / 'retained_review_input_proof.py').read_text())
    scoped = [n for n in retained_tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'historical_reference_route_scope']
    dc.need(len(scoped) == 1 and old_refresh.sha(ast.dump(scoped[0]).encode()) == 'b5017fc937a9b6e5e58719363f99cf12937b5fe331ec5d561946971df4f61e3e', 'foundation_catalog_exact_operation_scope')
    retained_tree.body.remove(scoped[0])
    dc.need(ast.dump(retained_tree) == ast.dump(ast.parse(old.replace("    if (request.prompt_template != 'draft_continuity_hard_safety'\n        or request.prompt_version not in {'3.0', '4.0-completion-supplement'}", "    historical_protocol = (request.prompt_template, request.prompt_version) in {\n        ('draft_continuity_hard_safety', '3.0'),\n        ('draft_continuity_hard_safety', '4.0-completion-supplement'),\n        ('foundation_creative_delta_review', '1.2-source-sections'),\n        ('foundation_creative_delta_review', '1.3-dependency-scope'),\n        ('planning_draft_admission', 'volume-source-admission.v3'),\n    }\n    if (not historical_protocol"))), 'foundation_catalog_original_physical_verifiers_unchanged')
    old = baseline_sources[prefix + 'foundation_volume_edit.py']
    dc.need(old.count("    _need(meta.get('source_component_receipt_hash') == content_hash(components), 'source_components_changed')") == 1, 'foundation_catalog_original_component_guard')
    same('foundation_volume_edit.py', old.replace("    _need(meta.get('source_component_receipt_hash') == content_hash(components), 'source_components_changed')", "    from bestseller.services.foundation_catalog_transition import verified_component_identity\n    component_identity = await verified_component_identity(session,\n        project=project, review=review, components=components)\n    _need(meta.get('source_component_receipt_hash') == component_identity, 'source_components_changed')").replace("'source_component_receipt_sha256': content_hash(components),", "'source_component_receipt_sha256': component_identity,"))
    same('foundation_revision_selection.py', baseline_sources[prefix + 'foundation_revision_selection.py'])
    same('volume_source_admission_input.py', baseline_sources[prefix + 'volume_source_admission_input.py'].replace("    need(content_hash(components) == meta['source_component_receipt_hash'], 'admission_volume_component_receipt_changed')", "    from bestseller.services.foundation_catalog_transition import verified_component_identity\n    component_identity = await verified_component_identity(session,\n        project=project, review=workflow, components=components)\n    need(component_identity == meta['source_component_receipt_hash'], 'admission_volume_component_receipt_changed')").replace("'receipt_sha256':content_hash(components),", "'receipt_sha256':component_identity,"))
    same('operator_action_approval.py', baseline_sources[prefix + 'operator_action_approval.py'])
    old = baseline_sources[prefix + 'pipelines.py']
    dc.need(old.count('"chapter_review_prerequisite.py", "foundation_selection.py",') == 1, 'foundation_catalog_original_manifest_guard')
    same('pipelines.py', old.replace('"chapter_review_prerequisite.py", "foundation_selection.py",', '"chapter_review_prerequisite.py", "foundation_selection.py",\n            "foundation_volume_edit.py", "foundation_revision_selection.py", "foundation_catalog_transition.py", "operator_action_approval.py", "volume_source_admission_input.py", "draft_continuity_applicable_review.py",'))
    key = prefix + 'draft_continuity.py'
    dc.need(old_refresh.sha(baseline_sources[key].encode()) == baseline_manifest[key], 'foundation_catalog_original_dc_witness')
    same('draft_continuity.py', baseline_sources[key].replace("    if proof.get('schema')=='draft-continuity-source-corrected-reference.v1':", "    if proof.get('schema')=='draft-continuity-applicable-reference.v1':\n        from bestseller.services.draft_continuity_applicable_review import validate_reference\n        await validate_reference(session,settings,project=project,chapter=chapter,draft=draft,proof=proof,_memo=_memo)\n    elif proof.get('schema')=='draft-continuity-source-corrected-reference.v1':").replace('    try:\n        return await _select_reference_without_source_scope(session,settings,project=project,chapter=chapter,_memo=memo)\n    finally:', '    try:\n        from bestseller.services.retained_review_input_proof import historical_reference_route_scope\n        async with historical_reference_route_scope(session,settings,project=project,chapter=chapter):\n            return await _select_reference_without_source_scope(session,settings,project=project,chapter=chapter,_memo=memo)\n    finally:'))
    applicable = (current_root / prefix / 'draft_continuity_applicable_review.py').read_text()
    applicable_tree = ast.parse(applicable)
    qualifier = next(n for n in applicable_tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'qualify_reference')
    pending_hook = ast.parse("""from bestseller.services.pending_manuscript_continuation import record_intent
checkpoint = None
if await dc.draft_mode_enabled(session, project_id=project.id, project=project):
    checkpoint = await record_intent(session, settings, project=project, after_chapter=chapter.chapter_number)
    if checkpoint is not None:
        from bestseller.services.pending_manuscript_continuation import wake
        await wake(session, settings, checkpoint)
""").body
    matches = [i for i in range(len(qualifier.body)-len(pending_hook)+1) if ast.dump(ast.Module(body=qualifier.body[i:i+len(pending_hook)], type_ignores=[])) == ast.dump(ast.Module(body=pending_hook, type_ignores=[]))]
    dc.need(len(matches) == 1, 'foundation_catalog_exact_pending_qualification_hook')
    del qualifier.body[matches[0]:matches[0]+len(pending_hook)]
    result = qualifier.body[-1].value
    keys = [i for i,k in enumerate(result.keys) if isinstance(k, ast.Constant) and k.value == 'continuation_checkpoint_id']
    dc.need(len(keys) == 1 and ast.dump(result.values[keys[0]]) == ast.dump(ast.parse('str(checkpoint.id) if checkpoint is not None else None', mode='eval').body), 'foundation_catalog_exact_pending_receipt')
    del result.keys[keys[0]]; del result.values[keys[0]]
    dc.need(old_refresh.sha(ast.dump(applicable_tree).encode()) == '09cff8040aaa574e2884df81d1d77a6c51d76c9597851cb783d8176ffe8ac4cb', 'foundation_catalog_exact_applicable_consumer')
    helper = (current_root / prefix / 'foundation_catalog_transition.py').read_text()
    dc.need(old_refresh.sha(ast.dump(ast.parse(helper)).encode()) == 'c70b08bf14ddd9a16c9d3c423b27ce926af3c6c06572aace4b180214f4ec1157', 'foundation_catalog_exact_new_consumer')
    name = prefix + 'draft_continuity_reference_transition.py'
    after = ast.parse((current_root / name).read_text())
    fn = next((n for n in after.body if isinstance(n, ast.FunctionDef) and n.name == '_verify_foundation_catalog_delta'))
    after.body.remove(fn)
    dispatch = next((n for n in after.body if isinstance(n, ast.FunctionDef) and n.name == 'verify_dispatch_delta'))
    branch = ast.parse("if 'src/bestseller/services/foundation_catalog_transition.py' in changed:\n        return _verify_foundation_catalog_delta(baseline_manifest=baseline_manifest,baseline_sources=baseline_sources,current_root=current_root)").body[0]
    matches = [n for n in dispatch.body if ast.dump(n) == ast.dump(branch)]
    dc.need(len(matches) == 1, 'foundation_catalog_exact_transition_dispatch')
    dispatch.body.remove(matches[0])
    wrappers = [n for n in after.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'refresh_reference']
    dc.need(len(wrappers) == 1 and old_refresh.sha(ast.dump(wrappers[0]).encode()) == 'f568a20cff62247e7b5b4387a113bcf55d90d7f2fe7616ec97196a1db09e408e', 'foundation_catalog_exact_refresh_scope')
    after.body.remove(wrappers[0])
    inners = [n for n in after.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_refresh_reference_without_operation_scope']
    dc.need(len(inners) == 1, 'foundation_catalog_original_refresh_missing')
    inners[0].name = 'refresh_reference'
    dc.need(ast.dump(after) == ast.dump(ast.parse(baseline_sources[name])), 'foundation_catalog_previous_migrations_unchanged')
    if 'config/model_catalog.yaml' in changed:
        from bestseller.services.foundation_catalog_transition import _entries
        import yaml
        old_text = baseline_sources['config/model_catalog.yaml']
        new_text = (current_root / 'config/model_catalog.yaml').read_text()
        dc.need(old_refresh.sha(old_text.encode()) == baseline_manifest['config/model_catalog.yaml'], 'foundation_catalog_original_catalog_witness')
        old_entries, new_entries = (_entries(old_text), _entries(new_text))
        dc.need(set(old_entries) < set(new_entries) and all((new_entries[key] == value for key, value in old_entries.items())), 'foundation_catalog_only_additions')
        dc.need({k: v for k, v in yaml.safe_load(old_text).items() if k != 'models'} == {k: v for k, v in yaml.safe_load(new_text).items() if k != 'models'}, 'foundation_catalog_unrelated_catalog_policy')
    return (current, {'schema': 'exact-foundation-catalog-route-transition.v1', 'baseline_policy_sha256': old_refresh.manifest_hash(baseline_manifest), 'current_policy_sha256': old_refresh.manifest_hash(current), 'changed_paths': sorted(changed), 'extra_old_source_witnesses_previously_policy_hashed': False, 'source_parser_body_and_grade_rules_unchanged': True, 'historical_foundation_protocols': ['foundation_creative_delta_review/1.2-source-sections', 'foundation_creative_delta_review/1.3-dependency-scope', 'planning_draft_admission/volume-source-admission.v3'], 'catalog_compatibility_requires_separate_operator_receipt': True, 'physical_model_endpoint_and_full_effective_hash_still_required': True, 'old_grade_relabelled': False, 'new_model_calls': 0})
