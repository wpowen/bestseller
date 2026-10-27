"""Explicit compatibility for one exact scoped-capacity plumbing transition.

Original plans, source decisions, approvals, Unknown results and credits are
immutable. This receipt establishes no paid grade and grants no model egress.
All normal current candidate/source/parent/authority checks still execute.
"""
import ast
from copy import deepcopy
import hashlib
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services.prompt_source_policy import content_hash

SCHEMA = 'planning-source-capacity-compatibility.v1'
WORKFLOW = 'planning_source_resolution_compatibility'
NAMES = {'planning_source_resolution.py', 'planning_draft_admission.py',
         'chapter_outline_readiness.py', 'operator_action_approval.py'}
GUARD = """need(content_hash({k:v for k,v in plan.items() if k!='plan_sha256'})==plan['plan_sha256']
     and plan['candidate']==candidate, 'plan_stale')
if plan['policy']!=policy():
    from bestseller.services.planning_source_resolution_compatibility import require_compatible_resolution
    await require_compatible_resolution(session,project=project,resolution=row,plan=plan)
"""
OLD_GUARD = """need(content_hash({k:v for k,v in plan.items() if k!='plan_sha256'})==plan['plan_sha256']
     and plan['policy']==policy() and plan['candidate']==candidate, 'plan_stale')
"""


def need(ok, code):
    if not ok:
        raise ValueError('planning_source_compatibility_' + code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def adapter_hash():
    return sha(Path(__file__).read_bytes())


def _dump(nodes):
    return ast.dump(ast.Module(body=nodes, type_ignores=[]))


def prove_capacity_transition(*, original_policy, original_sources, current_root=None):
    root = Path(current_root) if current_root is not None else Path(__file__).parent
    need(set(original_policy) == NAMES, 'policy_inventory')
    current = {name: sha((root / name).read_bytes()) for name in sorted(NAMES)}
    changed = {name for name in NAMES if current[name] != original_policy[name]}
    need(changed == {'planning_source_resolution.py', 'planning_draft_admission.py'},
         'unrelated_policy_change')
    need(set(original_sources) == changed, 'source_witness_inventory')
    for name in sorted(changed):
        text = original_sources[name]
        need(isinstance(text, str) and sha(text.encode()) == original_policy[name], 'old_source_witness')
        before = ast.parse(text)
        after = ast.parse((root / name).read_text())
        if name == 'planning_source_resolution.py':
            fn = next(n for n in after.body if isinstance(n, ast.AsyncFunctionDef)
                      and n.name == 'verify_resolution')
            expected = ast.parse(GUARD).body
            positions = [i for i in range(len(fn.body) - 1)
                         if _dump(fn.body[i:i + 2]) == _dump(expected)]
            need(len(positions) == 1, 'exact_compatibility_dispatch')
            i = positions[0]
            fn.body[i:i + 2] = ast.parse(OLD_GUARD).body
        else:
            fn = next(n for n in after.body if isinstance(n, ast.FunctionDef)
                      and n.name == '_input_capacity_receipt')
            expected_import = ast.parse(
                'from bestseller.services.finite_book_budget import current_capacity').body[0]
            imports = [n for n in fn.body if ast.dump(n) == ast.dump(expected_import)]
            need(len(imports) == 1, 'exact_capacity_import')
            fn.body.remove(imports[0])
            calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Name) and n.func.id == 'measure_request_capacity']
            need(len(calls) == 1, 'capacity_call_inventory')
            keywords = [k for k in calls[0].keywords if k.arg == 'total_budget']
            need(len(keywords) == 1 and ast.dump(keywords[0].value)
                 == ast.dump(ast.parse('current_capacity()', mode='eval').body), 'exact_capacity_argument')
            calls[0].keywords.remove(keywords[0])
        need(ast.dump(before) == ast.dump(after), 'source_semantics_changed')
    return {'schema': SCHEMA, 'original_policy': deepcopy(original_policy),
            'current_policy': current, 'changed_files': sorted(changed),
            'adapter_sha256': adapter_hash(), 'original_sources_sha256': content_hash(original_sources),
            'model_request_constructors_and_source_decisions_unchanged': True,
            'model_grade_asserted': False, 'automatic_credit_granted': False,
            'provider_authorization_granted': False}


def identity(resolution_id, plan):
    from bestseller.services.planning_source_resolution import policy
    return uuid5(NAMESPACE_URL, SCHEMA + ':' + str(resolution_id) + ':' + content_hash({
        'plan_sha256': plan['plan_sha256'], 'current_policy': policy(), 'adapter_sha256': adapter_hash()}))


