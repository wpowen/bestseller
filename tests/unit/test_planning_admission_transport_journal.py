"""Actual SQLite gateway orchestration; quarantine custody has its own suite."""
from copy import deepcopy
import json

import pytest
from test_planning_draft_admission import harness as harness  # noqa: F401
from test_planning_admission_literal_runtime import literal as literal  # noqa: F401
from test_planning_admission_id_runtime import id_review as id_review, assess  # noqa: F401

from bestseller.services import planning_draft_admission as admission
from bestseller.services import planning_admission_id_protocol as ids
from bestseller.services import planning_admission_spent_transport as spent
from bestseller.services.budget import bind_execution_scope
from bestseller.services.planning_output_budget import LITERAL_ID_SOURCE_VERSION

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def state_with_unknown(h):
    async with h.session_factory() as session:
        with bind_execution_scope('audit:admission-fixture'):
            supplied = await admission._input(h.wrap(session), **h.kwargs(),
                commercial_report=h.commercial, check_initial_context=True)
    supplied = admission._input_for_contract(supplied, ids.SCHEMA)
    item = {'status': 'transport_quarantined', 'wire_version': LITERAL_ID_SOURCE_VERSION,
        'transport_quarantine': {'test_custody_adapter': 'not production evidence'}}
    return {'graded_input_hash': admission.content_hash(supplied),
        'hard_admission_policy': ids.POLICY,
        'candidate_hash': admission.content_hash(h.kwargs()['candidate_payload']),
        'wire_version': LITERAL_ID_SOURCE_VERSION, 'attempts': [item], 'status': 'pending'}, supplied


async def test_unknown_slot_consumes_original_first_slot_and_reuses_one_real_native_return(id_review, monkeypatch):
    h = id_review
    state, supplied = await state_with_unknown(h)
    old = deepcopy(state['attempts'][0])
    verified = []
    async def custody(*args, **kwargs):
        verified.append(kwargs['index'])
        assert kwargs['item'] == old and kwargs['supplied'] == supplied
        return 'audit:admission-fixture'
    monkeypatch.setattr(spent, 'verify_quarantined_transport_slot', custody)
    receipt = await assess(h, state)
    assert receipt['draft_eligible'] is True and len(h.sent) == 1
    assert len(state['attempts']) == 2 and state['attempts'][0] == old
    assert 'completion' not in state['attempts'][0]
    feedback = h.logical[0]['protocol_repair']['previous_attempts'][0]
    assert feedback['grade_available'] is False and feedback['native_submission_available'] is False
    assert 'raw_grade' not in feedback and h.logical[0]['input'] == supplied
    assert await assess(h, deepcopy(state)) == receipt
    assert len(h.sent) == 1 and len(verified) >= 4


async def test_invalid_remaining_native_exhausts_without_a_third_slot(id_review, monkeypatch):
    h = id_review
    state, _ = await state_with_unknown(h)
    async def custody(*args, **kwargs): return 'audit:admission-fixture'
    monkeypatch.setattr(spent, 'verify_quarantined_transport_slot', custody)
    h.id_modes = ['bad_id']
    with pytest.raises(admission.PlanningDraftAdmissionProtocolError):
        await assess(h, state)
    assert len(h.sent) == 1 and len(state['attempts']) == 2
    assert state['status'] == 'protocol_exhausted'
    with pytest.raises(admission.PlanningDraftAdmissionProtocolError):
        await assess(h, deepcopy(state))
    assert len(h.sent) == 1


async def test_changed_quarantine_evidence_blocks_before_gateway(id_review, monkeypatch):
    h = id_review
    state, _ = await state_with_unknown(h)
    async def custody(*args, **kwargs): raise ValueError('custody_changed')
    monkeypatch.setattr(spent, 'verify_quarantined_transport_slot', custody)
    with pytest.raises(ValueError, match='custody_changed'):
        await assess(h, state)
    assert h.sent == [] and len(state['attempts']) == 1


async def test_transport_diagnostic_never_constructs_native_or_story_grade(id_review):
    h = id_review
    state, supplied = await state_with_unknown(h)
    digest = state['graded_input_hash']
    original = deepcopy(state)
    data = json.loads(admission._admission_prompt(supplied, digest, state['attempts'], diagnostic_only=True))
    assert data['input'] == supplied and state == original
    feedback = data['protocol_repair']['previous_attempts'][0]
    assert feedback['grade_available'] is False and 'raw_grade' not in feedback
    kwargs = h.kwargs()
    original_op = admission._protocol_operation(kwargs['project'], kwargs['workflow_run_id'], digest, [], 0)
    remaining_op = admission._protocol_operation(kwargs['project'], kwargs['workflow_run_id'], digest, state['attempts'], 1)
    assert remaining_op != original_op
