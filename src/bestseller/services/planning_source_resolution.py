"""Explicit, scoped operator source decisions, separate from paid model grades.

No automatic fallback invokes this API. Old protocol Unknown/False, physical
returns and spent attempts remain immutable. This is source admission only;
commercial judgement, materialization and manuscript safety still apply.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID, NAMESPACE_URL, uuid5

from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services.prompt_source_policy import content_hash

SCHEMA = 'planning-explicit-source-resolution.v1'
PURPOSE = 'planning_source_resolution'


def need(ok, code):
    if not ok:
        raise ValueError('planning_source_resolution_' + code)


def policy():
    root = Path(__file__).parent
    return {name: sha256((root / name).read_bytes()).hexdigest() for name in (
        'planning_source_resolution.py', 'operator_action_approval.py',
        'chapter_outline_readiness.py', 'planning_draft_admission.py')}


def leaves(value, path=''):
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in leaves(v, path+'/'+k.replace('~','~0').replace('/','~1'))]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in leaves(v, path+'/'+str(i))]
    return [path]


def pointer(value, path):
    need(isinstance(path, str) and path.startswith('/'), 'source_pointer')
    for part in path[1:].split('/'):
        part = part.replace('~1','/').replace('~0','~')
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def validate_review(review, *, supplied, historical_targets, candidate):
    """Check completeness and literal evidence, not machine entailment of prose."""
    from bestseller.services.planning_draft_admission import projection_audit_targets
    need(isinstance(review, dict) and review.get('reviewer_kind') == 'authorized_operator_agent'
         and review.get('unresolved_hard_items') == [], 'unresolved_or_reviewer')
    expected = projection_audit_targets(candidate)
    checks = review.get('current_projections')
    need(isinstance(checks,list) and len(checks)==len(expected), 'projection_coverage')
    for check, target in zip(checks, expected, strict=True):
        need(check.get('target') == target and check.get('decision') == 'source_consistent'
             and isinstance(check.get('reason'),str) and len(check['reason']) >= 20,
             'projection_decision')
    need(review.get('checked_candidate_leaf_paths') == leaves(candidate), 'field_coverage')
    historical = review.get('historical_projections')
    need(isinstance(historical,list) and len(historical)==len(historical_targets), 'historical_coverage')
    for item, target in zip(historical, historical_targets, strict=True):
        need(item.get('target') == target and item.get('disposition') in
             {'corrected_in_requested_scope','retained_unapproved','unsupported_model_inference'}
             and isinstance(item.get('reason'),str) and len(item['reason']) >= 20, 'historical_decision')
    from bestseller.services.planning_draft_admission import _authority_roles
    roles = _authority_roles(supplied['source_authority'])
    uses = {'world_rule':'world_rule', 'identity':'character_identity',
            'provisional_continuity':'provisional_manuscript_reference',
            'advisory_only':'advisory', 'explicit_creator':'canonical_author'}
    evidence=review.get('source_evidence')
    need(isinstance(evidence,list) and evidence, 'source_evidence')
    for item in evidence:
        need(roles.get(item['path']) == uses.get(item.get('use')) and item.get('use') in uses, 'source_role')
        value=pointer(supplied,item['path'])
        need(isinstance(value,str) and isinstance(item.get('quote'),str) and item['quote']
             and item['quote'] in value and item.get('use') in
             {'world_rule','identity','provisional_continuity','advisory_only','explicit_creator'}, 'literal_evidence')
        if item['use']=='provisional_continuity':
            need(item['path'].startswith('/draft_manuscript_continuity/recent_qualified_pending_prose/'), 'provisional_role')
        if item['use']=='world_rule':
            need(item['path'].startswith('/approved_world_spec/'), 'world_role')
    need(review.get('old_model_grade_changed') is False
         and review.get('provisional_is_canon') is False
         and review.get('commercial_grade_asserted') is False, 'authority_escalation')


def protocol_review_bindings(protocol):
    result=[]
    for index,item in enumerate(protocol['attempts']):
        completion=item.get('completion') or {}
        claims=[]
        for tool in completion.get('tool_calls') or []:
            try:
                args=json.loads(tool['function']['arguments'])
            except (KeyError,TypeError,ValueError):
                continue
            for claim in args.get('hard_violations') or []:
                claims.append({'claim_index':len(claims),'claim_sha256':content_hash(claim),'claim':deepcopy(claim)})
        result.append({'attempt_index':index,'status':item.get('status'),
            'completion_sha256':content_hash(completion),'error_sha256':content_hash(item.get('error')),
            'claims':claims})
    return result


def validate_protocol_review(review,protocol):
    expected=protocol_review_bindings(protocol)
    actual=deepcopy(review.get('historical_attempts'))
    need(isinstance(actual,list) and len(actual)==len(expected),'native_attempt_coverage')
    for item,binding in zip(actual,expected,strict=True):
        claims=item.get('claims')
        need(isinstance(claims,list) and len(claims)==len(binding['claims']),'native_claim_coverage')
        for claim in claims:
            disposition=claim.pop('disposition',None);reason=claim.pop('reason',None)
            need(disposition in {'corrected_in_requested_scope','retained_unapproved','unsupported_model_inference'}
                 and isinstance(reason,str) and len(reason)>=20,'native_disposition')
        need(item==binding,'native_evidence_changed')


async def prepare_source_resolution(session, settings, *, project, parent_workflow_id,
        chapter_numbers, patches, review):
    from bestseller.services import chapter_outline_readiness as ready
    from bestseller.services.operator_outline_edit import _apply
    from bestseller.services.planning_draft_admission import _input, projection_audit_targets
    parent=await session.get(WorkflowRunModel,UUID(str(parent_workflow_id)),populate_existing=True)
    need(parent is not None and ready._workflow_scope(parent,project.id)
         and parent.status=='machine_blocked', 'parent_scope')
    old=deepcopy(parent.metadata_json or {})
    need(old.get('phase')=='admission_protocol_blocked' and old.get('candidates'), 'parent_protocol_state')
    current=old['candidates'][-1]
    protocol=current.get('source_admission_protocol') or {}
    need(protocol.get('status')=='protocol_exhausted' and protocol.get('attempts'), 'protocol_not_exhausted')
    numbers=list(chapter_numbers)
    need(numbers and numbers==list(range(numbers[0],numbers[-1]+1))
         and set(numbers)<=set(old['chapter_numbers']), 'scope')
    before=current['candidate']
    need(content_hash(before)==current['candidate_hash'], 'parent_candidate')
    edited=_apply(before,patches)
    for a,b in zip(before['chapters'],edited['chapters'],strict=True):
        need(a['chapter_number'] in numbers or a==b, 'unrequested_edit')
    source=deepcopy(old['source_payload'])
    overlay={c['chapter_number']:c for c in edited['chapters'] if c['chapter_number'] in numbers}
    source['chapters']=[deepcopy(overlay.get(c['chapter_number'],c)) for c in source['chapters']]
    # The public prefix constructor retains the immutable proposal envelope.
    # Rebase batch labels/omission receipts remain historical parent evidence;
    # they cannot silently replace the proposal root or unreviewed tail.
    candidate={**ready._body(source),'chapters':[deepcopy(c) for c in source['chapters'] if c['chapter_number'] in numbers]}
    foundation,contents=await ready._foundation(session,project)
    need(foundation==old['foundation'], 'foundation_changed')
    await ready._assert_unadopted_range(session,project_id=project.id,numbers=numbers)
    validated=ready._validate(candidate,project=project,state={**old,'chapter_numbers':numbers},cast=contents['cast_spec'])
    need(validated==candidate, 'validation_changed')
    supplied=await _input(session,project=project,workflow_run_id=parent.id,candidate_payload=candidate,
        book_spec=contents['book_spec'],world_spec=contents['world_spec'],cast_spec=contents['cast_spec'],commercial_report=None)
    validate_review(review,supplied=supplied,historical_targets=projection_audit_targets(before),candidate=candidate)
    plan={'schema':SCHEMA,'project_id':str(project.id),'parent_workflow_id':str(parent.id),
        'parent_state_sha256':content_hash(old),'parent_protocol_sha256':content_hash(protocol),
        'parent_candidate_sha256':content_hash(before),'chapter_numbers':numbers,'patches':deepcopy(patches),
        'candidate':candidate,'source_payload':source,'review_input':supplied,'review':deepcopy(review),
        'inherited_attempts':{k:deepcopy(old.get(k,[])) for k in ('patch_attempts','hard_patch_attempts')},
        'policy':policy(),'model_grade_asserted':False,'automatic_credit_granted':False}
    plan['plan_sha256']=content_hash(plan)
    await _validate_plan_parent(session, plan)
    return plan


async def _validate_plan_parent(session, plan):
    from bestseller.services.operator_outline_edit import _apply
    from bestseller.services.planning_draft_admission import projection_audit_targets
    from bestseller.services import chapter_outline_readiness as ready
    parent=await session.get(WorkflowRunModel,UUID(plan['parent_workflow_id']),populate_existing=True)
    need(parent is not None and ready._workflow_scope(parent,UUID(plan['project_id']))
         and parent.status=='machine_blocked' and content_hash(parent.metadata_json)==plan['parent_state_sha256'], 'parent_changed')
    old=parent.metadata_json
    need(old.get('phase')=='admission_protocol_blocked','parent_protocol_state')
    current=old['candidates'][-1]
    protocol=current.get('source_admission_protocol') or {}
    need(protocol.get('status')=='protocol_exhausted' and protocol.get('attempts')
         and content_hash(protocol)==plan['parent_protocol_sha256'], 'protocol_changed')
    validate_protocol_review(plan['review'],protocol)
    before=current['candidate']
    need(content_hash(before)==current['candidate_hash']==plan['parent_candidate_sha256'],'parent_candidate')
    numbers=plan['chapter_numbers']
    need(numbers and numbers==list(range(numbers[0],numbers[-1]+1)) and set(numbers)<=set(old['chapter_numbers']),'scope')
    edited=_apply(before,plan['patches'])
    for a,b in zip(before['chapters'],edited['chapters'],strict=True):
        need(a['chapter_number'] in numbers or a==b,'unrequested_edit')
    source=deepcopy(old['source_payload'])
    overlay={c['chapter_number']:c for c in edited['chapters'] if c['chapter_number'] in numbers}
    source['chapters']=[deepcopy(overlay.get(c['chapter_number'],c)) for c in source['chapters']]
    # The public prefix constructor retains the immutable proposal envelope.
    # Rebase batch labels/omission receipts remain historical parent evidence;
    # they cannot silently replace the proposal root or unreviewed tail.
    candidate={**ready._body(source),'chapters':[deepcopy(c) for c in source['chapters'] if c['chapter_number'] in numbers]}
    need(candidate==plan['candidate'] and source==plan['source_payload'],'patch_lineage')
    need(plan['inherited_attempts']=={k:deepcopy(old.get(k,[])) for k in ('patch_attempts','hard_patch_attempts')},'attempts_changed')
    need(plan['review_input']['candidate_payload']==candidate
         and plan['review_input']['project_id']==plan['project_id']
         and plan['review_input']['workflow_run_id']==plan['parent_workflow_id'],'review_input_scope')
    validate_review(plan['review'],supplied=plan['review_input'],
        historical_targets=projection_audit_targets(before),candidate=candidate)
    need(plan.get('model_grade_asserted') is False and plan.get('automatic_credit_granted') is False,'authority_escalation')
    return plan


async def record_source_resolution(session, *, plan, actor, authorization_ref, explicit_user_approval=False):
    from bestseller.services.operator_action_approval import record_operator_action_approval
    need(explicit_user_approval is True, 'explicit_authority_required')
    need(content_hash({k:v for k,v in plan.items() if k!='plan_sha256'})==plan['plan_sha256']
         and plan['policy']==policy(), 'plan_changed')
    await _validate_plan_parent(session, plan)
    identity=uuid5(NAMESPACE_URL,'bestseller:planning-source-resolution:'+plan['plan_sha256'])
    binding={'project_id':plan['project_id'],'target_id':str(identity),'plan_sha256':plan['plan_sha256']}
    approval=await record_operator_action_approval(session,purpose=PURPOSE,binding=binding,
        capabilities=['resolve_exact_source'],actor=actor,authorization_ref=authorization_ref,explicit_user_approval=True)
    data={'plan':deepcopy(plan),'approval_id':str(approval)}
    row=await session.get(WorkflowRunModel,identity,populate_existing=True)
    if row is None:
        session.add(WorkflowRunModel(id=identity,project_id=UUID(plan['project_id']),
            workflow_type=PURPOSE,scope_type='project',scope_id=UUID(plan['project_id']),
            status='completed',current_step='explicit_source_only',requested_by=actor[:64],metadata_json=data))
    else:
        need(row.metadata_json==data and row.status=='completed','identity_conflict')
    await session.flush()
    return str(identity)


async def verify_resolution(session, *, project, resolution_id, candidate, source_payload=None, supplied=None):
    from bestseller.services.operator_action_approval import require_operator_action_approval
    from bestseller.services import chapter_outline_readiness as ready
    row=await session.get(WorkflowRunModel,UUID(str(resolution_id)),populate_existing=True)
    need(row is not None and row.project_id==project.id and row.workflow_type==PURPOSE
         and row.scope_type=='project' and row.scope_id==project.id and row.status=='completed', 'receipt_scope')
    plan=(row.metadata_json or {})['plan']
    need(content_hash({k:v for k,v in plan.items() if k!='plan_sha256'})==plan['plan_sha256']
         and plan['candidate']==candidate, 'plan_stale')
    if plan['policy']!=policy():
        from bestseller.services.planning_source_resolution_compatibility import require_compatible_resolution
        await require_compatible_resolution(session,project=project,resolution=row,plan=plan)
    await _validate_plan_parent(session, plan)
    await require_operator_action_approval(session,approval_id=row.metadata_json['approval_id'],purpose=PURPOSE,
        binding={'project_id':str(project.id),'target_id':str(row.id),'plan_sha256':plan['plan_sha256']},capability='resolve_exact_source')
    parent=await session.get(WorkflowRunModel,UUID(plan['parent_workflow_id']),populate_existing=True)
    need(parent is not None and ready._workflow_scope(parent,project.id)
         and content_hash(parent.metadata_json)==plan['parent_state_sha256'], 'parent_changed')
    if source_payload is not None:
        need(source_payload==plan['source_payload'],'proposal_changed')
    if supplied is not None:
        comparable={**deepcopy(supplied),'workflow_run_id':plan['parent_workflow_id']}
        need(comparable==plan['review_input'], 'current_source_changed')
    return deepcopy(plan)


async def bind_resolution(session, *, project, workflow, state, contents, resolution_id):
    from bestseller.services.planning_draft_admission import _input, HARD_ADMISSION_POLICY
    current=state['candidates'][-1]
    supplied=await _input(session,project=project,workflow_run_id=workflow.id,candidate_payload=current['candidate'],
        book_spec=contents['book_spec'],world_spec=contents['world_spec'],cast_spec=contents['cast_spec'],commercial_report=None)
    plan=await verify_resolution(session,project=project,resolution_id=resolution_id,candidate=current['candidate'],
        source_payload=state['source_payload'],supplied=supplied)
    need(state['chapter_numbers']==plan['chapter_numbers'], 'consumer_scope')
    need(state['identity'].get('source_resolution_id')==str(resolution_id),'consumer_identity')
    return {'schema':SCHEMA,'hard_admission_policy':HARD_ADMISSION_POLICY,'resolution_id':str(resolution_id),
        'resolution_plan_sha256':plan['plan_sha256'],'workflow_run_id':str(workflow.id),'project_id':str(project.id),
        'candidate_hash':content_hash(current['candidate']),'graded_input_hash':content_hash(supplied),
        'foundation_hashes':supplied['foundation'],'commercial_report':None,'draft_eligible':True,
        'model_grade_asserted':False,'commercial_grade_asserted':False,'automatic_credit_granted':False}


async def verify_source_admission(session, *, project, workflow_run_id, candidate_payload,
        book_spec, world_spec, cast_spec, receipt):
    from bestseller.services.planning_draft_admission import _input
    workflow=await session.get(WorkflowRunModel,workflow_run_id,populate_existing=True)
    need(workflow is not None and workflow.project_id==project.id,'consumer_workflow')
    state=workflow.metadata_json or {}
    need(state.get('identity',{}).get('source_resolution_id')==receipt.get('resolution_id')
         and state.get('candidates') and state['candidates'][-1].get('source_admission')==receipt,'consumer_binding')
    supplied=await _input(session,project=project,workflow_run_id=workflow_run_id,candidate_payload=candidate_payload,
        book_spec=book_spec,world_spec=world_spec,cast_spec=cast_spec,commercial_report=None)
    plan=await verify_resolution(session,project=project,resolution_id=receipt['resolution_id'],candidate=candidate_payload,
        source_payload=state['source_payload'],supplied=supplied)
    need(state['chapter_numbers']==plan['chapter_numbers'] and all(state.get(k,[])==v for k,v in plan['inherited_attempts'].items()),'attempts_or_scope_changed')
    expected=await bind_resolution(session,project=project,workflow=workflow,state=state,
        contents={'book_spec':book_spec,'world_spec':world_spec,'cast_spec':cast_spec},resolution_id=receipt['resolution_id'])
    need(expected==receipt,'admission_changed')
    return deepcopy(receipt)
