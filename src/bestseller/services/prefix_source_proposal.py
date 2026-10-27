"""Explicit ungraded prefix proposals, separate from paid commercial grades.

Trusted operator API only. A proposal authorizes review of one exact prefix;
normal commercial judgement and hard admission still own write eligibility.
"""
from copy import deepcopy
from uuid import UUID, NAMESPACE_URL, uuid5

from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services.prompt_source_policy import content_hash

SCHEMA = 'outline-prefix-source-proposal.v1'
KEY = 'outline_prefix_source_proposals'
SCOPED_KEY = 'outline_prefix_source_proposal_scopes'


def need(ok, code):
    if not ok:
        raise ValueError('prefix_source_proposal_' + code)


async def register_prefix_source_proposal(session, *, project, parent_workflow_id,
        source_payload, through_chapter, actor, authorization_ref,
        explicit_user_approval=False, from_chapter=None):
    from bestseller.services import chapter_outline_readiness as ready
    from bestseller.services.operator_action_approval import record_operator_action_approval

    need(explicit_user_approval is True, 'human_approval_required')
    parent = await session.get(WorkflowRunModel, parent_workflow_id, populate_existing=True)
    need(parent is not None and parent.project_id == project.id
         and ready._workflow_scope(parent, project.id), 'parent_scope')
    old = parent.metadata_json or {}
    original = old.get('source_payload')
    need(isinstance(original, dict) and isinstance(source_payload, dict), 'parent_body')
    window = ready.outline_window(source_payload)
    need(window is not None and window == ready.outline_window(original), 'window')
    first, end = window
    need(type(through_chapter) is int and first <= through_chapter <= end, 'prefix_scope')
    start=first if from_chapter is None else from_chapter
    need(type(start) is int and first<=start<=through_chapter,'prefix_scope')
    numbers = list(range(start, through_chapter + 1))
    await ready._assert_unadopted_range(session, project_id=project.id, numbers=numbers)
    foundation, contents = await ready._foundation(session, project)
    volume = source_payload['chapters'][0].get('volume_number')
    binding = ready._window_binding(project, source_payload, volume)
    # The unreviewed tail is an immutable parent, not a fresh creative proposal.
    old_tail = {**original, 'chapters': [c for c in original['chapters'] if c['chapter_number'] not in numbers]}
    tail = {**source_payload, 'chapters': [c for c in source_payload['chapters'] if c['chapter_number'] not in numbers]}
    need(old_tail == tail, 'unreviewed_tail_changed')
    prefix = {**ready._body(source_payload), 'chapters': deepcopy([c for c in source_payload['chapters'] if c['chapter_number'] in numbers])}
    validated = ready._validate(prefix, project=project,
        state={**deepcopy(old), 'chapter_numbers': numbers, 'binding': binding}, cast=contents['cast_spec'])
    need(validated == prefix, 'candidate_validation_changed')
    record = {'schema': SCHEMA, 'project_id': str(project.id),
        'candidate_hash': content_hash(source_payload), 'prefix_hash': content_hash(prefix),
        'chapter_numbers': numbers, 'binding': binding, 'foundation': foundation,
        'parent_workflow_id': str(parent.id), 'parent_workflow_hash': content_hash(old),
        'parent_source_hash': content_hash(original),
        'parent_attempts_hash': content_hash({k: old.get(k, []) for k in ('patch_attempts', 'hard_patch_attempts')}),
        'grade_asserted': False, 'write_authorized': False, 'automatic_credit_granted': False}
    identity = uuid5(NAMESPACE_URL, 'bestseller:prefix-source-proposal:' + content_hash(record))
    record['proposal_id'] = str(identity)
    approval_binding = {**record, 'target_id': str(identity)}
    approval = await record_operator_action_approval(session, purpose='prefix_source_proposal',
        binding=approval_binding, capabilities=['review_exact_prefix'], actor=actor,
        authorization_ref=authorization_ref, explicit_user_approval=True)
    data = {'record': record, 'source_payload': deepcopy(source_payload), 'approval_id': str(approval)}
    existing = await session.get(WorkflowRunModel, identity)
    if existing is None:
        session.add(WorkflowRunModel(id=identity, project_id=project.id,
            workflow_type='prefix_source_proposal', scope_type='project', scope_id=project.id,
            status='completed', current_step='ungraded_source_registered', requested_by=actor,
            metadata_json=data))
    else:
        need(existing.metadata_json == data and existing.status == 'completed', 'identity_conflict')
    pointers = deepcopy((project.metadata_json or {}).get(KEY) or {})
    prior = pointers.get(record['candidate_hash'])
    if prior is None and start==first or prior==record:
        pointers[record['candidate_hash']] = deepcopy(record)
        project.metadata_json = {**project.metadata_json, KEY: pointers}
    else:
        # Exact scoped records coexist; an extended/continuation review must
        # never overwrite the original admitted prefix's custody pointer.
        scopes=deepcopy(project.metadata_json.get(SCOPED_KEY) or {})
        bucket=scopes.setdefault(record['candidate_hash'],{})
        scope_key=f'{start}:{through_chapter}'
        need(bucket.get(scope_key) in (None,record),'existing_scope_conflict')
        bucket[scope_key]=deepcopy(record)
        project.metadata_json={**project.metadata_json,SCOPED_KEY:scopes}
    await session.flush()
    return deepcopy(record)


