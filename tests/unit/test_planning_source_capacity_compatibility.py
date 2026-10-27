# ruff: noqa: F811
"""Synthetic old source decision, real SQLite authority and current entry fence."""
import ast
from copy import deepcopy
from pathlib import Path
import textwrap
from uuid import UUID

import pytest

from bestseller.infra.db.models import ProjectModel, WorkflowRunModel
from bestseller.services import planning_source_resolution as resolution
from bestseller.services import planning_source_resolution_compatibility as compatibility
from test_planning_source_resolution import entry_case, prepared, register  # noqa: F401
from test_chapter_outline_readiness import rig  # noqa: F401

pytestmark = [pytest.mark.unit]


def baseline(tmp_path):
    source = Path(resolution.__file__).parent
    current = tmp_path / 'services'
    current.mkdir()
    for name in compatibility.NAMES:
        (current / name).write_bytes((source / name).read_bytes())
    original = {}
    name = 'planning_source_resolution.py'
    text = (current / name).read_text()
    fn = next(n for n in ast.parse(text).body if getattr(n, 'name', None) == 'verify_resolution')
    expected = ast.parse(compatibility.GUARD).body
    index = next(i for i in range(len(fn.body) - 1)
                 if compatibility._dump(fn.body[i:i + 2]) == compatibility._dump(expected))
    lines = text.splitlines(keepends=True)
    a, b = fn.body[index].lineno - 1, fn.body[index + 1].end_lineno
    original[name] = ''.join(lines[:a]) + textwrap.indent(compatibility.OLD_GUARD, '    ') + ''.join(lines[b:])
    name = 'planning_draft_admission.py'
    text = (current / name).read_text()
    assert text.count('    from bestseller.services.finite_book_budget import current_capacity\n') == 1
    original[name] = text.replace('    from bestseller.services.finite_book_budget import current_capacity\n', '').replace(',total_budget=current_capacity()', '')
    policy = {name: compatibility.sha(original[name].encode() if name in original else (current / name).read_bytes())
              for name in compatibility.NAMES}
    return current, original, policy


def test_only_exact_capacity_and_guard_delta_is_accepted(tmp_path):
    root, sources, policy = baseline(tmp_path)
    proof = compatibility.prove_capacity_transition(original_policy=policy, original_sources=sources, current_root=root)
    assert proof['model_request_constructors_and_source_decisions_unchanged']
    assert proof['model_grade_asserted'] is False
    assert proof['provider_authorization_granted'] is False


@pytest.mark.parametrize('changed', [
    'operator_action_approval.py', 'chapter_outline_readiness.py',
    'resolution_semantics', 'admission_semantics', 'original_witness', 'legacy_envelope',
])
def test_unrelated_semantics_or_false_witness_is_rejected(tmp_path, changed):
    root, sources, policy = baseline(tmp_path)
    if changed.endswith('.py'):
        path = root / changed
        path.write_text(path.read_text() + '\n# unrelated policy change\n')
    elif changed == 'resolution_semantics':
        path = root / 'planning_source_resolution.py'
        path.write_text(path.read_text().replace("'approval_id':str(approval)", "'approval_id':str(approval), 'unrelated':True"))
    elif changed == 'admission_semantics':
        path = root / 'planning_draft_admission.py'
        path.write_text(path.read_text().replace('output_tokens: int = 6000', 'output_tokens: int = 6001', 1))
    elif changed == 'original_witness':
        sources['planning_draft_admission.py'] += '\n# not in approved old bytes\n'
    else:
        key = 'planning_source_resolution.py'
        sources[key] = sources[key].replace('candidate={**ready._body(source)', 'candidate={**ready._body(edited)')
        policy[key] = compatibility.sha(sources[key].encode())
    with pytest.raises(ValueError, match='planning_source_compatibility_'):
        compatibility.prove_capacity_transition(original_policy=policy, original_sources=sources, current_root=root)


async def old_registered(c, monkeypatch, policy):
    with monkeypatch.context() as legacy:
        legacy.setattr(resolution, 'policy', lambda: deepcopy(policy))
        plan = await prepared(c)
        rid = await register(c, plan)
    return plan, rid