def proof_for(*, resolution, plan, original_sources):
    delta = prove_capacity_transition(original_policy=plan['policy'], original_sources=original_sources)
    return {**delta, 'original_resolution_id': str(resolution.id),
            'original_resolution_metadata_sha256': content_hash(resolution.metadata_json),
            'original_plan_sha256': plan['plan_sha256'], 'project_id': str(resolution.project_id),
            'candidate_sha256': content_hash(plan['candidate']),
            'source_payload_sha256': content_hash(plan['source_payload']),
            'review_input_sha256': content_hash(plan['review_input']),
            'parent_state_sha256': plan['parent_state_sha256']}


async def record_capacity_compatibility(session, *, project, resolution_id, candidate,
                                        source_payload, supplied, original_sources, actor,
                                        authorization_ref, explicit_user_approval=False):
    from bestseller.services import planning_source_resolution as service
    from bestseller.services import chapter_outline_readiness as ready
    from bestseller.services.operator_action_approval import (
        record_operator_action_approval, require_operator_action_approval,
    )
    need(explicit_user_approval is True and actor and authorization_ref, 'explicit_authority')
    row = await session.get(WorkflowRunModel, UUID(str(resolution_id)), populate_existing=True)
    need(row is not None and row.project_id == project.id and row.workflow_type == service.PURPOSE
         and row.scope_type == 'project' and row.scope_id == project.id and row.status == 'completed',
         'original_resolution_scope')
    plan = row.metadata_json['plan']
    need(content_hash({k: v for k, v in plan.items() if k != 'plan_sha256'}) == plan['plan_sha256'],
         'original_plan_changed')
    need(candidate == plan['candidate'] and source_payload == plan['source_payload']
         and {**deepcopy(supplied), 'workflow_run_id': plan['parent_workflow_id']} == plan['review_input'],
         'current_inputs_changed')
    await service._validate_plan_parent(session, plan)
    await require_operator_action_approval(session, approval_id=row.metadata_json['approval_id'],
        purpose=service.PURPOSE, binding={'project_id': str(project.id), 'target_id': str(row.id),
        'plan_sha256': plan['plan_sha256']}, capability='resolve_exact_source')
    parent = await session.get(WorkflowRunModel, UUID(plan['parent_workflow_id']), populate_existing=True)
    need(parent is not None and ready._workflow_scope(parent, project.id)
         and content_hash(parent.metadata_json) == plan['parent_state_sha256'], 'parent_changed')
    proof = proof_for(resolution=row, plan=plan, original_sources=original_sources)
    target = identity(row.id, plan)
    binding = {'project_id': str(project.id), 'target_id': str(target),
               'original_resolution_id': str(row.id), 'proof_sha256': content_hash(proof)}
    approval = await record_operator_action_approval(session, purpose=service.PURPOSE,
        binding=binding, capabilities=['resolve_exact_source'], actor=actor,
        authorization_ref=authorization_ref, explicit_user_approval=True)
    data = {'schema': SCHEMA, 'proof': proof, 'binding': binding, 'approval_id': str(approval),
            'original_sources': deepcopy(original_sources)}
    existing = await session.get(WorkflowRunModel, target, populate_existing=True)
    if existing is None:
        session.add(WorkflowRunModel(id=target, project_id=project.id, workflow_type=WORKFLOW,
            scope_type='project', scope_id=project.id, status='completed',
            current_step='exact_capacity_source_compatibility', requested_by=actor[:64], metadata_json=data))
    else:
        need(existing.status == 'completed' and existing.metadata_json == data, 'identity_conflict')
    await session.flush()
    return {'id': str(target), 'proof': proof, 'original_record_unchanged': True}


async def require_compatible_resolution(session, *, project, resolution, plan):
    from bestseller.services.planning_source_resolution import PURPOSE
    from bestseller.services.operator_action_approval import require_operator_action_approval
    target = identity(resolution.id, plan)
    row = await session.get(WorkflowRunModel, target, populate_existing=True)
    need(row is not None and row.project_id == project.id and row.workflow_type == WORKFLOW
         and row.scope_type == 'project' and row.scope_id == project.id and row.status == 'completed',
         'current_receipt_missing')
    data = row.metadata_json
    proof = proof_for(resolution=resolution, plan=plan, original_sources=data['original_sources'])
    binding = {'project_id': str(project.id), 'target_id': str(target),
               'original_resolution_id': str(resolution.id), 'proof_sha256': content_hash(proof)}
    need(data.get('schema') == SCHEMA and data.get('proof') == proof and data.get('binding') == binding,
         'receipt_changed')
    await require_operator_action_approval(session, approval_id=data['approval_id'], purpose=PURPOSE,
                                           binding=binding, capability='resolve_exact_source')
