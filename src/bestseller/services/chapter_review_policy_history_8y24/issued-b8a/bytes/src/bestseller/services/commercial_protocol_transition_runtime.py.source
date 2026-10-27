"""Persist explicit legacy-protocol exclusions without changing paid evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import inspect, or_, select

from bestseller.infra.db.models import (
    ExecutionAttemptModel, ExecutionBudgetModel, ExecutionOperationModel,
    LlmRunModel, WorkflowRunModel,
)
from bestseller.services.budget import completion_authorization, execution_scope_block_code
from bestseller.services.commercial_protocol_transition import (
    LEGACY_VERSIONS, SCHEMA, _version, build_transition_receipt, validate_transition_receipt,
)

_INFO = 'commercial_protocol_transition_proof'
_POLICY_FILES = (
    'commercial_protocol_transition.py', 'commercial_protocol_transition_runtime.py',
    'commercial_request_purpose.py', 'unsent_reclaim_control.py', 'commercial_ambiguity_retirement.py',
    'legacy_request_purpose.py', 'legacy_fanqie_beat_request.py',
    'execution_control_purpose.py',
    'commercial_ambiguity_continuation.py',
    'commercial_judge_recovery.py', 'chapter_llm_quality_judge.py',
    'commercial_source_evidence.py', 'commercial_review_wire.py', 'commercial_review_tool.py',
    'llm.py',
)


def _row(model):
    return json.loads(json.dumps({field.key: getattr(model, field.key)
        for field in inspect(type(model)).column_attrs}, default=str))


def protocol_manifest():
    services = Path(__file__).resolve().parent
    return {name: hashlib.sha256((services / name).read_bytes()).hexdigest() for name in _POLICY_FILES}


async def transition_snapshots(session, *, project_id, scope):
    runs = list(await session.scalars(select(LlmRunModel).where(
        or_(LlmRunModel.project_id == project_id, LlmRunModel.project_id.is_(None)),
        LlmRunModel.prompt_template == 'chapter_commercial_quality_judge')))
    legacy = [run for run in runs if _version(run.prompt_version) in LEGACY_VERSIONS
        and (run.metadata_json or {}).get('execution_receipt', {}).get('scope_id') == scope]
    hashes = {run.prompt_hash for run in legacy}
    declared = {(run.metadata_json or {}).get('execution_receipt', {}).get('operation_key') for run in legacy}
    all_ops = list(await session.scalars(select(ExecutionOperationModel).where(
        ExecutionOperationModel.scope_id == scope)))
    roots = {op.key for op in all_ops if op.key in declared or (
        op.status in {'completed', 'stopped', 'pending', 'unknown'} and op.input_hash in hashes)}
    attempts = list(await session.scalars(select(ExecutionAttemptModel).where(
        ExecutionAttemptModel.scope_id == scope, ExecutionAttemptModel.operation_key.in_(roots))))
    aids = {attempt.id for attempt in attempts}
    controls = []
    for op in all_ops:
        payload = op.result_json or {}
        if not isinstance(payload, dict):
            continue
        if op.status in {'transport_result', 'transport_failure_receipt'} and (
            payload.get('operation_key') in roots or payload.get('attempt_id') in aids):
            controls.append(op)
        elif op.status == 'reconciliation_receipt' and (
            payload.get('receipt') or {}).get('attempt_id') in aids:
            controls.append(op)
    selected = [op for op in all_ops if op.key in roots] + controls
    return {'runs': [_row(run) for run in legacy], 'operations': [_row(op) for op in selected],
        'attempts': [_row(attempt) for attempt in attempts],
        'quarantine_receipts': [_row(op) for op in controls if op.status == 'reconciliation_receipt']}


async def inspect_authorized_protocol_transition(session, *, project_id, chapter_id, scope,
        evaluation_contract, current_input_hash, effective_critic):
    """Return only exact revalidated exclusions; never prepare or commit a slot.

    This path requires the existing explicit completion-bound authorization.
    Ordinary scopes retain the strict orphan fence. A different receipt UUID
    cannot change the runtime's stable cohort or paid operation identity.
    """
    session.info.pop(_INFO, None)
    if not current_input_hash or not effective_critic:
        return None
    budget = await session.get(ExecutionBudgetModel, scope)
    authorization = completion_authorization(budget) if budget is not None else None
    if not authorization or execution_scope_block_code(budget):
        return None
    if (effective_critic.get('model') not in authorization.get('models', [])
            or urlparse(effective_critic.get('api_base') or '').hostname not in authorization.get('endpoint_hosts', [])):
        raise ValueError('commercial_transition_provider_not_authorized')
    from bestseller.services.commercial_source_evidence import PROMPT_VERSION
    binding = {'project_id': str(project_id), 'chapter_id': str(chapter_id),
        'execution_scope_id': scope, 'current_effective_input_hash': current_input_hash,
        'target_protocol': {'prompt_version': PROMPT_VERSION, 'source_manifest': protocol_manifest(),
                            'evaluation_contract': dict(evaluation_contract)}}
    snapshots = await transition_snapshots(session, project_id=project_id, scope=scope)
    if not snapshots['runs']:
        return None
    proposed = build_transition_receipt(**binding, snapshots=snapshots)
    identity = uuid5(NAMESPACE_URL, 'bestseller:' + SCHEMA + ':' + proposed['identity'])
    existing = await session.get(WorkflowRunModel, identity)
    receipt = (existing.metadata_json or {}).get('receipt') if existing is not None else proposed
    if existing is not None and (existing.project_id != project_id
            or existing.scope_type != 'chapter' or existing.scope_id != chapter_id
            or existing.workflow_type != 'commercial_protocol_transition' or existing.status != 'completed'):
        raise ValueError('commercial_transition_persisted_binding_mismatch')
    exclusions = validate_transition_receipt(receipt, **binding, snapshots=snapshots)
    session.info[_INFO] = {'id': identity, 'project_id': project_id, 'chapter_id': chapter_id,
        'receipt': receipt, 'authorization_ref': authorization['authorization_ref']}
    return exclusions


async def persist_inspected_protocol_transition(session, *, expected_input_hash):
    """Caller commits this audit with the prepared cohort before any provider send."""
    proof = session.info.get(_INFO)
    if proof is None:
        return None
    if proof['receipt']['binding']['current_effective_input_hash'] != expected_input_hash:
        raise ValueError('commercial_transition_prepared_input_changed')
    existing = await session.get(WorkflowRunModel, proof['id'])
    if existing is None:
        session.add(WorkflowRunModel(id=proof['id'], project_id=proof['project_id'],
            workflow_type='commercial_protocol_transition', scope_type='chapter',
            scope_id=proof['chapter_id'], status='completed', current_step='legacy_protocol_retired',
            requested_by='authorized-protocol-transition', idempotency_key=proof['receipt']['identity'],
            metadata_json={'schema': SCHEMA, 'authorization_ref': proof['authorization_ref'],
                           'receipt': proof['receipt']}))
        await session.flush()
    elif (existing.metadata_json or {}).get('receipt') != proof['receipt']:
        raise ValueError('commercial_transition_persisted_receipt_changed')
    return str(proof['id'])


async def inspect_ambiguity_retirement(session, *, project_id, chapter_id, scope, body_hash,
        evaluation_contract, current_input_hash, effective_critic, roots, operations, attempts,
        cohort_samples=3, draft_id=None):
    """Read a fixed operator baseline; ordinary execution cannot create one."""
    from bestseller.services.commercial_ambiguity_retirement import (
        SCHEMA as AMBIGUITY_SCHEMA, build_ambiguity_retirement_receipt,
        validate_ambiguity_retirement_receipt,
    )
    from bestseller.services.commercial_protocol_transition import _digest
    from bestseller.services.commercial_source_evidence import PROMPT_VERSION

    for transient in ('commercial_ambiguity_derived', 'commercial_ambiguity_verified',
                      'commercial_ambiguity_proposed'):
        session.info.pop(transient, None)
    if type(cohort_samples) is not int or cohort_samples != 3:
        raise ValueError('commercial_ambiguity_requires_existing_three_slot_policy')
    budget = await session.get(ExecutionBudgetModel, scope)
    authorization = completion_authorization(budget) if budget is not None else None
    if not authorization or execution_scope_block_code(budget):
        raise ValueError('commercial_cohort_opaque_operation_requires_recovery')
    if (effective_critic.get('model') not in authorization.get('models', [])
            or urlparse(effective_critic.get('api_base') or '').hostname not in authorization.get('endpoint_hosts', [])):
        raise ValueError('commercial_transition_provider_not_authorized')
    actual = [a for a in attempts if a.operation_key in roots]
    aids = {a.id for a in actual}
    selected = []
    for op in operations:
        payload = op.result_json or {}
        linked = isinstance(payload, dict) and (
            payload.get('attempt_id') in aids or (isinstance(payload.get('receipt'), dict)
            and payload['receipt'].get('attempt_id') in aids))
        if op.key in roots or linked:
            selected.append(op)
    snapshots = {'operations': [_row(op) for op in selected],
        'attempts': [_row(a) for a in actual],
        'quarantine_receipts': [_row(op) for op in selected if op.status == 'reconciliation_receipt']}
    operator = session.info.get('commercial_ambiguity_operator_prepare')
    binding = {'project_id': str(project_id), 'chapter_id': str(chapter_id),
        'execution_scope_id': scope, 'body_sha256': body_hash,
        'current_effective_input_hash': current_input_hash,
        'target_protocol': {'prompt_version': PROMPT_VERSION, 'source_manifest': protocol_manifest(),
                            'evaluation_contract': dict(evaluation_contract)},
        'stable_cohort_key': _digest({'project_id': str(project_id), 'chapter_id': str(chapter_id),
                                     'body_sha256': body_hash}),
        'allowed_slot_labels': [f'chapter-quality.independent-v2:{i}' for i in range(3)],
        'actor': operator['actor'] if operator else 'persisted-operator',
        'reason': operator['reason'] if operator else 'persisted-operator',
        'original_authorization_ref': authorization['authorization_ref']}
    proposed = build_ambiguity_retirement_receipt(**binding, snapshots=snapshots)
    identity = uuid5(NAMESPACE_URL, 'bestseller:' + AMBIGUITY_SCHEMA + ':' + proposed['identity'])
    existing = await session.get(WorkflowRunModel, identity)
    if existing is not None:
        if (existing.project_id != project_id or existing.scope_type != 'chapter'
                or existing.scope_id != chapter_id or existing.status != 'completed'
                or existing.workflow_type != 'commercial_ambiguity_retirement'):
            raise ValueError('commercial_ambiguity_persisted_binding_mismatch')
        saved = (existing.metadata_json or {}).get('receipt')
        if not isinstance(saved, dict) or not isinstance(saved.get('binding'), dict):
            raise ValueError('commercial_ambiguity_persisted_receipt_missing')
        for audit_field in ('actor', 'reason'):
            binding[audit_field] = saved['binding'].get(audit_field)
        exclusions = validate_ambiguity_retirement_receipt(saved, **binding, snapshots=snapshots)
        session.info['commercial_ambiguity_verified'] = {'workflow_id': str(identity), 'receipt': saved}
        return exclusions
    if not operator:
        derived = await inspect_continuation_grant(
            session, binding=binding, snapshots=snapshots, authorization=authorization,
            project_id=project_id, chapter_id=chapter_id, draft_id=draft_id,
        )
        if derived is None:
            raise ValueError('commercial_ambiguity_explicit_operator_migration_required')
        return derived
    exclusions = validate_ambiguity_retirement_receipt(proposed, **binding, snapshots=snapshots)
    session.info['commercial_ambiguity_proposed'] = {'id': identity, 'project_id': project_id,
        'chapter_id': chapter_id, 'receipt': proposed}
    return exclusions


async def persist_operator_ambiguity_retirement(session):
    """Operator-only checkpoint. No cohort, budget, attempt or historical row edits."""
    proof = session.info.get('commercial_ambiguity_proposed')
    if proof is None or not session.info.get('commercial_ambiguity_operator_prepare'):
        return None
    existing = await session.get(WorkflowRunModel, proof['id'])
    if existing is not None:
        if (existing.metadata_json or {}).get('receipt') != proof['receipt']:
            raise ValueError('commercial_ambiguity_persisted_receipt_changed')
    else:
        session.add(WorkflowRunModel(id=proof['id'], project_id=proof['project_id'],
            workflow_type='commercial_ambiguity_retirement', scope_type='chapter',
            scope_id=proof['chapter_id'], status='completed', current_step='ambiguity_retained',
            requested_by=proof['receipt']['binding']['actor'],
            idempotency_key=proof['receipt']['identity'],
            metadata_json={'receipt': proof['receipt']}))
        await session.flush()
    return str(proof['id'])


async def inspect_continuation_grant(session, *, binding, snapshots, authorization,
        project_id, chapter_id, draft_id):
    """Derive only from an explicitly committed, unchanged legacy baseline."""
    from bestseller.infra.db.models import ChapterDraftVersionModel
    from bestseller.services.commercial_ambiguity_continuation import validate_continuation_grant
    from bestseller.services.commercial_ambiguity_retirement import (
        SCHEMA as CHILD_SCHEMA, build_ambiguity_retirement_receipt,
        validate_ambiguity_retirement_receipt,
    )
    policy = {key: binding['target_protocol'][key] for key in ('prompt_version', 'source_manifest')}
    candidates = list(await session.scalars(select(WorkflowRunModel).where(
        WorkflowRunModel.project_id == project_id,
        WorkflowRunModel.workflow_type == 'commercial_ambiguity_continuation',
        WorkflowRunModel.status == 'completed')))
    matching = []
    for workflow in candidates:
        meta = workflow.metadata_json or {}
        if (meta.get('execution_scope_id') != binding['execution_scope_id']
                or meta.get('target_protocol_policy') != policy):
            continue
        baseline = await session.get(WorkflowRunModel, UUID(meta['baseline_workflow_id']))
        if (workflow.scope_type != 'project' or workflow.scope_id != project_id
                or baseline is None or baseline.project_id != project_id
                or baseline.status != 'completed'
                or baseline.workflow_type != 'commercial_ambiguity_retirement'):
            raise ValueError('commercial_ambiguity_continuation_baseline_binding')
        baseline_receipt = (baseline.metadata_json or {}).get('receipt')
        validate_continuation_grant(meta.get('grant'),
            project_id=str(project_id), execution_scope_id=binding['execution_scope_id'],
            baseline_workflow_id=str(baseline.id), baseline_receipt=baseline_receipt,
            target_protocol_policy=policy,
            original_authorization_ref=authorization['authorization_ref'],
            actor=meta.get('actor'), reason=meta.get('reason'), snapshots=snapshots)
        matching.append((workflow, meta, baseline_receipt))
    if not matching:
        return None
    # All matching grants were validated. The choice cannot change the frozen
    # baseline or the existing body's consumption identity.
    workflow, meta, baseline_receipt = min(matching, key=lambda item: str(item[0].id))
    draft = await session.get(ChapterDraftVersionModel, draft_id) if draft_id is not None else None
    if (draft is None or not draft.is_current or draft.project_id != project_id
            or draft.chapter_id != chapter_id
            or hashlib.sha256(draft.content_md.encode()).hexdigest() != binding['body_sha256']):
        raise ValueError('commercial_ambiguity_continuation_current_draft_required')
    current = {**binding, 'actor': meta['actor'],
        'reason': 'Fixed-baseline continuation: ' + meta['reason']}
    receipt = build_ambiguity_retirement_receipt(**current, snapshots=snapshots)
    exclusions = validate_ambiguity_retirement_receipt(receipt, **current, snapshots=snapshots)
    identity = uuid5(NAMESPACE_URL, 'bestseller:' + CHILD_SCHEMA + ':' + receipt['identity'])
    session.info['commercial_ambiguity_derived'] = {'id': identity, 'project_id': project_id,
        'chapter_id': chapter_id, 'draft_id': draft.id, 'receipt': receipt,
        'grant_workflow_id': str(workflow.id), 'baseline_workflow_id': meta['baseline_workflow_id'],
        'baseline_sha256': baseline_receipt['baseline_sha256']}
    return exclusions


async def persist_derived_ambiguity_retirement(session, *, expected_input_hash):
    """Commit lineage together with the unchanged normal prepared body cohort."""
    proof = session.info.get('commercial_ambiguity_derived')
    if proof is None:
        return None
    if proof['receipt']['binding']['current_effective_input_hash'] != expected_input_hash:
        raise ValueError('commercial_transition_prepared_input_changed')
    existing = await session.get(WorkflowRunModel, proof['id'])
    meta = {key: proof[key] for key in (
        'receipt', 'grant_workflow_id', 'baseline_workflow_id', 'baseline_sha256')}
    meta['target_draft_id'] = str(proof['draft_id'])
    if existing is not None:
        if (existing.project_id != proof['project_id'] or existing.scope_id != proof['chapter_id']
                or existing.scope_type != 'chapter' or existing.status != 'completed'
                or existing.workflow_type != 'commercial_ambiguity_retirement'
                or existing.metadata_json != meta):
            raise ValueError('commercial_ambiguity_persisted_receipt_changed')
    else:
        session.add(WorkflowRunModel(id=proof['id'], project_id=proof['project_id'],
            workflow_type='commercial_ambiguity_retirement', scope_type='chapter',
            scope_id=proof['chapter_id'], status='completed', current_step='fixed_baseline_continuation',
            requested_by=proof['receipt']['binding']['actor'],
            idempotency_key=proof['receipt']['identity'], metadata_json=meta))
        await session.flush()
    return str(proof['id'])