async def verify_prefix_source_proposal(session, *, project, record, source_payload,
        chapter_numbers=None):
    from bestseller.services import chapter_outline_readiness as ready
    from bestseller.services.operator_action_approval import require_operator_action_approval

    need(isinstance(record, dict) and record.get('schema') == SCHEMA, 'schema')
    row = await session.get(WorkflowRunModel, UUID(record['proposal_id']), populate_existing=True)
    need(row is not None and row.project_id == project.id and row.scope_type == 'project'
         and row.scope_id == project.id and row.workflow_type == 'prefix_source_proposal'
         and row.status == 'completed', 'row_scope')
    data = row.metadata_json or {}
    need(data.get('record') == record and data.get('source_payload') == source_payload, 'record_changed')
    allowed=record['chapter_numbers'];scope_key=f'{allowed[0]}:{allowed[-1]}' if allowed else ''
    scoped=((project.metadata_json.get(SCOPED_KEY) or {}).get(record['candidate_hash']) or {}).get(scope_key)
    need((project.metadata_json.get(KEY) or {}).get(record['candidate_hash']) == record or scoped==record, 'pointer_changed')
    await require_operator_action_approval(session, approval_id=UUID(data['approval_id']),
        purpose='prefix_source_proposal', binding={**record, 'target_id': str(row.id)},
        capability='review_exact_prefix')
    parent = await session.get(WorkflowRunModel, UUID(record['parent_workflow_id']), populate_existing=True)
    need(parent is not None and parent.project_id == project.id and ready._workflow_scope(parent, project.id), 'parent_scope')
    old = parent.metadata_json or {}
    need(content_hash(old) == record['parent_workflow_hash']
         and content_hash(old.get('source_payload')) == record['parent_source_hash']
         and content_hash({k: old.get(k, []) for k in ('patch_attempts', 'hard_patch_attempts')}) == record['parent_attempts_hash'], 'parent_changed')
    need(content_hash(source_payload) == record['candidate_hash'], 'source_changed')
    need(allowed and allowed == list(range(allowed[0], allowed[-1] + 1)), 'scope')
    if chapter_numbers is not None:
        need(list(chapter_numbers) == allowed, 'review_scope_changed')
    prefix = {**ready._body(source_payload), 'chapters': [c for c in source_payload['chapters'] if c['chapter_number'] in allowed]}
    need(content_hash(prefix) == record['prefix_hash'], 'prefix_changed')
    volume = record['binding']['volume_number']
    need(ready._window_binding(project, source_payload, volume) == record['binding'], 'binding_stale')
    foundation, _ = await ready._foundation(session, project)
    need(foundation == record['foundation'], 'foundation_stale')
    need(record.get('grade_asserted') is False and record.get('write_authorized') is False
         and record.get('automatic_credit_granted') is False, 'authority_escalation')
    return deepcopy(record)