def kwargs(plan, rid, sources):
    return {'resolution_id': rid, 'candidate': deepcopy(plan['candidate']),
            'source_payload': deepcopy(plan['source_payload']), 'supplied': deepcopy(plan['review_input']),
            'original_sources': sources, 'actor': 'isolated-source-recovery',
            'authorization_ref': 'fixture framework migration only', 'explicit_user_approval': True}


@pytest.mark.asyncio
async def test_actual_entry_requires_derived_authority_and_retains_original(entry_case, monkeypatch, tmp_path):
    c = entry_case
    _, sources, policy = baseline(tmp_path)
    plan, rid = await old_registered(c, monkeypatch, policy)
    paid_before = len(c.sends)
    async with c.sessions() as s:
        p = await s.get(ProjectModel, c.project_id)
        old = await s.get(WorkflowRunModel, UUID(rid))
        original = deepcopy(old.metadata_json)
        with pytest.raises(ValueError, match='current_receipt_missing'):
            await resolution.verify_resolution(s, project=p, resolution_id=rid,
                candidate=plan['candidate'], source_payload=plan['source_payload'], supplied=plan['review_input'])
        result = await compatibility.record_capacity_compatibility(s, project=p, **kwargs(plan, rid, sources))
        repeated = await compatibility.record_capacity_compatibility(s, project=p, **kwargs(plan, rid, sources))
        assert result == repeated
        await s.commit()
    async with c.sessions() as fresh:
        p = await fresh.get(ProjectModel, c.project_id)
        actual = await resolution.verify_resolution(fresh, project=p, resolution_id=rid,
            candidate=plan['candidate'], source_payload=plan['source_payload'], supplied=plan['review_input'])
        assert actual == plan
        old = await fresh.get(WorkflowRunModel, UUID(rid))
        assert old.metadata_json == original
        derived = await fresh.get(WorkflowRunModel, UUID(result['id']))
        assert derived.metadata_json['proof']['original_plan_sha256'] == plan['plan_sha256']
        for key in ('patch_attempts', 'hard_patch_attempts'):
            assert actual['inherited_attempts'][key] == plan['inherited_attempts'][key]
        bad_input = deepcopy(plan['review_input'])
        bad_input['source_authority'] = {'invented': 'author request'}
        with pytest.raises(ValueError, match='current_source_changed'):
            await resolution.verify_resolution(fresh, project=p, resolution_id=rid,
                candidate=plan['candidate'], source_payload=plan['source_payload'], supplied=bad_input)
        derived.metadata_json = {**derived.metadata_json, 'proof': {**derived.metadata_json['proof'], 'automatic_credit_granted': True}}
        await fresh.commit()
    async with c.sessions() as fresh:
        p = await fresh.get(ProjectModel, c.project_id)
        with pytest.raises(ValueError, match='receipt_changed'):
            await resolution.verify_resolution(fresh, project=p, resolution_id=rid,
                candidate=plan['candidate'], source_payload=plan['source_payload'], supplied=plan['review_input'])
    assert len(c.sends) == paid_before


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['candidate', 'supplied', 'source_payload', 'authority', 'parent'])
async def test_derivation_refuses_changed_inputs_or_missing_authority(entry_case, monkeypatch, tmp_path, change):
    c = entry_case
    _, sources, policy = baseline(tmp_path)
    plan, rid = await old_registered(c, monkeypatch, policy)
    args = kwargs(plan, rid, sources)
    if change in ('candidate', 'supplied', 'source_payload'):
        args[change] = {**args[change], 'unknown_authored_clause': 'changed'}
    elif change == 'authority':
        args['explicit_user_approval'] = False
    async with c.sessions() as s:
        p = await s.get(ProjectModel, c.project_id)
        if change == 'parent':
            parent = await s.get(WorkflowRunModel, UUID(plan['parent_workflow_id']))
            parent.metadata_json = {**parent.metadata_json, 'original_history_changed': True}
            await s.flush()
        with pytest.raises(ValueError):
            await compatibility.record_capacity_compatibility(s, project=p, **args)
        await s.rollback()
