"""Real SQLite/physical proof for same-owner recovery, with no provider sends."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import socket
import sys
from uuid import uuid4

import pytest
from sqlalchemy import select, update

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/unit'))
import test_outline_revision_proposal_v2 as base
from test_outline_revision_proposal_v2 import proposal as frozen_proposal, source_case, store  # noqa: F401
from bestseller.infra.db import models as db

from bestseller.services import outline_revision_proposal_v2 as service
isolation_spec = importlib.util.spec_from_file_location('staged_telemetry_isolation', ROOT / 'tests/conftest.py')
isolation = importlib.util.module_from_spec(isolation_spec)
isolation_spec.loader.exec_module(isolation)
isolate_settings_environment = isolation.isolate_settings_environment
pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def real_ledger_shape(monkeypatch):
    from support.database_safety import require_test_engine_url
    require_test_engine_url(__import__('os').environ['BESTSELLER__DATABASE__URL'])
    monkeypatch.setattr(base, 'service', service)
    actual = base.record_completion

    async def record(c, *args, **kwargs):
        final, run = await actual(c, *args, **kwargs)
        assert final.llm_run_id == run.id
        key = run.metadata_json['execution_receipt']['operation_key']
        aid = run.metadata_json['execution_receipt']['attempts'][0]['attempt_id']
        import hashlib
        tkey = hashlib.sha256(f'transport-result.v1\n{c.scope}\n{aid}'.encode()).hexdigest()
        for operation_key in (key, tkey):
            row = await c.session.get(db.ExecutionOperationModel, operation_key)
            value = deepcopy(row.result_json)
            paid = value['result'] if operation_key == tkey else value
            paid['llm_run_id'] = getattr(c, 'wrong_paid_id', None)
            row.result_json = value
        await c.session.flush()
        return final, run  # Only the caller telemetry result carries run.id.

    monkeypatch.setattr(base, 'record_completion', record)
    def no_network(*args, **kwargs):
        raise AssertionError('staged proof forbids network/provider')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    monkeypatch.setattr(socket, 'create_connection', no_network)


@pytest.fixture
async def proposal(frozen_proposal):  # noqa: F811 - borrowed fixture dependency
    c = frozen_proposal
    # The borrowed source-reader fixture has illustrative counters (9/1234)
    # with zero physical attempts. This fresh scratch lifecycle starts at its
    # actual empty ledger, before any request or workflow dispatch exists.
    async with c.factory() as session:
        assert not list(await session.scalars(select(db.ExecutionAttemptModel)))
        budget = await session.get(db.ExecutionBudgetModel, c.scope)
        budget.calls_used = 0
        budget.tokens_charged = 0
        await session.commit()
    c.accounting = True
    return c


async def owner(c):
    async with c.factory() as session:
        return (await session.scalars(select(db.WorkflowRunModel).where(db.WorkflowRunModel.workflow_type == service.WORKFLOW))).one()


async def fail_real_id_guard(c, monkeypatch):
    actual = service._prove
    async def original_id_guard(session, settings, project, identity, state):
        # Reproduce only the historical failing assertion, using real paid
        # custody; this stays valid after the fixed main module is integrated.
        from bestseller.services.foundation_field_recovery import _paid
        run_id = state['candidate_payload']['_meta']['surgical_repair_receipt']['llm_run_id']
        run, _, paid, _ = await _paid(session, settings, project, run_id)
        assert paid.llm_run_id is None
        service._need(paid.llm_run_id == run.id, 'paid_request_owner_or_protocol')
    monkeypatch.setattr(service, '_prove', original_id_guard)
    with pytest.raises(ValueError, match='paid_request_owner_or_protocol'):
        await base.call(c)
    monkeypatch.setattr(service, '_prove', actual)
    row = await owner(c)
    assert row.status == 'failed' and row.metadata_json['candidate_payload']
    return row


async def test_null_paid_id_is_normal_but_telemetry_verified_and_reused(proposal):
    c = proposal
    first = await base.call(c)
    assert await base.call(c) == first
    proof = first['proposal_receipt']['paid_patch_attempts'][0]
    assert proof['provider_return_llm_run_id'] is None
    assert proof['telemetry_row_verified'] is True
    assert proof['llm_run_id']
    assert len(c.patch_calls) == 1


async def test_wrong_nonnull_paid_id_rejected(proposal):
    c = proposal
    c.wrong_paid_id = str(uuid4())
    with pytest.raises(ValueError, match='paid_request_owner_or_protocol'):
        await base.call(c)
    assert len(c.patch_calls) == 1


async def test_failed_retained_candidate_recovery_is_zero_call_append_only_and_idempotent(proposal, monkeypatch):
    c = proposal
    old = await fail_real_id_guard(c, monkeypatch)
    old_history = [{"opaque_prior_event": {"unknown": None, "flag": False}}]
    seeded = {**old.metadata_json, "recovery_history": old_history, "unknown_old_field": {"raw": None}}
    async with c.factory() as session:
        await session.execute(update(db.WorkflowRunModel).where(db.WorkflowRunModel.id == old.id).values(metadata_json=seeded))
        await session.commit()
    old = await owner(c)
    previous = deepcopy(old.metadata_json)
    paths = {key: path.read_bytes() for key, path in c.paths.items()}
    before = await base.protected(c)
    with pytest.raises(ValueError, match='terminal_or_inflight'):
        await base.call(c)
    first = await base.call(c, recover_retained_candidate=True)
    assert await base.call(c, recover_retained_candidate=True) == first
    assert await base.call(c) == first
    row = await owner(c)
    assert row.id == old.id and row.status == 'completed'
    assert row.metadata_json['candidate_payload'] == previous['candidate_payload']
    history = row.metadata_json['recovery_history']
    assert len(history) == 2 and history[:1] == old_history
    assert history[-1]['fullprevious_state'] == previous
    assert history[-1]['original_phase'] == 'terminal_error'
    assert history[-1]['original_error_type'] == 'ValueError'
    assert history[-1]['provider_dispatches'] == 0
    assert row.metadata_json['error_type'] == previous['error_type']
    assert await base.protected(c) == before
    assert {key: path.read_bytes() for key, path in c.paths.items()} == paths
    assert len(c.patch_calls) == 1


@pytest.mark.parametrize('change', ['no_candidate', 'inflight', 'cancelled', 'pause', 'canonical', 'beat', 'scope', 'actor', 'authorization', 'approval'])
async def test_recovery_denies_missing_inflight_control_source_and_authorization(proposal, monkeypatch, change):
    c = proposal
    old = await fail_real_id_guard(c, monkeypatch)
    changes = {'recover_retained_candidate': True}
    async with c.factory() as session:
        state = deepcopy(old.metadata_json)
        if change == 'no_candidate':
            state.pop('candidate_payload')
            await session.execute(update(db.WorkflowRunModel).where(db.WorkflowRunModel.id == old.id).values(metadata_json=state))
        elif change in ('inflight', 'cancelled'):
            if change == 'inflight':
                state['phase'] = 'dispatching'
            await session.execute(update(db.WorkflowRunModel).where(db.WorkflowRunModel.id == old.id).values(status='running' if change == 'inflight' else 'cancelled', metadata_json=state))
        elif change == 'pause':
            await session.execute(update(db.BookProductionControlModel).where(db.BookProductionControlModel.project_id == c.project.id).values(desired_state='pause'))
        elif change in ('canonical', 'beat'):
            target = c.canonical if change == 'canonical' else c.beat
            await session.execute(update(db.PlanningArtifactVersionModel).where(db.PlanningArtifactVersionModel.id == target.id).values(content={**target.content, 'changed': True}))
        elif change == 'scope':
            project = await session.get(db.ProjectModel, c.project.id)
            project.metadata_json = {**project.metadata_json, 'execution_scope_id': 'another-original-scope'}
        elif change == 'actor':
            changes['actor'] = 'different operator'
        elif change == 'authorization':
            changes['authorization_ref'] = 'different approval'
        else:
            changes['explicit_user_approval'] = False
        await session.commit()
    prior = deepcopy((await owner(c)).metadata_json)
    with pytest.raises(ValueError):
        await base.call(c, **changes)
    assert (await owner(c)).metadata_json == prior
    assert len(c.patch_calls) == 1


async def test_recovery_missing_owner_never_dispatches(proposal):
    c = proposal
    with pytest.raises(ValueError, match='recovery_owner_missing'):
        await base.call(c, recover_retained_candidate=True)
    assert not c.patch_calls


async def test_recovery_last_control_guard_blocks_postproof_pause(proposal, monkeypatch):
    c = proposal
    old = await fail_real_id_guard(c, monkeypatch)
    actual = service._prove
    async def pause_after_proof(session, *args, **kwargs):
        receipt = await actual(session, *args, **kwargs)
        await session.execute(update(db.BookProductionControlModel).where(db.BookProductionControlModel.project_id == c.project.id).values(desired_state='pause'))
        await session.commit()
        return receipt
    monkeypatch.setattr(service, '_prove', pause_after_proof)
    with pytest.raises(ValueError):
        await base.call(c, recover_retained_candidate=True)
    row = await owner(c)
    assert row.status == 'failed' and row.metadata_json == old.metadata_json
    assert len(c.patch_calls) == 1


async def test_cancelled_recovery_proof_does_not_rewrite_terminal_state(proposal, monkeypatch):
    import asyncio
    c = proposal
    old = await fail_real_id_guard(c, monkeypatch)
    async def cancel(*args, **kwargs):
        raise asyncio.CancelledError()
    monkeypatch.setattr(service, '_prove', cancel)
    with pytest.raises(asyncio.CancelledError):
        await base.call(c, recover_retained_candidate=True)
    row = await owner(c)
    assert row.status == 'failed' and row.metadata_json == old.metadata_json
    assert len(c.patch_calls) == 1


async def test_existing_completed_receipt_optional_audit_missing_reuses_unchanged(proposal):
    c = proposal
    first = await base.call(c)
    row = await owner(c)
    old = deepcopy(row.metadata_json)
    for paid in old['proposal_receipt']['paid_patch_attempts']:
        paid.pop('provider_return_llm_run_id')
        paid.pop('telemetry_row_verified')
    async with c.factory() as session:
        await session.execute(update(db.WorkflowRunModel).where(db.WorkflowRunModel.id == row.id).values(metadata_json=old))
        await session.commit()
    reused = await base.call(c)
    assert reused['proposal_receipt'] == old['proposal_receipt']
    assert reused['candidate_payload'] == first['candidate_payload']
    assert (await owner(c)).metadata_json == old
    assert len(c.patch_calls) == 1


async def seed_partial_failure(c, monkeypatch):
    """Actual native helper: one settled invalid return, then pre-send capacity denial."""
    import json
    from bestseller.services import llm, planner, planning_surgical_repair as surgical
    from bestseller.services.source_bound_planning_content import SourceBoundPlanningCapacityError
    c.accounting = True
    c.actual_sends = []
    bad = [{"chapter_number": n, "scope": "chapter", "field": "opening_situation",
            "value": f"Observe an explicit weather signal {n}"} for n in range(1, 5)]
    bad.append({"chapter_number": 1, "scope": "scene", "field": "purpose",
        "value": {"story": "Compare the actual station"}})
    c.invalid_raw = json.dumps({"patches": bad}, ensure_ascii=False)
    actual_builder = surgical.build_request

    def capacity_before_second(*args, **kwargs):
        if kwargs.get('protocol_feedback'):
            raise SourceBoundPlanningCapacityError({'fixture': 'actual pre-provider capacity boundary'})
        return actual_builder(*args, **kwargs)

    async def first_return(session, settings, request):
        assert not c.actual_sends
        c.actual_sends.append(deepcopy(request))
        c.session = session
        completion, _ = await base.record_completion(c, request, content=c.invalid_raw)
        return completion

    async def actual_native(session, settings, *, world_spec=None, **kwargs):
        return await surgical.repair(session, settings, **kwargs)

    with monkeypatch.context() as initial:
        initial.setattr(planner, '_repair_outline_commercial_findings_surgically', actual_native)
        initial.setattr(surgical, 'build_request', capacity_before_second)
        initial.setattr(llm, 'complete_text', first_return)
        with pytest.raises(SourceBoundPlanningCapacityError):
            await base.call(c)
    row = await owner(c)
    assert row.status == 'failed' and row.metadata_json.get('candidate_payload') is None
    assert row.metadata_json['error_type'] == 'SourceBoundPlanningCapacityError'
    assert len(c.actual_sends) == 1
    async with c.factory() as session:
        runs = list(await session.scalars(select(db.LlmRunModel).where(db.LlmRunModel.workflow_run_id == row.id)))
        assert len(runs) == 1
        c.first_run_id = runs[0].id
    return row


def remaining_return(c, monkeypatch, *, invalid=False):
    import json
    from bestseller.services import llm
    async def final(session, settings, request):
        assert len(c.actual_sends) == 1  # A third protocol send is never permitted.
        c.actual_sends.append(deepcopy(request))
        assert request.workflow_run_id == (await owner(c)).id
        assert llm._semantic_operation_namespace.get() == (f'outline-revision-proposal:{request.workflow_run_id}', 0)
        row = await owner(c)
        assert row.status == 'running' and row.metadata_json['phase'] == 'dispatching_retained_protocol'
        history = row.metadata_json['protocol_resume_history'][-1]
        assert history['remaining_protocol_slots'] == history['original_spent_protocol_slots'] == 1
        assert history['fullprevious_state'].get('candidate_payload') is None
        from bestseller.services import planning_surgical_transport as transport
        logical = transport.decode_source_complete_surgical_prompts(system_prompt=request.system_prompt,
            user_prompt=request.user_prompt,max_chars=1_000_000)
        feedback = logical['directives']['protocol_feedback']
        assert len(feedback) == 1 and feedback[0]['attempt'] == 1
        assert feedback[0]['llm_run_id'] == str(c.first_run_id)
        assert feedback[0]['rejected_response'] == c.invalid_raw
        assert feedback[0]['error'] == 'Outline surgical repair returned invalid patch 5: scene_number must be an integer.'
        assert feedback[0]['structural_errors'] == [{'patch_indices': [5], 'error': 'scene_number must be an integer.'}]
        assert logical['candidate_payload'] == row.metadata_json['source_context']['compiler_candidate']
        raw = '{"patches":[]}' if invalid else json.dumps({'patches': [{
            'chapter_number': 1, 'scope': 'chapter', 'field': 'opening_situation',
            'value': 'Record a verifiable change at the weather station'}]})
        c.session = session
        completion, _ = await base.record_completion(c, request, content=raw)
        return completion
    monkeypatch.setattr(llm, 'complete_text', final)


async def test_partial_protocol_resume_reuses_first_and_only_spends_original_last_slot(proposal, monkeypatch):
    c = proposal
    old = await seed_partial_failure(c, monkeypatch)
    raw_files = {key: path.read_bytes() for key, path in c.paths.items()}
    with pytest.raises(ValueError, match='terminal_or_inflight'):
        await base.call(c)
    remaining_return(c, monkeypatch)
    result = await base.call(c, resume_retained_protocol=True)
    assert await base.call(c, resume_retained_protocol=True) == result
    assert await base.call(c) == result
    row = await owner(c)
    assert row.id == old.id and row.status == 'completed'
    assert len(c.actual_sends) == 2
    assert row.metadata_json['protocol_resume_history'][0]['fullprevious_state'] == old.metadata_json
    assert row.metadata_json['retained_protocol_proof']['exact_remaining_scope_attempts_verified'] is True
    assert row.metadata_json['candidate_payload']['_meta']['surgical_repair_receipt']['protocol_attempts'][0]['llm_run_id'] == str(c.first_run_id)
    assert result['proposal_receipt']['grade_asserted'] is False
    for key, data in raw_files.items():
        assert c.paths[key].read_bytes() == data
    async with c.factory() as session:
        runs = list(await session.scalars(select(db.LlmRunModel).where(db.LlmRunModel.workflow_run_id == old.id)))
        assert len(runs) == 2 and c.first_run_id in {run.id for run in runs}
        budget = await session.get(db.ExecutionBudgetModel, c.scope)
        assert budget.calls_used == 2 and budget.tokens_charged == 36


@pytest.mark.parametrize('change', ['no_baseline', 'wrong_error', 'candidate', 'inflight', 'cancelled',
    'paused', 'canonical', 'wire', 'fee', 'raw', 'paid_id', 'extra_attempt', 'orphan_transport', 'two_runs'])
async def test_partial_protocol_rejects_unproven_or_extra_physical_activity_before_send(proposal, monkeypatch, change):
    import hashlib
    c = proposal
    old = await seed_partial_failure(c, monkeypatch)
    async with c.factory() as session:
        state = deepcopy(old.metadata_json)
        if change == 'no_baseline':
            state.pop('execution_before_dispatch')
        elif change == 'wrong_error':
            state['error_type'] = 'ValueError'
        elif change == 'candidate':
            state['candidate_payload'] = {'untrusted': 'must use candidate recovery separately'}
        elif change == 'inflight':
            state['phase'] = 'dispatching_retained_protocol'
        if change in ('no_baseline', 'wrong_error', 'candidate', 'inflight', 'cancelled'):
            await session.execute(update(db.WorkflowRunModel).where(db.WorkflowRunModel.id == old.id).values(
                metadata_json=state, status='running' if change == 'inflight' else 'cancelled' if change == 'cancelled' else 'failed'))
        elif change == 'paused':
            await session.execute(update(db.BookProductionControlModel).where(db.BookProductionControlModel.project_id == c.project.id).values(desired_state='pause'))
        elif change == 'canonical':
            await session.execute(update(db.PlanningArtifactVersionModel).where(db.PlanningArtifactVersionModel.id == c.canonical.id).values(content={'changed': True}))
        elif change == 'wire':
            c.settings.llm.planner.max_tokens -= 1
        elif change == 'fee':
            budget = await session.get(db.ExecutionBudgetModel,c.scope)
            budget.tokens_charged += 1
        elif change == 'raw':
            path = c.paths[f'{c.first_run_id}:response']
            path.write_text('{}')
        elif change == 'paid_id':
            run = await session.get(db.LlmRunModel, c.first_run_id)
            aid = run.metadata_json['execution_receipt']['attempts'][0]['attempt_id']
            key = hashlib.sha256(f'transport-result.v1\n{c.scope}\n{aid}'.encode()).hexdigest()
            transport = await session.get(db.ExecutionOperationModel,key)
            value = deepcopy(transport.result_json)
            value['result']['llm_run_id'] = str(uuid4())
            transport.result_json = value
        elif change == 'extra_attempt':
            first = (await session.scalars(select(db.ExecutionAttemptModel).where(db.ExecutionAttemptModel.scope_id == c.scope))).one()
            op = await session.get(db.ExecutionOperationModel,first.operation_key)
            key = uuid4().hex
            session.add(db.ExecutionOperationModel(key=key,scope_id=c.scope,input_hash='f'*64,status='completed',owner='different',lease_until=op.lease_until,result_json=None))
            session.add(db.ExecutionAttemptModel(id=uuid4().hex,operation_key=key,scope_id=c.scope,model_name=first.model_name,
                input_hash='f'*64,reserved_tokens=3,actual_tokens=3,status='completed',created_at=first.created_at))
            budget = await session.get(db.ExecutionBudgetModel,c.scope)
            budget.calls_used += 1; budget.tokens_charged += 3
        elif change == 'orphan_transport':
            run = await session.get(db.LlmRunModel,c.first_run_id)
            op = await session.get(db.ExecutionOperationModel,run.metadata_json['execution_receipt']['operation_key'])
            session.add(db.ExecutionOperationModel(key=uuid4().hex,scope_id=c.scope,input_hash=op.input_hash,status='transport_result',
                owner=op.owner,lease_until=op.lease_until,result_json={'schema':'transport-result.v1','result':deepcopy(op.result_json)}))
        elif change == 'two_runs':
            run = await session.get(db.LlmRunModel,c.first_run_id)
            values = {attr.key: deepcopy(getattr(run,attr.key)) for attr in __import__('sqlalchemy').inspect(db.LlmRunModel).column_attrs}
            values['id'] = uuid4()
            session.add(db.LlmRunModel(**values))
        await session.commit()
    before = deepcopy((await owner(c)).metadata_json)
    remaining_return(c, monkeypatch)
    with pytest.raises((ValueError, FileNotFoundError)):
        await base.call(c, resume_retained_protocol=True)
    assert (await owner(c)).metadata_json == before
    assert len(c.actual_sends) == 1


async def test_partial_last_protocol_failure_never_reopens(proposal, monkeypatch):
    c = proposal
    await seed_partial_failure(c, monkeypatch)
    remaining_return(c, monkeypatch, invalid=True)
    with pytest.raises(base.planner.PlannerFallbackError):
        await base.call(c, resume_retained_protocol=True)
    with pytest.raises(ValueError):
        await base.call(c, resume_retained_protocol=True)
    assert len(c.actual_sends) == 2


async def test_owner_only_cancel_after_retained_checkpoint_blocks_model(proposal, monkeypatch):
    from bestseller.services import pipelines
    c = proposal
    old = await seed_partial_failure(c, monkeypatch)
    actual_commit = pipelines._checkpoint_commit
    cancelled = False
    async def cancel_after_checkpoint(session):
        nonlocal cancelled
        await actual_commit(session)
        async with c.factory() as other:
            row = await other.get(db.WorkflowRunModel, old.id)
            if not cancelled and row.current_step == 'exact_retained_protocol_slot_v2':
                cancelled = True
                await other.execute(update(db.WorkflowRunModel).where(db.WorkflowRunModel.id == old.id).values(status='cancelled'))
                await other.commit()
    monkeypatch.setattr(pipelines, '_checkpoint_commit', cancel_after_checkpoint)
    remaining_return(c, monkeypatch)
    with pytest.raises(ValueError, match='dispatch_owner_changed'):
        await base.call(c, resume_retained_protocol=True)
    assert cancelled and len(c.actual_sends) == 1
    assert (await owner(c)).status == 'cancelled'
